"""Conversation compression: prompt orchestration, source boundaries and atomic result CAS."""

import json
import logging
import re
from collections.abc import Callable, Mapping
from typing import Any
from datetime import datetime, timezone
from app.engine.llm_client import LLMClient, LLMUnavailableError, chat_with_optional_conversation_id
from app.engine import prompts, free_body_protocol as free_body
from sqlmodel import Session, select
from sqlalchemy import update as sa_update
from app.db.models import MessageGenerationJob, PostCommitTask, ConversationRelationshipState, CharacterMemory, Message, SceneState
from app.services import genre_domain_service, system_prompt_service
from app.services.context_management_service import AutomaticContextPlan, ContextCapacity, build_automatic_context_plan, group_complete_turns, select_raw_tail_groups
from app.services.runtime_settings_service import DEFAULT_COMPRESSION_STRATEGY
from app.services.conversation_memory_policy import (
    MAX_SCENE_MEMORY_CHARS,
    clamp_state_value,
    compact_block_text,
    compact_text,
    contains_rank_ledger_claim,
    parse_json_object_from_text,
    scene_summary_copies_recent_raw_text,
    strip_rank_claims_from_scene_summary,
)
from app.services.conversation_queries import (
    COMMON_ROOM_MEMORY_CHARACTER_ID,
    MAX_CONTEXT_MEMORY_SCAN_ITEMS,
    get_conversation,
    get_participants,
    list_messages,
    list_visible_durable_memories_for_compression,
)
from app.services.genre_domain_service import (
    normalize_conversation_genre,
    normalize_genre_mode,
)


MAX_SCENE_MEMORY_BULLETS = 18


MAX_SCENE_MEMORY_BULLET_CHARS = 180


logger = logging.getLogger(__name__)


def compression_error_summary(exc: BaseException) -> str:
    text = compact_text(str(exc) or type(exc).__name__, 500)
    text = re.sub(r"key=[^\s&]+", "key=[REDACTED]", text, flags=re.IGNORECASE)
    text = re.sub(r"API key[^.;\n]*", "API key error", text, flags=re.IGNORECASE)
    return compact_text(f"{type(exc).__name__}: {text}", 500)


SCENE_SUMMARY_RAW_MARKER_RE = re.compile(
    r"(dialogue=|action=|emotion=|thought=|private=|stance=|speaker_id=|speaker_type=|directive=|counterpart message:|character reply:)",
    re.IGNORECASE,
)


COMPRESSION_RECENT_MESSAGE_LIMIT = 12


COMPRESSION_BATCH_MESSAGE_LIMIT = 48


def generation_job_source_message_ids(
    session: Session,
    conversation_id: str,
    *,
    extra: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Return authoritative generation-job -> incoming-message ownership."""

    jobs = session.exec(
        select(MessageGenerationJob).where(
            MessageGenerationJob.conversation_id == conversation_id
        )
    ).all()
    mapping = {
        job.id: job.incoming_message_id
        for job in jobs
        if job.id and job.incoming_message_id
    }
    mapping.update(extra or {})
    return mapping


def automatic_context_plan(
    scene_state: SceneState,
    messages: list[Message],
    *,
    capacity: ContextCapacity,
    mandatory_prompt_tokens: int,
    job_source_message_ids: Mapping[str, str] | None = None,
    token_estimator: Callable[[Message], int] | None = None,
    history_token_estimator: Callable[[list[Message]], int] | None = None,
) -> AutomaticContextPlan:
    suffix = messages_after_compression_boundary(scene_state, messages)
    return build_automatic_context_plan(
        capacity=capacity,
        mandatory_prompt_tokens=mandatory_prompt_tokens,
        messages=suffix,
        job_source_message_ids=job_source_message_ids,
        token_estimator=token_estimator or automatic_context_message_tokens,
        batch_message_limit=COMPRESSION_BATCH_MESSAGE_LIMIT,
        history_token_estimator=history_token_estimator,
    )


def automatic_context_history_token_estimator(
    character_names: Mapping[str, str] | None = None,
    *, history_serialization: str = "transcript", accounting=None,
    excluded_message_ids: frozenset[str] = frozenset(),
) -> Callable[[list[Message]], int]:
    """Linear serialized-history cost, separate from advisory per-row weights.

    Without an envelope this counts the standalone public join, useful for
    legacy callers that supply their own fixed prompt cost. Production callers
    pass the compiled envelope to include its header and whole-message rounding.
    """
    from app.engine.harness_comparison import HistoryAccounting
    envelope = accounting or HistoryAccounting()
    names = dict(character_names or {})

    def estimate(messages: list[Message]) -> int:
        rows = [prompts.format_message_for_context(message, include_thought=False,
                character_names=names, compact=False)
                for message in messages if message.id not in excluded_message_ids]
        return envelope.tokens(rows, history_serialization=history_serialization)

    return estimate


def automatic_context_message_tokens(
    message: Message,
    *,
    character_names: Mapping[str, str] | None = None,
    history_serialization: str = "transcript",
) -> int:
    """Advisory row weight for complete-group fold selection, not request cost."""

    return max(
        1,
        prompts.approx_tokens(
            prompts.format_message_for_context(
                message,
                include_thought=False,
                character_names=dict(character_names or {}),
                compact=False,
            )
        ) + (4 if history_serialization == "native" else 1),
    )


def automatic_context_message_token_estimator(
    character_names: Mapping[str, str] | None = None,
    *, history_serialization: str = "transcript",
) -> Callable[[Message], int]:
    names = dict(character_names or {})

    def estimate(message: Message) -> int:
        return automatic_context_message_tokens(message, character_names=names,
                                                history_serialization=history_serialization)

    return estimate


def messages_after_compression_boundary(
    scene_state: SceneState,
    messages: list[Message],
) -> list[Message]:
    """Return the contiguous raw suffix after the persisted summary boundary.

    ``last_compression_source_message_id`` means the last message actually folded
    into ``scene_state.summary``. A missing persisted boundary starts at the
    beginning; a dangling boundary is rejected instead of silently skipping an
    unknown section of history.
    """
    boundary_id = scene_state.last_compression_source_message_id
    if not boundary_id:
        return list(messages)
    for index, message in enumerate(messages):
        if message.id == boundary_id:
            return list(messages[index + 1:])
    raise ValueError(f"compression boundary message is missing: {boundary_id}")


def compression_raw_tail_start(messages: list[Message], *, raw_tail_limit: int = COMPRESSION_RECENT_MESSAGE_LIMIT) -> int:
    """Choose a raw-tail boundary without splitting a generated reply batch."""
    if not messages:
        return 0
    start = max(0, len(messages) - max(1, int(raw_tail_limit)))
    if start <= 0:
        return 0
    job_id = messages[start].generation_job_id
    if job_id:
        while start > 0 and messages[start - 1].generation_job_id == job_id:
            start -= 1
        # Generated replies follow their source message. Keep that source with
        # the raw batch as well, even though it has no generation_job_id.
        if start > 0 and messages[start - 1].generation_job_id is None:
            start -= 1
    return start


def select_incremental_compression_batch(
    scene_state: SceneState,
    messages: list[Message],
    *,
    raw_tail_limit: int = COMPRESSION_RECENT_MESSAGE_LIMIT,
    batch_limit: int = COMPRESSION_BATCH_MESSAGE_LIMIT,
    raw_tail_token_budget: int | None = None,
    job_source_message_ids: Mapping[str, str] | None = None,
    token_estimator: Callable[[Message], int] | None = None,
) -> tuple[list[Message], list[Message]]:
    """Split history into the oldest foldable prefix and untouched raw suffix.

    The returned batch is always chronological and never includes the protected
    latest raw tail. If a backlog is larger than one bounded LLM request, later
    compression runs continue from the persisted boundary without gaps.
    """
    uncompressed = messages_after_compression_boundary(scene_state, messages)
    if raw_tail_token_budget is not None:
        complete_turns = group_complete_turns(
            uncompressed,
            job_source_message_ids=job_source_message_ids,
        )
        selector_kwargs = {
            "token_estimator": token_estimator or automatic_context_message_tokens,
        }
        raw_turns = select_raw_tail_groups(
            complete_turns,
            token_budget=max(0, int(raw_tail_token_budget)),
            **selector_kwargs,
        )
        foldable_turn_count = len(complete_turns) - len(raw_turns)
        if foldable_turn_count <= 0:
            return [], uncompressed

        batch_turns: list[list[Message]] = []
        batch_message_count = 0
        normalized_batch_limit = max(1, int(batch_limit))
        for turn in complete_turns[:foldable_turn_count]:
            if batch_turns and batch_message_count + len(turn) > normalized_batch_limit:
                break
            batch_turns.append(turn)
            batch_message_count += len(turn)
            if batch_message_count >= normalized_batch_limit:
                break

        taken_turn_count = len(batch_turns)
        batch = [message for turn in batch_turns for message in turn]
        untouched = [
            message
            for turn in complete_turns[taken_turn_count:]
            for message in turn
        ]
        return batch, untouched

    foldable_count = compression_raw_tail_start(uncompressed, raw_tail_limit=raw_tail_limit)
    if foldable_count <= 0:
        return [], uncompressed
    take = min(foldable_count, max(1, int(batch_limit)))
    if take < foldable_count:
        job_id = uncompressed[take - 1].generation_job_id
        if job_id:
            while take < foldable_count and uncompressed[take].generation_job_id == job_id:
                take += 1
    return uncompressed[:take], uncompressed[take:]


def build_compact_scene_memory(
    scene_state: SceneState,
    recent_messages: list[Message],
    *,
    memories: list[CharacterMemory] | None = None,
) -> str:
    """Return only an already-valid prior arc; never synthesize or truncate one.

    Semantic event merging belongs to the compression LLM. A deterministic
    fallback cannot safely turn transcript beats into one-line major events, so
    it must not manufacture a summary that could advance the persisted boundary.
    """
    _ = (recent_messages, memories)
    return validate_scene_memory_summary(scene_state.summary or "") or ""


TRANSIENT_STORY_ARC_MARKERS = [
    "현재 장기 갈등은",
    "직전 주요 사건은",
    "최근 공개 진행은",
    "사용자 입력:",
]


DURABLE_RECENT_KEYWORDS = [
    "약속",
    "스케줄",
    "리허설",
    "뮤직뱅크",
    "뮤뱅",
    "일정",
    "이동",
    "호텔",
    "귀국",
    "데뷔",
    "센터",
    "직캠",
    "알고리즘",
    "비밀",
    "연애",
    "목격",
    "한 방",
    "같은 방",
]


def filter_durable_story_arc_bullets(bullets: list[str]) -> list[str]:
    durable: list[str] = []
    for bullet in bullets:
        text = bullet.strip()
        if not text.startswith("- "):
            continue
        if any(marker in text for marker in TRANSIENT_STORY_ARC_MARKERS):
            continue
        if "장기 기억:" in text:
            continue
        if SCENE_SUMMARY_RAW_MARKER_RE.search(text) or "행동=" in text or "대사방향=" in text:
            continue
        if text not in durable:
            durable.append(compact_text(text, 180))
    return durable


def durable_recent_story_arc_bullets(recent_messages: list[Message]) -> list[str]:
    bullets: list[str] = []
    joined = " ".join(
        compact_text(message.content, 160)
        for message in recent_messages[-8:]
        if message.speaker_type in {"system", "user", "storytelling", "character"} and message.content
    )
    if not joined:
        return bullets
    if any(keyword in joined for keyword in DURABLE_RECENT_KEYWORDS):
        if "리허설" in joined or "뮤직뱅크" in joined or "뮤뱅" in joined:
            bullets.append("- 당면한 공식 일정으로 리허설 준비가 진행 중이다")
        if "호텔" in joined or "한 방" in joined or "같은 방" in joined:
            bullets.append("- 호텔 방에서 함께 머문 뒤 멤버들 사이의 긴장과 장난이 이어졌다")
        if "직캠" in joined or "알고리즘" in joined:
            bullets.append("- 공개 영상과 알고리즘 확산으로 등장인물의 인지도 상승 조짐이 생겼다")
    return bullets[:3]


def extract_summary_section_bullets(
    summary: str | None,
    section_name: str,
    *,
    limit: int = 3,
    preserve_non_bulleted_lines: bool = False,
) -> list[str]:
    if not summary:
        return []
    lines = summary.splitlines()
    start_marker = f"[{section_name}]"
    start = next((idx for idx, line in enumerate(lines) if line.strip() == start_marker), None)
    if start is None:
        return []
    bullets: list[str] = []
    for line in lines[start + 1:]:
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            break
        if stripped.startswith("- "):
            candidate = stripped
        elif preserve_non_bulleted_lines and stripped:
            candidate = f"- {stripped}"
        else:
            candidate = ""
        if candidate and candidate not in bullets:
            bullets.append(candidate)
        if len(bullets) >= limit:
            break
    return bullets


def previous_story_arc_for_compression(summary: str | None, *, limit: int = 12) -> list[str]:
    """Return only durable story arc bullets carried from before the recent tail."""
    bullets = extract_summary_section_bullets(
        summary,
        "Rolling Story Arc",
        limit=limit,
        preserve_non_bulleted_lines=True,
    )
    if not bullets:
        bullets = extract_summary_section_bullets(
            summary,
            "Story Arc",
            limit=limit,
            preserve_non_bulleted_lines=True,
        )
    return filter_durable_story_arc_bullets(bullets)[:limit]


def format_long_term_memories_for_compression(memories: list[CharacterMemory] | None, *, limit: int = 16) -> list[str]:
    if not memories:
        return []
    lines = []
    for memory in memories[:limit]:
        lines.append(
            f"- character_id={memory.character_id}; type={memory.memory_type}; "
            f"importance={memory.importance}; content={compact_text(memory.content, 180)}"
        )
    return lines


def latest_visible_scene_anchor(recent_messages: list[Message]) -> str:
    """Return the newest public beat for current-scene fallback summaries."""
    for message in reversed(recent_messages):
        if message.speaker_type not in {"storytelling", "system", "user"} or not message.content:
            continue
        label = {
            "storytelling": "최근 장면",
            "system": "시스템 지시",
            "user": "사용자 입력",
        }.get(message.speaker_type, message.speaker_type)
        return compact_text(f"{label}: {message.content}", 150)
    return ""


def latest_public_scene_anchor(recent_messages: list[Message]) -> str:
    """Return a compact visible/canon beat for fallback Story Arc updates."""
    priority_types = ["system", "user", "storytelling"]
    for speaker_type in priority_types:
        for message in reversed(recent_messages):
            if message.speaker_type != speaker_type or not message.content:
                continue
            label = {
                "system": "시스템 지시",
                "user": "사용자 입력",
                "storytelling": "내레이션",
                "character": message.speaker_id or "캐릭터",
            }.get(message.speaker_type, message.speaker_type)
            return compact_text(f"{label}: {message.content}", 150)
    return ""


EXPLICIT_LOCATION_KEYWORDS = [
    "홍대 길거리",
    "유저의 집",
    "사용자의 집",
    "준비 구역",
    "자취방",
    "연습실",
    "경기장",
    "라운지",
    "교실",
    "옥상",
    "모텔",
    "호텔",
    "카페",
    "학교",
    "길거리",
    "집",
    "방",
]


def infer_scene_location_from_recent(recent_messages: list[Message]) -> str:
    """Infer only explicit location changes from recent public text."""
    for message in reversed(recent_messages):
        if message.speaker_type not in {"system", "user", "storytelling"} or not message.content:
            continue
        if location := infer_scene_location_from_text(message.content):
            return location
    return ""


def infer_scene_location_from_text(content: str | None) -> str:
    text = compact_text(content, 260)
    if not text:
        return ""
    for keyword in EXPLICIT_LOCATION_KEYWORDS:
        if keyword in text:
            return keyword
    if not any(token in text for token in ["장소", "공간", "무대", "도착", "들어", "이동", "향하", "왔다", "온다"]):
        return ""
    explicit = re.search(r"(?:장소|공간|무대)\s*[:：]\s*([^\n.!?。]{1,40})", text)
    if explicit:
        return compact_text(explicit.group(1), 120)
    arrived = re.search(r"([가-힣A-Za-z0-9·\s]{1,40}?)(?:에|로)\s*(?:도착|들어|이동|향하|왔다|온다)", text)
    if arrived:
        phrase = arrived.group(1).strip()
        phrase = phrase.split()[-1] if len(phrase.split()) > 1 else phrase
        if phrase:
            return compact_text(phrase, 120)
    return ""


def build_structured_scene_patch(scene_state: SceneState, recent_messages: list[Message]) -> dict[str, str]:
    """Derive lightweight structured scene fields from visible recent turns.

    The LLM scene summary is free-form history; drawer fields need stable short
    values so an empty/no-op compression pass does not leave stale current scene
    labels behind.
    """
    visible = [message for message in recent_messages if message.speaker_type in {"system", "user", "character", "storytelling"}]
    latest = visible[-1] if visible else None
    latest_user_or_system = next((message for message in reversed(visible) if message.speaker_type in {"user", "system"}), None)
    latest_character = next((message for message in reversed(visible) if message.speaker_type == "character"), None)

    last_event_parts = []
    if latest_character and latest_character.action:
        last_event_parts.append(latest_character.action)
    if latest:
        last_event_parts.append(latest.content)
    last_event = compact_text(" / ".join(part for part in last_event_parts if part), 220)

    conflict_source = latest_user_or_system or latest_character or latest
    current_conflict = compact_text(conflict_source.content if conflict_source else "", 220)
    if latest_character and latest_character.emotion and current_conflict:
        current_conflict = compact_text(f"{current_conflict} — {latest_character.emotion}", 220)

    inferred_location = (
        infer_scene_location_from_recent(visible)
        or infer_scene_location_from_text(scene_state.summary)
        or infer_scene_location_from_text(scene_state.last_event)
    )

    return {
        "location": inferred_location or compact_text(scene_state.location, 120),
        "mood": compact_text(scene_state.mood, 120),
        "current_conflict": current_conflict or compact_text(scene_state.current_conflict, 220),
        "last_event": last_event or compact_text(scene_state.last_event, 220),
    }


def validate_scene_memory_summary(summary: str) -> str | None:
    text = summary.strip()
    if not text:
        return None
    lines = [line.rstrip() for line in text.splitlines()]
    if not lines or lines[0].strip() != "[Rolling Story Arc]":
        return None
    section_markers = [line.strip() for line in lines if line.strip().startswith("[") and line.strip().endswith("]")]
    if section_markers != ["[Rolling Story Arc]"]:
        return None
    content_lines = [line.strip() for line in lines[1:] if line.strip()]
    if any(not line.startswith("- ") for line in content_lines):
        return None
    bullets = content_lines
    if not bullets or len(bullets) > MAX_SCENE_MEMORY_BULLETS:
        return None
    if any(len(bullet) > MAX_SCENE_MEMORY_BULLET_CHARS for bullet in bullets):
        return None
    forbidden = [
        "Advantage:",
        "Relationship/tension:",
        "Official winner:",
        "Official loser:",
        "Ranking:",
    ]
    if any(marker in text for marker in forbidden):
        return None
    if SCENE_SUMMARY_RAW_MARKER_RE.search(text):
        return None
    if "행동=" in text or "대사방향=" in text:
        return None
    if "Recent flow" in text or "Next continuity anchors" in text:
        return None
    if len(text) > MAX_SCENE_MEMORY_CHARS:
        return None
    return text


def build_scene_memory_source(
    scene_state: SceneState,
    recent_messages: list[Message],
    *,
    include_compression_focus: bool = False,
    room_cast_roles: dict[str, str] | None = None,
    memories: list[CharacterMemory] | None = None,
) -> str:
    """Build an incremental, transcript-only compression source.

    World setting, character cards, user persona, and durable-memory tables are
    injected independently during generation. Repeating them here wastes the
    bounded summary and lets static lore displace actual conversation history.
    """
    # Do not pre-truncate the previous arc. The compression LLM must see the
    # complete durable timeline so it can semantically merge events to budget.
    previous_summary = (scene_state.summary or "").strip()
    lines = [
        "Previous Conversation Summary (covers only messages through the persisted compression boundary):",
        previous_summary or "- none",
        "Chronological Messages To Fold Into The Summary:",
    ]
    if include_compression_focus:
        lines.append(f"Room-specific memory/relationship update contract: {scene_state.compression_focus or ''}")
        relationship_archetype = (scene_state.relationship_archetype or "").strip().lower().replace("-", "_")
        if relationship_archetype:
            lines.append(f"Relationship archetype contract: {relationship_archetype}\n{prompts.format_relationship_archetype(relationship_archetype)}")
        cast_role_contract = prompts.format_cast_role_contract(room_cast_roles)
        if cast_role_contract:
            lines.append(f"Room cast role contract:\n{cast_role_contract}")
    for message in recent_messages:
        if (message.metadata_ or {}).get("response_protocol") in free_body.PROTOCOLS:
            lines.append("- " + prompts.format_message_for_context(message, include_thought=False))
            continue
        parts = [f"speaker_type={message.speaker_type}", f"speaker_id={message.speaker_id}"]
        if message.emotion:
            parts.append(f"emotion={message.emotion}")
        if message.action:
            parts.append(f"action={message.action}")
        # Private thought is intentionally excluded from shared story canon. A
        # thought matters only after it becomes visible through dialogue/action.
        parts.append(f"dialogue/directive={message.content}")
        lines.append("- " + " | ".join(parts))
    return "\n".join(lines)


FAST_COMPRESSION_CONTRACT = system_prompt_service.DEFAULT_COMPRESSION_FAST_STRATEGY


QUALITY_EXTRACTION_SYSTEM = system_prompt_service.DEFAULT_COMPRESSION_QUALITY_EXTRACTION


QUALITY_CRITIC_SYSTEM = system_prompt_service.DEFAULT_COMPRESSION_QUALITY_CRITIC


QUALITY_FINAL_SYSTEM = system_prompt_service.DEFAULT_COMPRESSION_QUALITY_FINAL


BATTLE_COMPRESSION_CONTRACT = system_prompt_service.DEFAULT_COMPRESSION_BATTLE_GUARD


QUALITY_EXTRACTION_RESPONSE_FORMAT = {
    "type": "json_object",
    "name": "compression_continuity_extraction",
    "schema": {
        "type": "object",
        "properties": {
            "events": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "event": {"type": "string"},
                        "actors": {"type": "array", "items": {"type": "string"}},
                        "chronology_causality": {"type": "string"},
                        "lifecycle_state": {"type": "string"},
                        "source_support": {"type": "string"},
                    },
                    "required": ["event", "actors", "chronology_causality", "lifecycle_state", "source_support"],
                },
            },
            "open_hooks": {"type": "array", "items": {"type": "string"}},
            "do_not_promote_to_fact": {"type": "array", "items": {"type": "string"}},
            "terminal_state": {"type": "string"},
        },
        "required": ["events", "open_hooks", "do_not_promote_to_fact", "terminal_state"],
    },
}


QUALITY_CRITIC_RESPONSE_FORMAT = {
    "type": "json_object",
    "name": "compression_independent_critic",
    "schema": {
        "type": "object",
        "properties": {
            "repair_required": {"type": "boolean"},
            "missing_or_weak_items": {"type": "array", "items": {"type": "string"}},
            "unsupported_or_promoted_items": {"type": "array", "items": {"type": "string"}},
            "chronology_lifecycle_corrections": {"type": "array", "items": {"type": "string"}},
            "terminal_hook_requirements": {"type": "array", "items": {"type": "string"}},
            "final_guidance": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["repair_required", "missing_or_weak_items", "unsupported_or_promoted_items", "chronology_lifecycle_corrections", "terminal_hook_requirements", "final_guidance"],
    },
}


def normalize_compression_strategy(value: str | None) -> str:
    return value if value in {"fast", "quality"} else DEFAULT_COMPRESSION_STRATEGY


def compression_genre_contract(genre_mode: str | None, prompt_settings: Mapping[str, str] | None = None) -> str:
    """Return only the domain guard needed by the selected room genre.

    Chronology, lifecycle, attribution, and open-hook retention remain the
    genre-neutral core. Battle ledger language is intentionally conditional so
    romance, mystery, fantasy, slice-of-life, and custom rooms are not prompted
    as if every conversation were a league.
    """
    if not genre_mode:
        return ""
    if normalize_genre_mode(genre_mode) != "battle":
        return ""
    settings = prompt_settings or system_prompt_service.default_prompt_settings_map()
    return settings.get("compression_battle_guard", BATTLE_COMPRESSION_CONTRACT)


def build_quality_compression_evidence_source(scene_state: SceneState, recent_messages: list[Message]) -> str:
    lines = [
        "Previous Conversation Summary (covers only messages through the persisted compression boundary):",
        (scene_state.summary or "").strip() or "- none",
        "Chronological Messages To Fold Into The Summary:",
    ]
    for message in recent_messages:
        if (message.metadata_ or {}).get("response_protocol") in free_body.PROTOCOLS:
            lines.append(f"- message_id={message.id} | " + prompts.format_message_for_context(message, include_thought=False))
            continue
        parts = [
            f"message_id={message.id}",
            f"speaker_type={message.speaker_type}",
            f"speaker_id={message.speaker_id}",
        ]
        if message.emotion:
            parts.append(f"emotion={message.emotion}")
        if message.action:
            parts.append(f"action={message.action}")
        parts.append(f"dialogue/directive={message.content}")
        lines.append("- " + " | ".join(parts))
    return "\n".join(lines)


def _parse_compression_json_object(content: str) -> dict:
    value = (content or "").strip()
    if value.startswith("```") and value.endswith("```"):
        first_newline = value.find("\n")
        if first_newline >= 0:
            value = value[first_newline + 1:-3].strip()
    parsed = json.loads(value)
    if not isinstance(parsed, dict):
        raise ValueError("structured compression stage did not return an object")
    return parsed


def _require_compression_string_list(value: Any, field_name: str) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
        raise ValueError(f"invalid compression field: {field_name}")
    return value


def _validate_quality_extraction(value: dict) -> dict:
    required = {"events", "open_hooks", "do_not_promote_to_fact", "terminal_state"}
    if set(value) != required or not isinstance(value["events"], list):
        raise ValueError("compression extraction contract failed")
    if not isinstance(value["terminal_state"], str) or not value["terminal_state"].strip():
        raise ValueError("compression extraction terminal state is missing")
    event_fields = {"event", "actors", "chronology_causality", "lifecycle_state", "source_support"}
    for event in value["events"]:
        if not isinstance(event, dict) or set(event) != event_fields:
            raise ValueError("compression extraction event contract failed")
        for field_name in event_fields - {"actors"}:
            if not isinstance(event[field_name], str) or not event[field_name].strip():
                raise ValueError(f"compression extraction event field is invalid: {field_name}")
        _require_compression_string_list(event["actors"], "actors")
    _require_compression_string_list(value["open_hooks"], "open_hooks")
    _require_compression_string_list(value["do_not_promote_to_fact"], "do_not_promote_to_fact")
    return value


def _validate_quality_critic(value: dict) -> dict:
    required = {"repair_required", "missing_or_weak_items", "unsupported_or_promoted_items", "chronology_lifecycle_corrections", "terminal_hook_requirements", "final_guidance"}
    if set(value) != required or type(value["repair_required"]) is not bool:
        raise ValueError("compression critic contract failed")
    for field_name in required - {"repair_required"}:
        _require_compression_string_list(value[field_name], field_name)
    return value


def _record_compression_stage(
    client: Any,
    response: Any,
    *,
    strategy: str,
    stage: str,
    status: str,
    parse_code: str,
    validation_code: str,
) -> None:
    recorder = getattr(client, "record_response_outcome", None)
    if not callable(recorder):
        return
    recorder(
        response,
        status=status,
        parse_code=parse_code,
        validation_code=validation_code,
        metadata_updates={"compression_strategy": strategy, "compression_stage": stage},
    )


async def _call_compression_stage(
    client: Any,
    scene_state: SceneState,
    messages: list[dict[str, str]],
    *,
    response_format: dict | None = None,
):
    from app.services.context_management_service import enforce_compression_stage_capacity, resolve_model_context_capacity
    capacity = getattr(client, "compression_context_capacity", None)
    if capacity is None:
        output_getter = getattr(client, "max_output_tokens", None)
        reserve = output_getter() if callable(output_getter) else None
        capacity = resolve_model_context_capacity(None, completion_reserve_tokens=reserve, mandatory_reserve_tokens=0)
    enforce_compression_stage_capacity(messages, capacity, response_format=response_format)
    try:
        return await chat_with_optional_conversation_id(
            client,
            messages,
            response_format=response_format,
            conversation_id=scene_state.conversation_id,
        )
    except LLMUnavailableError as exc:
        raise RuntimeError(f"scene compression LLM unavailable: {compression_error_summary(exc)}") from exc


async def summarize_scene_memory_with_llm(
    scene_state: SceneState,
    recent_messages: list[Message],
    llm_client: LLMClient | None = None,
    *,
    memories: list[CharacterMemory] | None = None,
    prompt_settings: dict[str, str] | None = None,
    compression_strategy: str = "fast",
    genre_mode: str | None = None,
) -> str | None:
    del memories  # Durable notes remain a separate source and are never compression evidence.
    client = llm_client or LLMClient(profile="compression", purpose="scene_compression")
    strategy = normalize_compression_strategy(compression_strategy)
    settings = prompt_settings or system_prompt_service.default_prompt_settings_map()
    if not genre_mode:
        settings = system_prompt_service.scoped_prompt_defaults(settings, include_world=True, include_genre=False)
    base_system_prompt = "\n\n".join(
        part.strip()
        for part in [
            settings.get("compression_scene_base", ""),
            settings.get("compression_source_scope_rules", ""),
        ]
        if part and part.strip()
    )
    fast_contract = settings.get("compression_fast_strategy", FAST_COMPRESSION_CONTRACT)
    quality_extraction_system = settings.get("compression_quality_extraction", QUALITY_EXTRACTION_SYSTEM)
    quality_critic_system = settings.get("compression_quality_critic", QUALITY_CRITIC_SYSTEM)
    quality_final_system = settings.get("compression_quality_final", QUALITY_FINAL_SYSTEM)
    genre_contract = compression_genre_contract(genre_mode, settings)

    if strategy == "fast":
        source = build_scene_memory_source(scene_state, recent_messages, include_compression_focus=False, memories=[])
        response = await _call_compression_stage(
            client,
            scene_state,
            [
                {"role": "system", "content": "\n\n".join(part for part in [base_system_prompt, fast_contract, genre_contract] if part)},
                {"role": "user", "content": source},
            ],
        )
        summary = validate_scene_memory_summary(response.content or "")
        copied_raw = bool(summary and scene_summary_copies_recent_raw_text(summary, recent_messages))
        valid = bool(summary and not copied_raw)
        _record_compression_stage(
            client,
            response,
            strategy=strategy,
            stage="audited_one_pass",
            status="validated" if valid else "validation_failed",
            parse_code="plain_text",
            validation_code="arc_valid" if valid else ("copied_recent_raw" if copied_raw else "arc_invalid"),
        )
        return summary if valid else None

    evidence = build_quality_compression_evidence_source(scene_state, recent_messages)
    extraction_response = await _call_compression_stage(
        client,
        scene_state,
        [
            {"role": "system", "content": "\n\n".join(part for part in [quality_extraction_system, genre_contract] if part)},
            {"role": "user", "content": evidence},
        ],
        response_format=QUALITY_EXTRACTION_RESPONSE_FORMAT,
    )
    try:
        extraction = _validate_quality_extraction(_parse_compression_json_object(extraction_response.content or ""))
    except (json.JSONDecodeError, ValueError) as exc:
        _record_compression_stage(client, extraction_response, strategy=strategy, stage="structured_extraction", status="validation_failed", parse_code="json_invalid", validation_code="extraction_invalid")
        raise ValueError("quality compression extraction failed validation") from exc
    _record_compression_stage(client, extraction_response, strategy=strategy, stage="structured_extraction", status="validated", parse_code="json_ok", validation_code="extraction_valid")

    extraction_json = json.dumps(extraction, ensure_ascii=False)
    critic_response = await _call_compression_stage(
        client,
        scene_state,
        [
            {"role": "system", "content": "\n\n".join(part for part in [quality_critic_system, genre_contract] if part)},
            {"role": "user", "content": "SOURCE EVIDENCE:\n" + evidence + "\n\nSTRUCTURED EXTRACTION:\n" + extraction_json},
        ],
        response_format=QUALITY_CRITIC_RESPONSE_FORMAT,
    )
    try:
        critic = _validate_quality_critic(_parse_compression_json_object(critic_response.content or ""))
    except (json.JSONDecodeError, ValueError) as exc:
        _record_compression_stage(client, critic_response, strategy=strategy, stage="independent_critic", status="validation_failed", parse_code="json_invalid", validation_code="critic_invalid")
        raise ValueError("quality compression critic failed validation") from exc
    _record_compression_stage(client, critic_response, strategy=strategy, stage="independent_critic", status="validated", parse_code="json_ok", validation_code="critic_valid")

    final_response = await _call_compression_stage(
        client,
        scene_state,
        [
            {"role": "system", "content": "\n\n".join(part for part in [base_system_prompt, quality_final_system, genre_contract] if part)},
            {
                "role": "user",
                "content": (
                    "SOURCE EVIDENCE:\n" + evidence
                    + "\n\nSTRUCTURED EXTRACTION:\n" + extraction_json
                    + "\n\nINDEPENDENT CRITIC:\n" + json.dumps(critic, ensure_ascii=False)
                ),
            },
        ],
    )
    summary = validate_scene_memory_summary(final_response.content or "")
    copied_raw = bool(summary and scene_summary_copies_recent_raw_text(summary, recent_messages))
    valid = bool(summary and not copied_raw)
    _record_compression_stage(
        client,
        final_response,
        strategy=strategy,
        stage="final_synthesis",
        status="validated" if valid else "validation_failed",
        parse_code="plain_text",
        validation_code="arc_valid" if valid else ("copied_recent_raw" if copied_raw else "arc_invalid"),
    )
    return summary if valid else None


def relationship_archetype_blocks_memory(content: str, relationship_archetype: str | None) -> bool:
    if not relationship_archetype:
        return False
    normalized = compact_text(content, 500).lower()
    if relationship_archetype in {"obsessive_low_trust", "tsundere_hidden_affection", "guarded_slowburn", "wounded_defensive", "rival_to_lovers", "manipulative_tease"}:
        blocked_pairs = [
            ("완전히 신뢰", "확정"),
            ("안정적인 연인", "확정"),
            ("fully secure trust", "confirmed"),
            ("stable commitment", "confirmed"),
            ("stable love", "confirmed"),
            ("unconditional trust", "confirmed"),
        ]
        return any(left in normalized and right in normalized for left, right in blocked_pairs)
    return False


def validate_conversation_compression_update(data: dict | None, allowed_character_ids: set[str], *, relationship_archetype: str | None = None) -> dict | None:
    if not isinstance(data, dict):
        return None
    scene = data.get("scene") if isinstance(data.get("scene"), dict) else {}
    clean_scene = {
        "summary": validate_scene_memory_summary(scene.get("summary") or data.get("summary") or "") or "",
        "location": compact_text(scene.get("location"), 120),
        "mood": compact_text(scene.get("mood"), 120),
        "current_conflict": compact_text(scene.get("current_conflict"), 220),
        "last_event": compact_text(scene.get("last_event"), 220),
    }
    for key in ["tension_delta", "romance_delta"]:
        try:
            clean_scene[key] = max(-2, min(2, int(scene.get(key) or 0)))
        except (TypeError, ValueError):
            clean_scene[key] = 0
    # Compression updates only the durable story arc and domain ledgers. User
    # notes are manual-only, and auto-derived relationship state is retired.
    memories = []
    relationships = []
    battle_events = []
    for item in (data.get("battle_events") or [])[:8]:
        if not isinstance(item, dict):
            continue
        event_type = item.get("event_type") or "match_result"
        if event_type != "match_result":
            continue
        participant_a_id = compact_text(item.get("participant_a_id"), 80)
        participant_b_id = compact_text(item.get("participant_b_id"), 80)
        winner_id = compact_text(item.get("winner_id"), 80)
        loser_id = compact_text(item.get("loser_id"), 80)
        involved_ids = {participant_a_id, participant_b_id, winner_id, loser_id} - {""}
        if not {participant_a_id, participant_b_id}.issubset(allowed_character_ids) or not involved_ids.issubset(allowed_character_ids):
            continue
        process_summary = compact_text(item.get("process_summary"), 900)
        if not process_summary:
            continue
        battle_events.append({
            "event_type": "match_result",
            "matchup_key": compact_text(item.get("matchup_key"), 160),
            "participant_a_id": participant_a_id,
            "participant_b_id": participant_b_id,
            "winner_id": winner_id,
            "loser_id": loser_id,
            "result_status": compact_text(item.get("result_status") or "completed", 40) or "completed",
            "process_summary": process_summary,
            "decisive_moment": compact_text(item.get("decisive_moment"), 240),
            "source_message_start_id": compact_text(item.get("source_message_start_id"), 80),
            "source_message_end_id": compact_text(item.get("source_message_end_id"), 80),
            "metadata": item.get("metadata") if isinstance(item.get("metadata"), dict) else {},
        })
    return {"scene": clean_scene, "memories": memories, "relationships": relationships, "battle_events": battle_events}


async def extract_durable_memories_with_llm(
    *,
    scene_state: SceneState,
    recent_messages: list[Message],
    character_ids: list[str],
    memories: list[CharacterMemory],
    llm_client: LLMClient | None = None,
    room_cast_roles: dict[str, str] | None = None,
) -> list[dict]:
    client = llm_client or LLMClient(profile="compression", purpose="durable_memory_extraction")
    source = "\n".join([
        build_scene_memory_source(scene_state, recent_messages, include_compression_focus=True, room_cast_roles=room_cast_roles, memories=memories),
        "Existing durable room memories / ledger rows (update these, do not duplicate):",
        *[f"- character_id={m.character_id}; {m.memory_type}; importance={m.importance}; {compact_text(m.content, 160)}" for m in memories],
    ])
    messages = [
        {"role": "system", "content": f"""You extract only durable long-term memory for a character-chat room.
Do not continue the scene. Return strict JSON only.
This pass is separate from timeline compression and relationship updates.
Return only facts that must survive after recent messages leave context.
Use common room memory id `{COMMON_ROOM_MEMORY_CHARACTER_ID}` only for public room canon known by all active characters.
Use specific character ids only for private durable facts, goals, secrets, promises, preferences, or boundaries.
Room-specific compression focus is the memory contract. For battle/league/ranking rooms, do not create winners, losers, rankings, standings, match results, or ledger rows here; official domain state is owned by battle control/domain events.
Reject ordinary dialogue, action, mood, jokes, temporary momentum, internal thoughts, timeline recap, and any official result/standing claim.
Return shape: {{"memories": [{{"character_id":"allowed id or {COMMON_ROOM_MEMORY_CHARACTER_ID}", "memory_type":"fact|preference|event|boundary|open_hook", "content":"concise durable memory", "importance":1-5}}]}}
If nothing durable changed, return {{"memories": []}}."""},
        {"role": "user", "content": f"Allowed character ids: {character_ids}; common room memory id: {COMMON_ROOM_MEMORY_CHARACTER_ID}\n\n{source}"},
    ]
    try:
        response = await chat_with_optional_conversation_id(client, messages, response_format={"type": "json_object"}, conversation_id=scene_state.conversation_id)
    except LLMUnavailableError:
        return []
    data = parse_json_object_from_text(response.content)
    wrapped = validate_conversation_compression_update({"scene": {}, "memories": data.get("memories") if isinstance(data, dict) else [], "relationships": []}, set(character_ids), relationship_archetype=scene_state.relationship_archetype)
    return (wrapped or {}).get("memories") or []


def build_compression_memory_source(
    *,
    scene_state: SceneState,
    recent_messages: list[Message],
    character_ids: list[str],
    memories: list[CharacterMemory],
    room_cast_roles: dict[str, str] | None = None,
) -> str:
    existing_memory_lines = [
        f"- character_id={memory.character_id}; type={memory.memory_type}; importance={memory.importance}; content={compact_text(memory.content, 120)}"
        for memory in memories[:16]
    ]
    return "\n".join([
        build_scene_memory_source(scene_state, recent_messages, include_compression_focus=True, room_cast_roles=room_cast_roles, memories=memories),
        f"Allowed character ids: {character_ids}; common room memory id: {COMMON_ROOM_MEMORY_CHARACTER_ID}",
        "Existing durable memories:",
        *(existing_memory_lines or ["- none"]),
    ])


def build_compression_prompt_harness(
    *,
    scene_state: SceneState,
    recent_messages: list[Message],
    character_ids: list[str],
    memories: list[CharacterMemory],
    room_cast_roles: dict[str, str] | None = None,
):
    """Build an auditable PromptHarness for compression inputs.

    The compiled text is diagnostics only; production LLM calls still use the
    specialized scene/memory prompts, but this ledger shows the
    exact source blocks available to those prompts.
    """
    sections = [
        prompts.PromptSection(
            "compression_scene_source",
            "Compression scene source",
            build_scene_memory_source(scene_state, recent_messages, include_compression_focus=False, memories=memories),
            "compression.scene_source",
            "visible_scene_summary_source",
            900,
            True,
        ),
        prompts.PromptSection(
            "compression_memory_source",
            "Compression durable memory source",
            build_compression_memory_source(
                scene_state=scene_state,
                recent_messages=recent_messages,
                character_ids=character_ids,
                memories=memories,
                room_cast_roles=room_cast_roles,
            ),
            "compression.memory_source",
            "durable_memory_extraction_source",
            1200,
            True,
        ),
        prompts.PromptSection(
            "compression_constraints",
            "Compression constraints",
            "\n".join([
                "Scene summary must stay visible-only and exclude advantage, relationship conclusions, winners, rankings, and ledger claims.",
                "Durable memory must exclude battle/league official results and standings.",
            ]),
            "compression.policy",
            "runtime_compression_guardrails",
            260,
            True,
        ),
    ]
    return prompts.compile_prompt_sections(sections=sections, total_budget_tokens=3600)


async def summarize_conversation_state_with_llm(
    *,
    scene_state: SceneState,
    recent_messages: list[Message],
    character_ids: list[str],
    relationship_states: list[ConversationRelationshipState],
    memories: list[CharacterMemory],
    llm_client: LLMClient | None = None,
    genre_mode: str | None = None,
    official_domain_context: str | None = None,
    room_cast_roles: dict[str, str] | None = None,
    prompt_settings: dict[str, str] | None = None,
    compression_strategy: str = "fast",
) -> dict | None:
    """Run graph-orchestrated compression so timeline, memory, and relationships do not contaminate each other."""
    from app.engine.compression_graph import run_compression_graph

    async def summarize_scene_callback(state: dict) -> str:
        scene_summary = await summarize_scene_memory_with_llm(
            scene_state,
            recent_messages,
            llm_client=llm_client,
            memories=memories,
            prompt_settings=prompt_settings,
            compression_strategy=compression_strategy,
            genre_mode=genre_mode,
        )
        if not scene_summary:
            raise RuntimeError("scene compression returned empty summary")
        if scene_summary_copies_recent_raw_text(scene_summary, recent_messages):
            raise RuntimeError("scene compression copied recent raw text")
        return scene_summary

    async def extract_memories_callback(state: dict) -> list[dict]:
        return []

    def validate_callback(state: dict) -> dict:
        scene_patch = build_structured_scene_patch(scene_state, recent_messages)
        return {
            "scene": {
                "summary": state.get("scene_summary") or "",
                "location": scene_patch["location"],
                "mood": scene_patch["mood"],
                "current_conflict": scene_patch["current_conflict"],
                "last_event": scene_patch["last_event"],
                "tension_delta": 0,
                "romance_delta": 0,
            },
            "memories": state.get("memory_updates") or [],
            "relationships": [],
            "battle_events": [],
        }

    result = await run_compression_graph({
        "compression_harness": build_compression_prompt_harness(
            scene_state=scene_state,
            recent_messages=recent_messages,
            character_ids=character_ids,
            memories=memories,
            room_cast_roles=room_cast_roles,
        ),
        "scene_state": scene_state,
        "recent_messages": recent_messages,
        "character_ids": character_ids,
        "memories": memories,
        "genre_mode": genre_mode,
        "official_domain_context": official_domain_context,
        "summarize_scene": summarize_scene_callback,
        "extract_memories": extract_memories_callback,
        "validate_update": validate_callback,
    })
    return result.get("validated_update")


def should_update_scene_orchestration_summary(
    session: Session,
    conversation_id: str,
    *,
    generated_character_messages: int = 0,
    interval_turns: int = 5,
    force: bool = False,
    prospective_messages: list[Message] | None = None,
    context_management_mode: str = "shadow",
    capacity: ContextCapacity | None = None,
    mandatory_prompt_tokens: int = 0,
    job_source_message_ids: Mapping[str, str] | None = None,
    token_estimator: Callable[[Message], int] | None = None,
    history_token_estimator: Callable[[list[Message]], int] | None = None,
) -> bool:
    """Choose automatic pressure policy or the unchanged rollback cadence."""
    interval_turns = max(1, min(30, int(interval_turns or 5)))
    if generated_character_messages <= 0:
        return False
    scene_state = session.get(SceneState, conversation_id)
    candidate_messages = list_messages(session, conversation_id)
    if prospective_messages:
        persisted_ids = {message.id for message in candidate_messages}
        candidate_messages.extend(message for message in prospective_messages if message.id not in persisted_ids)

    if (context_management_mode or "shadow").strip().lower() == "automatic":
        selector_scene = scene_state or SceneState(conversation_id=conversation_id)
        fallback_capacity = ContextCapacity(
            model_option_key=None,
            context_window_tokens=16_384,
            max_output_tokens=2_048,
            completion_reserve_tokens=2_048,
            mandatory_reserve_tokens=1_024,
            available_input_tokens=13_312,
            working_input_tokens=12_000,
            source="conservative_fallback",
            used_fallback=True,
        )
        try:
            plan = automatic_context_plan(
                selector_scene,
                candidate_messages,
                capacity=capacity or fallback_capacity,
                mandatory_prompt_tokens=mandatory_prompt_tokens,
                job_source_message_ids=job_source_message_ids,
                token_estimator=token_estimator,
                history_token_estimator=history_token_estimator,
            )
        except ValueError:
            # A dangling boundary must be handled by maintenance, never hidden by cadence.
            return True
        return plan.pressure.status in {"high", "hard"} and plan.has_foldable_backlog

    # Shadow and legacy deliberately retain the old cadence/count-tail behavior.
    since = scene_state.last_compression_attempt_at if scene_state else None
    filters = [
        Message.conversation_id == conversation_id,
        Message.speaker_type.in_({"user", "system"}),
    ]
    if since is not None:
        filters.append(Message.created_at > since)
    turn_count = session.exec(
        select(Message)
        .where(*filters)
        .limit(interval_turns)
    ).all()
    if len(turn_count) < interval_turns:
        return False

    selector_scene = scene_state or SceneState(conversation_id=conversation_id)
    try:
        compression_batch, _ = select_incremental_compression_batch(
            selector_scene,
            candidate_messages,
        )
    except ValueError:
        return True
    return bool(compression_batch)


def _latest_message_id(session: Session, conversation_id: str) -> str | None:
    latest = list_messages(session, conversation_id, limit=1)
    return latest[-1].id if latest else None


def _compression_snapshot(model):
    # SQLModel.model_copy also copies SQLAlchemy InstanceState; an expired row
    # then becomes an unusable detached object. Materialize every mapped field.
    from copy import deepcopy
    return type(model)(**{key: deepcopy(getattr(model, key)) for key in type(model).model_fields})


def compression_source_fingerprint(messages: list[Message]) -> str:
    import hashlib
    fields = []
    for message in messages:
        metadata = message.metadata_ or {}
        fields.append([message.id, message.speaker_type, message.speaker_id,
            message.content, message.action, message.thought, message.emotion,
            {key: metadata.get(key) for key in ("response_protocol", "raw_body", "render_parts")}])
    return hashlib.sha256(json.dumps(fields, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def _claim_compression_result(
    session: Session,
    conversation_id: str,
    *,
    source_message_id: str | None,
    expected_revision: int | None,
    expected_boundary_message_id: str | None = None,
    folded_message_ids: list[str] | None = None,
    folded_content_sha256: str | None = None,
) -> bool:
    from app.services.hardening_core_transactions import compression_post_commit_fence
    fence = compression_post_commit_fence.get()
    if fence is not None:
        task_id, owner, attempt = fence
        result = session.exec(sa_update(PostCommitTask).where(
            PostCommitTask.id == task_id, PostCommitTask.status == "processing",
            PostCommitTask.lease_owner == owner, PostCommitTask.attempt_count == attempt,
            PostCommitTask.lease_expires_at.is_(None) | (PostCommitTask.lease_expires_at > datetime.now(timezone.utc)),
        ).values(updated_at=PostCommitTask.updated_at).execution_options(synchronize_session=False))
        if result.rowcount != 1:
            return False
    # Acquire a write boundary BEFORE checking source content. Message edits
    # cannot slip between the freshness read and the scene CAS on SQLite.
    session.execute(sa_update(SceneState).where(SceneState.conversation_id == conversation_id)
        .values(updated_at=SceneState.updated_at).execution_options(synchronize_session=False))
    session.expire_all()
    scene_state = session.get(SceneState, conversation_id)
    if not scene_state:
        return False
    if scene_state.last_compression_source_message_id != expected_boundary_message_id:
        return False
    current_revision = int(scene_state.compression_revision or 0)
    if expected_revision is not None and current_revision != expected_revision:
        return False
    if source_message_id is None:
        return True
    if scene_state.last_compression_source_message_id == source_message_id:
        return False
    source_message = session.get(Message, source_message_id)
    if source_message is None or source_message.conversation_id != conversation_id:
        return False
    if folded_message_ids:
        current_messages = list(session.exec(
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.created_at.asc(), Message.id.asc())
        ))
        try:
            current_after_boundary = messages_after_compression_boundary(scene_state, current_messages)
        except ValueError:
            return False
        current_prefix_ids = [message.id for message in current_after_boundary[:len(folded_message_ids)]]
        if current_prefix_ids != folded_message_ids:
            return False
        if folded_message_ids[-1] != source_message_id:
            return False
        if folded_content_sha256 is not None and compression_source_fingerprint(current_after_boundary[:len(folded_message_ids)]) != folded_content_sha256:
            return False
    return True


def validate_compression_update_for_persistence(update: dict | None) -> dict:
    if not isinstance(update, dict):
        raise ValueError("compression result was not a JSON object")
    scene = update.get("scene")
    if not isinstance(scene, dict):
        raise ValueError("compression result did not contain a scene object")
    raw_summary = scene.get("summary")
    if not isinstance(raw_summary, str) or not raw_summary.strip():
        raise ValueError("compression result contained a blank scene summary")
    summary = compact_block_text(strip_rank_claims_from_scene_summary(raw_summary), MAX_SCENE_MEMORY_CHARS).strip()
    if not summary:
        raise ValueError("compression scene summary became blank after validation")
    return {
        **update,
        "scene": {**scene, "summary": summary},
        "memories": list(update.get("memories") or []),
        "relationships": list(update.get("relationships") or []),
        "battle_events": list(update.get("battle_events") or []),
    }


def apply_conversation_compression_update(
    session: Session,
    conversation_id: str,
    update: dict,
    *,
    source_message_id: str | None = None,
    expected_revision: int | None = None,
    expected_boundary_message_id: str | None = None,
    folded_message_ids: list[str] | None = None,
    folded_content_sha256: str | None = None,
    expected_relationship_updated_at: Mapping[tuple[str, str, str], datetime | None] | None = None,
) -> bool:
    scene = update.get("scene") or {}
    if source_message_id is None:
        scene_state = session.get(SceneState, conversation_id) or SceneState(conversation_id=conversation_id)
        if scene.get("summary"):
            scene_state.summary = compact_block_text(
                strip_rank_claims_from_scene_summary(scene["summary"]),
                MAX_SCENE_MEMORY_CHARS,
            )
        for key in ["location", "mood", "current_conflict", "last_event"]:
            if scene.get(key):
                if key in {"current_conflict", "last_event"} and contains_rank_ledger_claim(scene[key]):
                    continue
                setattr(scene_state, key, scene[key])
        scene_state.tension_level = clamp_state_value(scene_state.tension_level + int(scene.get("tension_delta") or 0))
        scene_state.romance_level = clamp_state_value(scene_state.romance_level + int(scene.get("romance_delta") or 0))
        updated_at = datetime.now(timezone.utc)
        scene_state.last_compressed_at = updated_at
        scene_state.last_compression_error = None
        scene_state.updated_at = updated_at
        session.add(scene_state)
        session.commit()
        session.expire_all()
    else:
        if not _claim_compression_result(
            session,
            conversation_id,
            source_message_id=source_message_id,
            expected_revision=expected_revision,
            expected_boundary_message_id=expected_boundary_message_id,
            folded_message_ids=folded_message_ids,
            folded_content_sha256=folded_content_sha256,
        ):
            session.rollback()
            return False
        summary = compact_block_text(
            strip_rank_claims_from_scene_summary(scene.get("summary") or ""),
            MAX_SCENE_MEMORY_CHARS,
        ).strip()
        if not summary:
            return False
        scene_state = session.get(SceneState, conversation_id)
        if not scene_state:
            return False
        current_revision = int(scene_state.compression_revision or 0)
        compressed_at = datetime.now(timezone.utc)
        scene_table = getattr(SceneState, "__table__")
        boundary_condition = (
            scene_table.c.last_compression_source_message_id.is_(None)
            if expected_boundary_message_id is None
            else scene_table.c.last_compression_source_message_id == expected_boundary_message_id
        )
        claim_and_persist = session.execute(
            sa_update(scene_table)
            .where(
                scene_table.c.conversation_id == conversation_id,
                scene_table.c.compression_revision == current_revision,
                boundary_condition,
            )
            .values(
                summary=summary,
                last_compressed_at=compressed_at,
                last_compression_source_message_id=source_message_id,
                last_compression_error=None,
                compression_revision=current_revision + 1,
                updated_at=compressed_at,
            )
        )
        if getattr(claim_and_persist, "rowcount", 0) != 1:
            session.rollback()
            return False
        session.commit()
        session.expire_all()
    conversation = get_conversation(session, conversation_id)
    genre_domain_service.apply_genre_domain_update(session, conversation, update)
    return True


class SceneSummaryRevisionConflict(RuntimeError):
    pass


def update_scene_summary_manually(
    session: Session,
    conversation_id: str,
    *,
    summary: str,
    expected_revision: int,
) -> SceneState | None:
    validated_summary = validate_scene_memory_summary(summary)
    if not validated_summary:
        raise ValueError("Story Arc must use the Rolling Story Arc heading and valid bullet limits")
    session.expire_all()
    scene_state = session.get(SceneState, conversation_id)
    if scene_state is None:
        return None
    current_revision = int(scene_state.compression_revision or 0)
    if current_revision != expected_revision:
        raise SceneSummaryRevisionConflict(
            f"Scene summary changed (expected revision {expected_revision}, current {current_revision})"
        )
    updated_at = datetime.now(timezone.utc)
    scene_table = getattr(SceneState, "__table__")
    result = session.execute(
        sa_update(scene_table)
        .where(
            scene_table.c.conversation_id == conversation_id,
            scene_table.c.compression_revision == expected_revision,
        )
        .values(
            summary=validated_summary,
            compression_revision=expected_revision + 1,
            last_compression_error=None,
            updated_at=updated_at,
        )
    )
    if getattr(result, "rowcount", 0) != 1:
        session.rollback()
        raise SceneSummaryRevisionConflict("Scene summary changed while the edit was being saved")
    session.commit()
    session.expire_all()
    return session.get(SceneState, conversation_id)


async def update_scene_orchestration_summary(
    session: Session,
    conversation_id: str,
    recent_messages: list[Message],
    llm_client: LLMClient | None = None,
    fallback_llm_client: LLMClient | None = None,
    character_ids: list[str] | None = None,
    compression_strategy: str = DEFAULT_COMPRESSION_STRATEGY,
    raw_tail_token_budget: int | None = None,
    job_source_message_ids: Mapping[str, str] | None = None,
    token_estimator: Callable[[Message], int] | None = None,
) -> SceneState:
    scene_state = session.get(SceneState, conversation_id)
    if not scene_state:
        scene_state = SceneState(conversation_id=conversation_id)
        session.add(scene_state)
    expected_revision = int(scene_state.compression_revision or 0)
    expected_boundary_message_id = scene_state.last_compression_source_message_id
    try:
        recent, _raw_tail = select_incremental_compression_batch(
            scene_state,
            recent_messages,
            raw_tail_token_budget=raw_tail_token_budget,
            job_source_message_ids=job_source_message_ids,
            token_estimator=token_estimator,
        )
    except ValueError as exc:
        scene_state.last_compression_attempt_at = datetime.now(timezone.utc)
        scene_state.last_compression_error = compact_text(str(exc), 1000)
        session.add(scene_state)
        session.commit()
        session.refresh(scene_state)
        return scene_state
    if not recent:
        scene_state.last_compression_error = None
        session.add(scene_state)
        session.commit()
        session.refresh(scene_state)
        return scene_state
    # Only a real foldable source window is a compression attempt. Provider or
    # validation failures after this point still advance the retry cadence, but
    # a protected-raw-tail no-op does not postpone the first useful fold.
    recent = [_compression_snapshot(message) for message in recent]
    folded_content_sha256 = compression_source_fingerprint(recent)
    scene_state.last_compression_attempt_at = datetime.now(timezone.utc)
    session.add(scene_state)
    session.commit()
    session.refresh(scene_state)
    source_message_id = recent[-1].id
    allowed_character_ids = character_ids or sorted({message.speaker_id for message in recent_messages if message.speaker_type == "character"})
    active_ids = set(allowed_character_ids)
    memories = list_visible_durable_memories_for_compression(
        session,
        conversation_id,
        active_ids,
        limit=MAX_CONTEXT_MEMORY_SCAN_ITEMS,
    )
    conversation = get_conversation(session, conversation_id)
    genre_mode = normalize_conversation_genre(getattr(conversation, "genre_mode", None))
    official_domain_context = genre_domain_service.get_official_context(session, conversation) if conversation else ""
    room_cast_roles = {
        participant.participant_id: participant.role
        for participant in get_participants(session, conversation_id)
        if participant.participant_type == "character" and participant.role
    }
    prompt_settings = system_prompt_service.prompt_settings_map()
    prompt_settings = system_prompt_service.scoped_prompt_defaults(prompt_settings,
        include_world=bool(conversation and conversation.world_setting_id), include_genre=bool(genre_mode))
    # Capture each selected compressor's own metadata before ending the read
    # transaction. Fallback admission is recomputed for its actual stage inputs.
    from app.services.context_management_service import resolve_model_context_capacity
    from app.services import model_provider_service
    for client in (llm_client, fallback_llm_client):
        if client is not None and hasattr(client, "__dict__"):
            overrides = getattr(client, "overrides", {}) or {}
            option = model_provider_service.option_by_key(session, overrides.get("model_option_key"))
            output_getter = getattr(client, "max_output_tokens", None)
            output_reserve = output_getter() if callable(output_getter) else None
            client.compression_context_capacity = resolve_model_context_capacity(option, completion_reserve_tokens=output_reserve, mandatory_reserve_tokens=0)
    scene_state = _compression_snapshot(scene_state)
    memories = [_compression_snapshot(memory) for memory in memories]
    session.rollback()  # No open DB transaction across any compression stage.
    llm_update = None
    attempt_errors: list[str] = []
    if allowed_character_ids:
        attempts: list[tuple[str, LLMClient | None]] = [("primary", llm_client)]
        if fallback_llm_client is not None and fallback_llm_client is not llm_client:
            attempts.append(("fallback", fallback_llm_client))
        for attempt_name, attempt_client in attempts:
            try:
                candidate = await summarize_conversation_state_with_llm(
                    scene_state=scene_state,
                    recent_messages=recent,
                    character_ids=allowed_character_ids,
                    relationship_states=[],
                    memories=memories,
                    llm_client=attempt_client,
                    genre_mode=genre_mode,
                    official_domain_context=official_domain_context,
                    room_cast_roles=room_cast_roles,
                    prompt_settings=prompt_settings,
                    compression_strategy=normalize_compression_strategy(compression_strategy),
                )
                llm_update = validate_compression_update_for_persistence(candidate)
                break
            except Exception as exc:
                attempt_errors.append(f"{attempt_name}: {compression_error_summary(exc)}")
                logger.warning(
                    "Scene compression %s attempt failed for conversation %s: %s",
                    attempt_name,
                    conversation_id,
                    compression_error_summary(exc),
                )
        if llm_update is None:
            logger.error("All scene compression attempts failed; keeping previous summary for conversation %s", conversation_id)
            session.rollback()
            persisted = session.get(SceneState, conversation_id) or scene_state
            persisted.last_compression_error = compact_text(" | ".join(attempt_errors), 1000)
            session.add(persisted)
            session.commit()
            session.refresh(persisted)
            return persisted
    if llm_update:
        applied = apply_conversation_compression_update(
            session,
            conversation_id,
            llm_update,
            source_message_id=source_message_id,
            expected_revision=expected_revision,
            expected_boundary_message_id=expected_boundary_message_id,
            folded_message_ids=[message.id for message in recent],
            folded_content_sha256=folded_content_sha256,
        )
        session.expire_all()
        scene_state = session.get(SceneState, conversation_id) or scene_state
        if applied:
            scene_state.last_compression_error = None
            session.add(scene_state)
            session.commit()
            session.refresh(scene_state)
    return scene_state
