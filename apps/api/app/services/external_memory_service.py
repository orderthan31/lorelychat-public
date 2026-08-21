from __future__ import annotations

import logging
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Protocol

from sqlmodel import Session, select

from app.core.config import Settings, get_settings
from app.db.models import Character, CharacterMemory, Conversation, Message

logger = logging.getLogger(__name__)

AUTHORITATIVE_DOMAIN_MARKERS = (
    "공식 서열",
    "현재 공개된 공식 서열",
    "battle_history",
    "ranking_snapshot",
    "challenge lock",
    "challenge-lock",
    "챌린지 락",
    "확정 승패",
    "공식 결과",
    "league standings",
    "현재 리그 승점 장부",
    "현재까지 대전 히스토리",
    "승점 장부",
)

BATTLE_RESULT_MEMORY_RE = re.compile(
    r"(\d+경기|vs|승점|승리|패배|상대로\s*승리|에게\s*패배|대전\s*히스토리|리그\s*승점)",
    re.IGNORECASE,
)

MESSAGE_WINDOW_INGEST_KEY = "external_memory_window_ingested"
MESSAGE_WINDOW_SOURCE = "auto_message_window"


@dataclass
class ExternalMemoryItem:
    content: str
    score: float | None = None
    metadata: dict = field(default_factory=dict)


@dataclass
class ExternalMemorySyncDecision:
    memory_id: str
    should_sync: bool
    reason: str
    metadata: dict = field(default_factory=dict)
    missing_metadata: list[str] = field(default_factory=list)


REQUIRED_LOCAL_MEMORY_METADATA_KEYS = (
    "app_id",
    "workspace",
    "user_id",
    "conversation_id",
    "genre_mode",
    "scope",
    "character_id",
    "memory_type",
    "source",
    "local_memory_id",
    "importance",
)

RAW_DIALOGUE_MARKERS = ("dialogue=", "action=", "thought=", "emotion=", "speaker_id=")


class ExternalMemoryProvider(Protocol):
    def add_memory(self, content: str, *, metadata: dict) -> str | None: ...

    def search(self, query: str, *, filters: dict, limit: int) -> list[ExternalMemoryItem]: ...


class NoopExternalMemoryProvider:
    def add_memory(self, content: str, *, metadata: dict) -> str | None:
        return None

    def search(self, query: str, *, filters: dict, limit: int) -> list[ExternalMemoryItem]:
        return []


class Mem0ExternalMemoryProvider:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.base_url = (settings.mem0_base_url or "").rstrip("/")
        if not self.base_url:
            try:
                from mem0 import MemoryClient  # type: ignore
            except Exception as exc:  # pragma: no cover - depends on optional package
                raise RuntimeError("mem0ai package is not installed") from exc

            kwargs = {}
            if settings.mem0_api_key:
                kwargs["api_key"] = settings.mem0_api_key
            self.client = MemoryClient(**kwargs)
        else:
            self.client = None

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.settings.mem0_api_key and not self.settings.mem0_api_key.startswith("local-"):
            headers["X-API-Key"] = self.settings.mem0_api_key
        return headers

    def add_memory(self, content: str, *, metadata: dict) -> str | None:
        try:
            if self.base_url:
                import httpx

                payload = {
                    "messages": [{"role": "user", "content": content}],
                    "user_id": metadata.get("user_id") or self.settings.mem0_user_id,
                    "metadata": metadata,
                    "infer": False,
                }
                response = httpx.post(f"{self.base_url}/memories", json=payload, headers=self._headers(), timeout=60)
                response.raise_for_status()
                result = response.json()
            else:
                assert self.client is not None
                result = self.client.add(
                    content,
                    user_id=metadata.get("user_id") or self.settings.mem0_user_id,
                    metadata=metadata,
                )
            if isinstance(result, dict):
                if result.get("id") or result.get("memory_id"):
                    return result.get("id") or result.get("memory_id")
                nested = result.get("results") or result.get("memories")
                if isinstance(nested, list) and nested and isinstance(nested[0], dict):
                    return nested[0].get("id") or nested[0].get("memory_id") or "mem0:selfhost:accepted"
                return "mem0:selfhost:accepted"
            if isinstance(result, list) and result and isinstance(result[0], dict):
                return result[0].get("id") or result[0].get("memory_id") or "mem0:selfhost:accepted"
        except Exception:
            logger.exception("Non-critical mem0 add failed")
        return None

    def search(self, query: str, *, filters: dict, limit: int) -> list[ExternalMemoryItem]:
        try:
            if self.base_url:
                import httpx

                payload = {"query": query, "filters": filters, "top_k": limit}
                response = httpx.post(f"{self.base_url}/search", json=payload, headers=self._headers(), timeout=60)
                response.raise_for_status()
                result = response.json()
            else:
                assert self.client is not None
                result = self.client.search(
                    query,
                    user_id=filters.get("user_id") or self.settings.mem0_user_id,
                    filters=filters,
                    limit=limit,
                )
        except Exception:
            logger.exception("Non-critical mem0 search failed")
            return []
        return normalize_mem0_search_result(result)


def normalize_mem0_search_result(result) -> list[ExternalMemoryItem]:
    raw_items = result.get("results", result) if isinstance(result, dict) else result
    if not isinstance(raw_items, list):
        return []
    items: list[ExternalMemoryItem] = []
    for raw in raw_items:
        if not isinstance(raw, dict):
            continue
        content = raw.get("memory") or raw.get("content") or raw.get("text") or ""
        if not str(content).strip():
            continue
        items.append(ExternalMemoryItem(
            content=str(content).strip(),
            score=raw.get("score"),
            metadata=raw.get("metadata") or {},
        ))
    return items


def get_provider(settings: Settings | None = None) -> ExternalMemoryProvider:
    settings = settings or get_settings()
    if not (settings.mem0_enabled and settings.memory_provider == "mem0"):
        return NoopExternalMemoryProvider()
    try:
        return Mem0ExternalMemoryProvider(settings)
    except Exception:
        logger.exception("External memory provider unavailable; falling back to noop")
        return NoopExternalMemoryProvider()


def build_memory_metadata(memory: CharacterMemory, *, genre_mode: str = "battle", settings: Settings | None = None) -> dict:
    settings = settings or get_settings()
    return {
        "app_id": settings.mem0_app_id,
        "workspace": settings.mem0_workspace,
        "user_id": settings.mem0_user_id,
        "conversation_id": memory.conversation_id,
        "genre_mode": genre_mode,
        "scope": "room" if memory.character_id == "__room__" else "character",
        "character_id": memory.character_id,
        "memory_type": memory.memory_type,
        "source": "compression",
        "local_memory_id": memory.id,
        "is_authoritative_domain_state": is_authoritative_domain_memory(memory.content),
        "domain_state_key": None,
        "importance": memory.importance,
    }


def is_authoritative_domain_memory(content: str) -> bool:
    normalized = " ".join((content or "").lower().split())
    return any(marker.lower() in normalized for marker in AUTHORITATIVE_DOMAIN_MARKERS)


def should_sync_memory_to_external(memory: CharacterMemory) -> bool:
    return explain_local_memory_sync(memory).should_sync


def explain_local_memory_sync(memory: CharacterMemory, *, genre_mode: str = "battle", settings: Settings | None = None) -> ExternalMemorySyncDecision:
    content = memory.content or ""
    lowered = content.lower()
    metadata = build_memory_metadata(memory, genre_mode=genre_mode, settings=settings)
    missing_metadata = [key for key in REQUIRED_LOCAL_MEMORY_METADATA_KEYS if metadata.get(key) in (None, "")]

    if not content.strip():
        return ExternalMemorySyncDecision(memory.id, False, "empty_content", metadata, missing_metadata)
    if memory.importance < 3:
        return ExternalMemorySyncDecision(memory.id, False, "low_importance", metadata, missing_metadata)
    if is_authoritative_domain_memory(content):
        return ExternalMemorySyncDecision(memory.id, False, "authoritative_domain_state", metadata, missing_metadata)
    if genre_mode == "battle" and BATTLE_RESULT_MEMORY_RE.search(content):
        return ExternalMemorySyncDecision(memory.id, False, "battle_result_or_score_belongs_to_ledger", metadata, missing_metadata)
    if any(marker in lowered for marker in RAW_DIALOGUE_MARKERS):
        return ExternalMemorySyncDecision(memory.id, False, "raw_dialogue_marker", metadata, missing_metadata)
    if missing_metadata:
        return ExternalMemorySyncDecision(memory.id, False, "missing_metadata", metadata, missing_metadata)
    return ExternalMemorySyncDecision(memory.id, True, "syncable", metadata, [])


def _compact(value: str | None, limit: int = 180) -> str:
    text = " ".join((value or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _message_metadata(message: Message) -> dict:
    return message.metadata_ if isinstance(message.metadata_, dict) else {}


def _set_message_metadata(message: Message, key: str, value) -> None:
    metadata = dict(_message_metadata(message))
    metadata[key] = value
    message.metadata_ = metadata


def _format_message_for_window(message: Message, *, speaker_names: dict[str, str] | None = None) -> str:
    speaker_names = speaker_names or {}
    speaker_label = speaker_names.get(message.speaker_id, message.speaker_id)
    parts = [f"{message.speaker_type}:{speaker_label} ({message.speaker_id})"]
    if message.action:
        parts.append(f"action={_compact(message.action, 80)}")
    if message.content:
        parts.append(f"dialogue={_compact(message.content, 160)}")
    if message.emotion:
        parts.append(f"emotion={_compact(message.emotion, 40)}")
    return " | ".join(parts)


def build_message_window_recall_card(
    *,
    conversation: Conversation,
    messages: list[Message],
    genre_mode: str,
    settings: Settings | None = None,
    speaker_names: dict[str, str] | None = None,
) -> tuple[str, dict] | None:
    settings = settings or get_settings()
    selected = [message for message in messages if (message.content or message.action or message.emotion)]
    if len(selected) < 2:
        return None
    start = selected[0]
    end = selected[-1]
    lines = [
        "Conversation message-window recall card",
        f"conversation_id={conversation.id}",
        f"genre_mode={genre_mode}",
        "memory_type=message_window_recall",
        f"time_range={start.created_at}..{end.created_at}",
        f"message_id_range={start.id}..{end.id}",
        "source_note=auto-ingested from recent chat window; official battle results/standings come from local ledger, not this card.",
        "speakers=" + ", ".join(
            sorted({speaker_names.get(message.speaker_id, message.speaker_id) for message in selected})
        ) if speaker_names else "speakers=unknown",
        "messages:",
    ]
    lines.extend(f"- {_format_message_for_window(message, speaker_names=speaker_names)}" for message in selected[-12:])
    content = "\n".join(lines)
    metadata = {
        "app_id": settings.mem0_app_id,
        "workspace": settings.mem0_workspace,
        "user_id": settings.mem0_user_id,
        "conversation_id": conversation.id,
        "genre_mode": genre_mode,
        "scope": "room",
        "character_id": "__room__",
        "memory_type": "message_window_recall",
        "source": MESSAGE_WINDOW_SOURCE,
        "message_start_id": start.id,
        "message_end_id": end.id,
        "message_count": len(selected),
        "window_start_at": str(start.created_at),
        "window_end_at": str(end.created_at),
        "ingested_at": datetime.now(timezone.utc).isoformat(),
        "is_authoritative_domain_state": False,
    }
    return content, metadata


def sync_message_window_to_external(
    session: Session,
    conversation: Conversation,
    messages: list[Message],
    *,
    genre_mode: str,
    settings: Settings | None = None,
) -> str | None:
    settings = settings or get_settings()
    if not (settings.mem0_enabled and settings.mem0_write_enabled and settings.memory_provider == "mem0"):
        return None
    if not messages:
        return None
    end_message = messages[-1]
    if _message_metadata(end_message).get(MESSAGE_WINDOW_INGEST_KEY):
        return None
    speaker_ids = {message.speaker_id for message in messages if message.speaker_type == "character" and message.speaker_id}
    speaker_names = {
        character.id: character.name
        for character in session.exec(select(Character).where(Character.id.in_(speaker_ids))).all()
    } if speaker_ids else {}
    card = build_message_window_recall_card(
        conversation=conversation,
        messages=messages,
        genre_mode=genre_mode,
        settings=settings,
        speaker_names=speaker_names,
    )
    if not card:
        return None
    content, metadata = card
    try:
        memory_id = get_provider(settings).add_memory(content, metadata=metadata)
        _set_message_metadata(end_message, MESSAGE_WINDOW_INGEST_KEY, {
            "source": MESSAGE_WINDOW_SOURCE,
            "memory_id": memory_id,
            "ingested_at": metadata["ingested_at"],
            "message_start_id": metadata["message_start_id"],
            "message_end_id": metadata["message_end_id"],
        })
        session.add(end_message)
        session.commit()
        return memory_id
    except Exception:
        logger.exception("Non-critical message-window external memory sync failed for %s", conversation.id)
        return None


def build_external_memory_qc_report(session: Session, conversation: Conversation | None, *, limit: int = 80) -> str:
    settings = get_settings()
    if conversation is None:
        return "[External memory QC]\nconversation=missing"
    genre_mode = getattr(conversation, "genre_mode", None) or "battle"
    memories = list(session.exec(
        select(CharacterMemory)
        .where(CharacterMemory.conversation_id == conversation.id)
        .order_by(CharacterMemory.updated_at.desc())
        .limit(max(1, min(200, limit)))
    ).all())
    decisions = [explain_local_memory_sync(memory, genre_mode=genre_mode, settings=settings) for memory in memories]
    reason_counts = Counter(decision.reason for decision in decisions)
    syncable = [decision for decision in decisions if decision.should_sync]
    missing_metadata = [decision for decision in decisions if decision.missing_metadata]
    recent_messages = list(session.exec(
        select(Message)
        .where(Message.conversation_id == conversation.id)
        .order_by(Message.created_at.desc(), Message.id.desc())
        .limit(40)
    ).all())
    recent_messages = list(reversed(recent_messages))
    recent_ingested = [
        message for message in recent_messages
        if isinstance(message.metadata_, dict) and message.metadata_.get(MESSAGE_WINDOW_INGEST_KEY)
    ]
    last_ingested = recent_ingested[-1] if recent_ingested else None
    newer_than_last_ingest = [
        message for message in recent_messages
        if last_ingested is None or message.created_at > last_ingested.created_at
    ]

    lines = [
        "[External memory QC]",
        "purpose=diagnose mem0/local-memory sync health and stale/noisy recall risk; not story canon",
        "prompt_injection=no (diagnostic section only; generated replies should use external_memory after filtering)",
        f"provider={settings.memory_provider} mem0_enabled={settings.mem0_enabled} read={settings.mem0_read_enabled} write={settings.mem0_write_enabled}",
        f"conversation_id={conversation.id} genre_mode={genre_mode}",
        f"sampled_local_memories={len(memories)} syncable={len(syncable)} skipped={len(decisions) - len(syncable)}",
        f"recent_message_sample={len(recent_messages)} ingested_window_ends={len(recent_ingested)} messages_after_last_ingest={len(newer_than_last_ingest)}",
    ]
    if last_ingested:
        ingest_meta = last_ingested.metadata_.get(MESSAGE_WINDOW_INGEST_KEY, {}) if isinstance(last_ingested.metadata_, dict) else {}
        lines.append(
            "last_message_window_ingest="
            f"end_message={last_ingested.id} at={last_ingested.created_at} "
            f"memory_id={ingest_meta.get('memory_id') or 'unknown'}"
        )
    else:
        lines.append("last_message_window_ingest=none")
    if reason_counts:
        lines.append("reasons=" + ", ".join(f"{reason}={count}" for reason, count in sorted(reason_counts.items())))
    if missing_metadata:
        lines.append("missing_metadata=" + ", ".join(f"{decision.memory_id}:{'/'.join(decision.missing_metadata)}" for decision in missing_metadata[:5]))
    else:
        lines.append("missing_metadata=none")
    if syncable:
        lines.append("sync_candidates=" + ", ".join(decision.memory_id for decision in syncable[:8]))
    else:
        lines.append("sync_candidates=none")
    skipped_examples = [decision for decision in decisions if not decision.should_sync][:5]
    if skipped_examples:
        lines.append("skip_examples=" + ", ".join(f"{decision.memory_id}:{decision.reason}" for decision in skipped_examples))
    if genre_mode == "battle":
        lines.append("battle_note=local ledger is authoritative; mem0 QC validates recall-card coverage/metadata, not official result correctness")
    return "\n".join(lines)


def build_search_filters(*, conversation_id: str, character_id: str, genre_mode: str, settings: Settings | None = None) -> dict:
    settings = settings or get_settings()
    return {
        "app_id": settings.mem0_app_id,
        "workspace": settings.mem0_workspace,
        "user_id": settings.mem0_user_id,
        "conversation_id": conversation_id,
        "genre_mode": genre_mode,
        "character_id": ["__room__", character_id],
    }


def filter_retrieved_items(items: list[ExternalMemoryItem], *, conversation_id: str, character_id: str, genre_mode: str, settings: Settings | None = None) -> list[ExternalMemoryItem]:
    settings = settings or get_settings()
    allowed_character_ids = {"__room__", character_id}
    filtered: list[ExternalMemoryItem] = []
    seen: set[str] = set()
    for item in items:
        metadata = item.metadata or {}
        if metadata.get("app_id") and metadata.get("app_id") != settings.mem0_app_id:
            continue
        if metadata.get("conversation_id") and metadata.get("conversation_id") != conversation_id:
            continue
        if metadata.get("genre_mode") and metadata.get("genre_mode") != genre_mode:
            continue
        if metadata.get("is_authoritative_domain_state") is True:
            continue
        item_character_id = metadata.get("character_id")
        if item_character_id and item_character_id not in allowed_character_ids:
            continue
        key = " ".join(item.content.split()).lower()
        if not key or key in seen:
            continue
        seen.add(key)
        filtered.append(item)
    return filtered


def rank_retrieved_items(items: list[ExternalMemoryItem], *, query: str) -> list[ExternalMemoryItem]:
    query_terms = {term for term in re.split(r"\s+", query.lower()) if len(term) >= 2}
    source_weight = {
        "league_index_rechunk_v1": 38.0,
        "battle_ledger_rechunk_v1": 42.0,
        "auto_message_window": 55.0,
        "local_memory_rechunk_v1": 24.0,
        "compression": 22.0,
        "battle_ledger": 25.0,
        "backfill_rechunk_v1": 5.0,
    }
    type_weight = {
        "league_index_recall": 20.0,
        "match_result_recall": 12.0,
        "durable_fact": 12.0,
        "durable_event": 10.0,
        "durable_user_note": 10.0,
        "interview_recall": 8.0,
        "scene_transition_recall": 4.0,
        "message_window_recall": 18.0,
    }

    def score(item: ExternalMemoryItem) -> float:
        metadata = item.metadata or {}
        content = item.content.lower()
        query_l = query.lower()
        overlap = sum(1 for term in query_terms if term in content)
        semantic_boost = 0.0
        index_key = str(metadata.get("index_key") or "")
        if index_key == "next_opponent_interview_index" and any(marker in query_l for marker in ["붙어보고", "다음상대", "다음 상대", "만만", "상대"]):
            semantic_boost += 45.0
        if index_key == "league_matchup_index" and any(marker in query_l for marker in ["대전", "매치업", "누가", "이겼", "결과"]):
            semantic_boost += 35.0
        if index_key == "league_standings_index" and any(marker in query_l for marker in ["승점", "순위", "장부", "테이블"]):
            semantic_boost += 35.0
        named_markers = [
            str(marker).strip()
            for marker in metadata.get("character_names", [])
            if str(marker).strip()
        ]
        name_hits = sum(1 for marker in named_markers if marker in query_l and marker in content)
        if name_hits and str(metadata.get("source") or "") in {"battle_ledger_rechunk_v1", "auto_message_window", "backfill_rechunk_v1"}:
            semantic_boost += name_hits * 18.0
        if index_key in {"league_matchup_index", "league_standings_index"} and name_hits:
            semantic_boost -= 18.0
        return (
            source_weight.get(str(metadata.get("source") or ""), 0.0)
            + type_weight.get(str(metadata.get("memory_type") or ""), 0.0)
            + semantic_boost
            + overlap * 2.0
            + float(item.score or 0.0)
        )

    return sorted(items, key=score, reverse=True)


def format_external_memory_context(items: list[ExternalMemoryItem], *, limit: int | None = None) -> str:
    selected = [item for item in items if is_prompt_injectable_external_memory(item)]
    selected = selected[:limit] if limit else selected
    if not selected:
        return ""
    lines = ["[Retrieved semantic memory]"]
    for item in selected:
        metadata = item.metadata or {}
        memory_type = metadata.get("memory_type") or "memory"
        importance = metadata.get("importance") or "?"
        content = " ".join(item.content.split())[:100]
        lines.append(f"- {memory_type}/{importance}: {content}")
    return "\n".join(lines)


def is_prompt_injectable_external_memory(item: ExternalMemoryItem) -> bool:
    """Keep mem0 diagnostics/index cards out of generation prompts.

    Some mem0 rows are search indexes or QC helpers. They are useful for retrieval
    maintenance, but if injected as continuity they look like stale story facts.
    """
    metadata = item.metadata or {}
    memory_type = str(metadata.get("memory_type") or "").strip()
    source = str(metadata.get("source") or "").strip()
    content = " ".join((item.content or "").split())
    if memory_type in {"league_index_recall", "external_memory_qc"}:
        return False
    if source in {"league_index_rechunk_v1"}:
        return False
    if re.match(r"League .+ index recall card", content, flags=re.IGNORECASE):
        return False
    return bool(content)


def sync_local_memory_to_external(memory: CharacterMemory, *, genre_mode: str = "battle", settings: Settings | None = None) -> str | None:
    settings = settings or get_settings()
    if not (settings.mem0_enabled and settings.mem0_write_enabled and settings.memory_provider == "mem0"):
        return None
    if not should_sync_memory_to_external(memory):
        return None
    metadata = build_memory_metadata(memory, genre_mode=genre_mode, settings=settings)
    try:
        return get_provider(settings).add_memory(memory.content, metadata=metadata)
    except Exception:
        logger.exception("Non-critical external memory sync failed for %s", memory.id)
        return None


def sync_battle_memory_card_to_external(card: dict, *, genre_mode: str = "battle", settings: Settings | None = None) -> str | None:
    settings = settings or get_settings()
    if genre_mode != "battle":
        return None
    if not (settings.mem0_enabled and settings.mem0_write_enabled and settings.memory_provider == "mem0"):
        return None
    content = str(card.get("content") or "").strip()
    metadata = card.get("metadata") if isinstance(card.get("metadata"), dict) else {}
    if not content:
        return None
    metadata = {
        "app_id": settings.mem0_app_id,
        "workspace": settings.mem0_workspace,
        "user_id": settings.mem0_user_id,
        **metadata,
        "genre_mode": "battle",
        "source": metadata.get("source") or "battle_ledger",
        "scope": metadata.get("scope") or "battle_match",
    }
    try:
        return get_provider(settings).add_memory(content, metadata=metadata)
    except Exception:
        logger.exception("Non-critical battle memory card sync failed")
        return None


def search_external_continuity(
    *,
    conversation_id: str,
    character_id: str,
    genre_mode: str,
    query: str,
    settings: Settings | None = None,
) -> list[ExternalMemoryItem]:
    settings = settings or get_settings()
    if not (settings.mem0_enabled and settings.mem0_read_enabled and settings.memory_provider == "mem0"):
        return []
    provider = get_provider(settings)
    filters = build_search_filters(
        conversation_id=conversation_id,
        character_id=character_id,
        genre_mode=genre_mode,
        settings=settings,
    )
    try:
        fetch_limit = max(settings.mem0_search_limit, min(100, settings.mem0_search_limit * 40))
        items = provider.search(query, filters=filters, limit=fetch_limit)
        if genre_mode == "battle":
            index_filters = {**filters, "source": "league_index_rechunk_v1"}
            items.extend(provider.search(query, filters=index_filters, limit=20))
            ledger_filters = {**filters, "source": "battle_ledger_rechunk_v1"}
            items.extend(provider.search(query, filters=ledger_filters, limit=20))
            window_filters = {**filters, "source": MESSAGE_WINDOW_SOURCE}
            items.extend(provider.search(query, filters=window_filters, limit=20))
            items.extend(provider.search(
                f"recent current scene latest chat window {query}",
                filters=window_filters,
                limit=20,
            ))
    except Exception:
        logger.exception("Non-critical external memory search failed")
        return []
    filtered = filter_retrieved_items(
        items,
        conversation_id=conversation_id,
        character_id=character_id,
        genre_mode=genre_mode,
        settings=settings,
    )
    return rank_retrieved_items(filtered, query=query)[: settings.mem0_search_limit]
