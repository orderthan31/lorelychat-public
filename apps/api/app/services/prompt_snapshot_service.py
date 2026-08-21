from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlmodel import Session, delete, select

from app.db.models import LLMPromptSnapshot

DEFAULT_RETENTION_DAYS = 7
MAX_RESPONSE_PREVIEW_CHARS = 4000


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _to_utc_naive(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def prune_prompt_snapshots(session: Session, *, retention_days: int = DEFAULT_RETENTION_DAYS, now: datetime | None = None) -> int:
    """Delete saved prompt snapshots older than the retention window."""
    safe_days = max(1, int(retention_days or DEFAULT_RETENTION_DAYS))
    cutoff = _to_utc_naive(now or _utc_now()) - timedelta(days=safe_days)
    old_rows = list(session.exec(select(LLMPromptSnapshot).where(LLMPromptSnapshot.created_at < cutoff)).all())
    for row in old_rows:
        session.delete(row)
    session.commit()
    return len(old_rows)


def record_prompt_snapshot(
    session: Session,
    *,
    conversation_id: str | None,
    source_message_id: str | None = None,
    provider: str | None = None,
    model: str | None = None,
    purpose: str = "chat_generation",
    compiled_text: str,
    messages: list[dict] | None,
    ledger: list[dict] | None,
    pipeline_steps: list[str] | None,
    used_tokens: int | None = None,
    prompt_tokens: int | None = None,
    completion_tokens: int | None = None,
    total_tokens: int | None = None,
    response_preview: str | None = None,
    metadata: dict | None = None,
    retention_days: int = DEFAULT_RETENTION_DAYS,
) -> LLMPromptSnapshot:
    """Persist the exact prompt payload sent for a generation turn.

    Prompt text can grow quickly, so every insert also prunes rows older than
    the retention window. This keeps the table useful for debugging without
    letting it become an unbounded transcript archive.
    """
    prune_prompt_snapshots(session, retention_days=retention_days)
    snapshot = LLMPromptSnapshot(
        id=f"prompt_{uuid4().hex[:12]}",
        conversation_id=conversation_id,
        source_message_id=source_message_id,
        provider=provider,
        model=model,
        purpose=purpose,
        compiled_text=compiled_text,
        messages_json=messages or [],
        ledger_json=ledger or [],
        pipeline_steps=pipeline_steps or [],
        used_tokens=used_tokens,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        response_preview=(response_preview or "")[:MAX_RESPONSE_PREVIEW_CHARS] or None,
        metadata_=metadata or {},
    )
    session.add(snapshot)
    session.commit()
    session.refresh(snapshot)
    return snapshot
