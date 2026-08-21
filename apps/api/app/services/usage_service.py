from __future__ import annotations

from datetime import datetime

from sqlalchemy import Integer, func
from sqlmodel import Session, select

from app.db.models import LLMUsageEvent
from app.schemas.conversations import ConversationUsageRead, UsageBucketRead


def summarize_usage(
    session: Session,
    *,
    conversation_id: str | None = None,
    start_at: datetime | None = None,
    end_at: datetime | None = None,
) -> ConversationUsageRead:
    conditions = []
    if conversation_id is not None:
        conditions.append(LLMUsageEvent.conversation_id == conversation_id)
    if start_at is not None:
        conditions.append(LLMUsageEvent.created_at >= start_at)
    if end_at is not None:
        conditions.append(LLMUsageEvent.created_at <= end_at)

    stmt = (
        select(
            LLMUsageEvent.provider,
            LLMUsageEvent.model,
            LLMUsageEvent.purpose,
            func.count(LLMUsageEvent.id),
            func.coalesce(func.sum(LLMUsageEvent.prompt_tokens), 0),
            func.coalesce(func.sum(LLMUsageEvent.completion_tokens), 0),
            func.coalesce(func.sum(LLMUsageEvent.total_tokens), 0),
            func.coalesce(func.sum(func.cast(LLMUsageEvent.estimated, Integer)), 0),
        )
        .where(*conditions)
        .group_by(LLMUsageEvent.provider, LLMUsageEvent.model, LLMUsageEvent.purpose)
        .order_by(LLMUsageEvent.provider, LLMUsageEvent.model, LLMUsageEvent.purpose)
    )
    buckets: list[UsageBucketRead] = []
    for provider, model, purpose, calls, prompt_tokens, completion_tokens, total_tokens, estimated_calls in session.exec(stmt).all():
        buckets.append(UsageBucketRead(
            provider=provider or "unknown",
            model=model or "unknown",
            purpose=purpose or "unknown",
            calls=int(calls or 0),
            prompt_tokens=int(prompt_tokens or 0),
            completion_tokens=int(completion_tokens or 0),
            total_tokens=int(total_tokens or 0),
            estimated_calls=int(estimated_calls or 0),
        ))
    return ConversationUsageRead(
        conversation_id=conversation_id,
        total_calls=sum(bucket.calls for bucket in buckets),
        prompt_tokens=sum(bucket.prompt_tokens for bucket in buckets),
        completion_tokens=sum(bucket.completion_tokens for bucket in buckets),
        total_tokens=sum(bucket.total_tokens for bucket in buckets),
        buckets=buckets,
    )
