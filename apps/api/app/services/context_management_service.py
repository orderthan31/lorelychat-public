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
DEFAULT_LOW_WATERMARK = 0.65
DEFAULT_COMPRESSION_BATCH_MESSAGE_LIMIT = 48
SYNC_TURN_GROUP_PREFIX = "sync_turn:"
SYNC_TURN_GROUP_V2_PREFIX = "sync_turn:v2:"

PressureStatus = Literal["low", "high", "hard"]
CapacitySource = Literal["model_metadata", "conservative_fallback"]
MessageGroup = tuple[Message, ...]
TokenEstimator = Callable[[Message], int]


def sync_turn_group_id(source_message_id: str, attempt_id: str) -> str:
    source_id = str(source_message_id or "")
    attempt = str(attempt_id or "")
    if not source_id:
        raise ValueError("source_message_id is required")
    if not attempt or ":" in attempt:
        raise ValueError("attempt_id must be non-empty and colon-free")
    return f"{SYNC_TURN_GROUP_V2_PREFIX}{attempt}:{source_id}"


def sync_turn_source_message_id(turn_group_id: str | None) -> str | None:
    value = str(turn_group_id or "")
    if value.startswith(SYNC_TURN_GROUP_V2_PREFIX):
        payload = value[len(SYNC_TURN_GROUP_V2_PREFIX):]
        attempt_id, separator, source_id = payload.partition(":")
        if not separator or not attempt_id or not source_id:
            return None
        return source_id
    if not value.startswith(SYNC_TURN_GROUP_PREFIX):
        return None
    source_message_id = value[len(SYNC_TURN_GROUP_PREFIX):]
    version_marker = source_message_id.partition(":")[0]
    if version_marker.startswith("v") and version_marker[1:].isdigit():
        return None
    return source_message_id or None


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


@dataclass(frozen=True)
class AutomaticContextPlan:
    """One authoritative pressure/coverage and complete-turn fold plan."""

    pressure: ContextPressureReport
    raw_tail_token_budget: int
    fold_message_ids: tuple[str, ...]
    raw_tail_message_ids: tuple[str, ...]
    fold_group_count: int
    oversized_indivisible_turn: bool

    @property
    def has_foldable_backlog(self) -> bool:
        return bool(self.fold_message_ids)


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


def resolve_safe_model_context_capacity(
    primary_model_option: ModelOption | None,
    fallback_model_option: ModelOption | None = None,
    *,
    room_prompt_budget_tokens: int | None = None,
    mandatory_reserve_tokens: int = DEFAULT_MANDATORY_RESERVE_TOKENS,
) -> ContextCapacity:
    """Use the narrowest usable input capacity across reachable generation routes."""

    options = [primary_model_option]
    if fallback_model_option is not None:
        options.append(fallback_model_option)
    capacities = [
        resolve_model_context_capacity(
            option,
            room_prompt_budget_tokens=room_prompt_budget_tokens,
            completion_reserve_tokens=getattr(option, "max_output_tokens", None),
            mandatory_reserve_tokens=mandatory_reserve_tokens,
        )
        for option in options
    ]
    return min(capacities, key=lambda candidate: candidate.working_input_tokens)


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
    """Group ordered messages into contiguous dependency-closed turn ranges.

    Authoritative job-to-source edges may span messages appended while generation
    was in flight.  The whole interval from source through reply is therefore one
    indivisible group, including intervening messages.  Overlapping intervals are
    merged transitively so compression never advances across one side of a turn.
    Unknown legacy jobs retain the conservative contiguous-run fallback.
    """

    ordered = list(messages)
    if not ordered:
        return []

    index_by_id = {message.id: index for index, message in enumerate(ordered)}
    interval_end_by_start: dict[int, int] = {}
    authoritative = dict(job_source_message_ids or {})
    for message in ordered:
        job_id = message.generation_job_id
        source_id = sync_turn_source_message_id(job_id)
        if source_id and source_id in index_by_id:
            authoritative.setdefault(str(job_id), source_id)

    for reply_index, message in enumerate(ordered):
        job_id = message.generation_job_id
        if not job_id:
            continue
        source_id = authoritative.get(job_id)
        source_index = index_by_id.get(source_id) if source_id else None
        if source_index is None:
            continue
        start = min(source_index, reply_index)
        end = max(source_index, reply_index)
        interval_end_by_start[start] = max(interval_end_by_start.get(start, start), end)

    index = 0
    while index < len(ordered):
        message = ordered[index]
        job_id = message.generation_job_id
        if not job_id or job_id in authoritative:
            index += 1
            continue
        run_end = index
        while (
            run_end + 1 < len(ordered)
            and ordered[run_end + 1].generation_job_id == job_id
        ):
            run_end += 1
        source_index = index - 1
        if source_index >= 0 and ordered[source_index].generation_job_id is None:
            interval_end_by_start[source_index] = max(
                interval_end_by_start.get(source_index, source_index),
                run_end,
            )
        elif run_end > index:
            interval_end_by_start[index] = run_end
        index = run_end + 1

    groups: list[MessageGroup] = []
    start = 0
    while start < len(ordered):
        end = max(start, interval_end_by_start.get(start, start))
        cursor = start
        while cursor <= end:
            end = max(end, interval_end_by_start.get(cursor, cursor))
            cursor += 1
        groups.append(tuple(ordered[start:end + 1]))
        start = end + 1
    return groups


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


def build_automatic_context_plan(
    *,
    capacity: ContextCapacity,
    mandatory_prompt_tokens: int,
    messages: Sequence[Message],
    job_source_message_ids: Mapping[str, str] | None = None,
    token_estimator: TokenEstimator = estimate_visible_message_tokens,
    high_watermark: float = DEFAULT_HIGH_WATERMARK,
    low_watermark: float = DEFAULT_LOW_WATERMARK,
    batch_message_limit: int = DEFAULT_COMPRESSION_BATCH_MESSAGE_LIMIT,
) -> AutomaticContextPlan:
    """Plan automatic compression without splitting a source/reply turn.

    Pressure is measured against the full prospective suffix.  Once pressure is
    high, the protected suffix is sized to the low watermark so a successful
    fold creates useful headroom instead of immediately retriggering.
    """

    pressure = estimate_context_pressure(
        capacity=capacity,
        mandatory_prompt_tokens=mandatory_prompt_tokens,
        messages=messages,
        job_source_message_ids=job_source_message_ids,
        token_estimator=token_estimator,
        high_watermark=high_watermark,
    )
    groups = group_complete_turns(messages, job_source_message_ids=job_source_message_ids)
    low_ratio = max(0.0, min(float(low_watermark), float(high_watermark)))
    raw_tail_budget = max(
        0,
        int(capacity.working_input_tokens * low_ratio) - max(0, int(mandatory_prompt_tokens)),
    )
    raw_tail_groups = select_raw_tail_groups(
        groups,
        token_budget=raw_tail_budget,
        token_estimator=token_estimator,
    )
    foldable_group_count = max(0, len(groups) - len(raw_tail_groups))
    limit = max(1, int(batch_message_limit))
    batch_groups: list[MessageGroup] = []
    batch_count = 0
    for group in groups[:foldable_group_count]:
        # The first complete turn is indivisible even when it alone exceeds 48.
        if batch_groups and batch_count + len(group) > limit:
            break
        batch_groups.append(tuple(group))
        batch_count += len(group)
        if batch_count >= limit:
            break

    fold_messages = tuple(message for group in batch_groups for message in group)
    consumed_group_count = len(batch_groups)
    raw_messages = tuple(
        message
        for group in groups[consumed_group_count:]
        for message in group
    )
    newest_group_tokens = (
        _group_token_cost(groups[-1], token_estimator)
        if groups else 0
    )
    return AutomaticContextPlan(
        pressure=pressure,
        raw_tail_token_budget=raw_tail_budget,
        fold_message_ids=tuple(message.id for message in fold_messages),
        raw_tail_message_ids=tuple(message.id for message in raw_messages),
        fold_group_count=consumed_group_count,
        oversized_indivisible_turn=bool(groups and newest_group_tokens > raw_tail_budget),
    )
