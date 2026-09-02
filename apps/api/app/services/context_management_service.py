from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from app.db.models import Message, ModelOption
from app.engine.prompt_harness import approx_tokens

DEFAULT_CONTEXT_WINDOW_TOKENS = 16_384
DEFAULT_MAX_OUTPUT_TOKENS = 2_048
DEFAULT_MANDATORY_RESERVE_TOKENS = 1_024
DEFAULT_HIGH_WATERMARK = 0.82

PressureStatus = Literal["low", "high", "hard"]
CapacitySource = Literal["model_metadata", "conservative_fallback"]
MessageGroup = tuple[Message, ...]
TokenEstimator = Callable[[Message], int]


@dataclass(frozen=True)
class ContextCapacity:
    model_option_key: str | None
    context_window_tokens: int
    max_output_tokens: int
    completion_reserve_tokens: int
    mandatory_reserve_tokens: int
    available_input_tokens: int
    working_input_tokens: int
    source: CapacitySource
    used_fallback: bool


@dataclass(frozen=True)
class ContextPressureReport:
    status: PressureStatus
    capacity_source: CapacitySource
    used_fallback: bool
    working_input_tokens: int
    mandatory_prompt_tokens: int
    raw_message_tokens: int
    projected_input_tokens: int
    utilization_ratio: float
    total_group_count: int
    selected_group_count: int
    backlog_group_count: int
    total_message_count: int
    selected_message_count: int
    backlog_message_count: int
    selected_message_ids: tuple[str, ...]


def _positive_int(value: int | None, fallback: int) -> int:
    if isinstance(value, bool) or value is None or value <= 0:
        return fallback
    return int(value)


def resolve_model_context_capacity(
    model_option: ModelOption | None,
    *,
    room_prompt_budget_tokens: int | None = None,
    completion_reserve_tokens: int | None = None,
    mandatory_reserve_tokens: int = DEFAULT_MANDATORY_RESERVE_TOKENS,
) -> ContextCapacity:
    """Resolve a conservative input budget without guessing a provider payload.

    Unknown model metadata deliberately falls back to a small context window. A
    product prompt budget may lower the usable input budget, but can never raise
    it above the model-derived capacity.
    """

    metadata_context = model_option.context_window_tokens if model_option else None
    metadata_output = model_option.max_output_tokens if model_option else None
    context_window = _positive_int(metadata_context, DEFAULT_CONTEXT_WINDOW_TOKENS)
    max_output = _positive_int(metadata_output, DEFAULT_MAX_OUTPUT_TOKENS)
    mandatory_reserve = max(0, int(mandatory_reserve_tokens or 0))
    requested_completion = _positive_int(completion_reserve_tokens, max_output)
    completion_reserve = min(requested_completion, max(1, context_window - 1))
    available_input = max(1, context_window - completion_reserve - mandatory_reserve)

    if room_prompt_budget_tokens is None or room_prompt_budget_tokens <= 0:
        working_input = available_input
    else:
        working_input = min(available_input, int(room_prompt_budget_tokens))

    has_context_metadata = bool(metadata_context and metadata_context > 0)
    has_output_metadata = bool(metadata_output and metadata_output > 0)
    used_fallback = not (has_context_metadata and has_output_metadata)
    source: CapacitySource = "model_metadata" if has_context_metadata else "conservative_fallback"

    return ContextCapacity(
        model_option_key=model_option.key if model_option else None,
        context_window_tokens=context_window,
        max_output_tokens=max_output,
        completion_reserve_tokens=completion_reserve,
        mandatory_reserve_tokens=mandatory_reserve,
        available_input_tokens=available_input,
        working_input_tokens=working_input,
        source=source,
        used_fallback=used_fallback,
    )


def estimate_visible_message_tokens(message: Message) -> int:
    """Estimate only fields that can be rendered into the RP prompt.

    Metadata/provider payloads are intentionally excluded so diagnostics cannot
    accidentally retain secrets or private provider transport details.
    """

    def compact(value: str | None, limit: int) -> str:
        text = " ".join((value or "").split())
        if len(text) <= limit:
            return text
        return text[: max(0, limit - 1)].rstrip() + "…"

    if message.speaker_type == "system":
        return max(1, approx_tokens(f"system:scene_direction | directive={compact(message.content, 220)}"))

    parts = [f"{message.speaker_type}:{message.speaker_id}"]
    if message.action:
        parts.append(f"action={compact(message.action, 110)}")
    if message.thought:
        parts.append(f"thought={compact(message.thought, 70)}")
    parts.append(f"dialogue={compact(message.content, 240)}")
    return max(1, approx_tokens(" | ".join(parts)))


def group_complete_turns(
    messages: Sequence[Message],
    *,
    job_source_message_ids: Mapping[str, str] | None = None,
) -> list[MessageGroup]:
    """Group ordered source messages and their contiguous generated bubbles.

    When job-to-source metadata is available it is authoritative. Without it,
    the current persisted ordering is used conservatively: a contiguous generated
    job attaches only to the immediately preceding untagged source group.
    """

    groups: list[list[Message]] = []
    active_job_id: str | None = None
    active_source_id: str | None = None
    source_job_ids = {
        source_message_id: job_id
        for job_id, source_message_id in (job_source_message_ids or {}).items()
    }

    for message in messages:
        source_for_job_id = source_job_ids.get(message.id)
        if source_for_job_id:
            groups.append([message])
            active_job_id = source_for_job_id
            active_source_id = message.id
            continue

        job_id = message.generation_job_id
        if not job_id:
            groups.append([message])
            active_job_id = None
            active_source_id = message.id
            continue

        expected_source_id = job_source_message_ids.get(job_id) if job_source_message_ids else None
        may_attach_to_source = bool(
            groups
            and active_source_id
            and (expected_source_id is None or expected_source_id == active_source_id)
            and (active_job_id is None or active_job_id == job_id)
        )
        may_attach_to_orphan_job = bool(
            groups
            and active_source_id is None
            and active_job_id == job_id
        )

        if may_attach_to_source or may_attach_to_orphan_job:
            groups[-1].append(message)
        else:
            groups.append([message])
            active_source_id = None
        active_job_id = job_id

    return [tuple(group) for group in groups]


def _group_token_cost(group: Sequence[Message], token_estimator: TokenEstimator) -> int:
    return sum(max(0, int(token_estimator(message))) for message in group)


def select_raw_tail_groups(
    groups: Sequence[Sequence[Message]],
    *,
    token_budget: int,
    token_estimator: TokenEstimator = estimate_visible_message_tokens,
) -> list[MessageGroup]:
    """Select a contiguous newest suffix without splitting a complete turn.

    The newest indivisible group is retained even when it alone exceeds budget;
    callers surface that condition as hard pressure instead of silently dropping
    the current turn.
    """

    if not groups:
        return []

    budget = max(0, int(token_budget))
    selected_reversed: list[MessageGroup] = []
    used_tokens = 0
    for raw_group in reversed(groups):
        group = tuple(raw_group)
        group_tokens = _group_token_cost(group, token_estimator)
        if selected_reversed and used_tokens + group_tokens > budget:
            break
        if not selected_reversed or used_tokens + group_tokens <= budget:
            selected_reversed.append(group)
            used_tokens += group_tokens
        else:
            break
    return list(reversed(selected_reversed))


def estimate_context_pressure(
    *,
    capacity: ContextCapacity,
    mandatory_prompt_tokens: int,
    messages: Sequence[Message],
    job_source_message_ids: Mapping[str, str] | None = None,
    token_estimator: TokenEstimator = estimate_visible_message_tokens,
    high_watermark: float = DEFAULT_HIGH_WATERMARK,
) -> ContextPressureReport:
    groups = group_complete_turns(messages, job_source_message_ids=job_source_message_ids)
    mandatory_tokens = max(0, int(mandatory_prompt_tokens))
    group_costs = [_group_token_cost(group, token_estimator) for group in groups]
    raw_tokens = sum(group_costs)
    projected_tokens = mandatory_tokens + raw_tokens
    working_tokens = max(1, capacity.working_input_tokens)
    raw_tail_budget = max(0, working_tokens - mandatory_tokens)
    selected_groups = select_raw_tail_groups(
        groups,
        token_budget=raw_tail_budget,
        token_estimator=token_estimator,
    )

    if projected_tokens > working_tokens:
        status: PressureStatus = "hard"
    elif projected_tokens >= working_tokens * max(0.0, min(1.0, high_watermark)):
        status = "high"
    else:
        status = "low"

    selected_messages = tuple(message for group in selected_groups for message in group)
    total_message_count = sum(len(group) for group in groups)
    selected_message_count = len(selected_messages)

    return ContextPressureReport(
        status=status,
        capacity_source=capacity.source,
        used_fallback=capacity.used_fallback,
        working_input_tokens=working_tokens,
        mandatory_prompt_tokens=mandatory_tokens,
        raw_message_tokens=raw_tokens,
        projected_input_tokens=projected_tokens,
        utilization_ratio=round(projected_tokens / working_tokens, 4),
        total_group_count=len(groups),
        selected_group_count=len(selected_groups),
        backlog_group_count=max(0, len(groups) - len(selected_groups)),
        total_message_count=total_message_count,
        selected_message_count=selected_message_count,
        backlog_message_count=max(0, total_message_count - selected_message_count),
        selected_message_ids=tuple(message.id for message in selected_messages),
    )
