from __future__ import annotations

import asyncio
import threading
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session

from app.api.conversations import _execute_claimed_post_commit_task
from app.db.models import Conversation, ConversationRelationshipState, Message, PostCommitTask
from app.services import conversation_service
from app.services.post_commit_dispatcher import PostCommitDispatcher


def _task(session: Session, task_id: str, *, status: str = "queued", available_at: datetime | None = None) -> PostCommitTask:
    now = datetime.now(timezone.utc)
    row = PostCommitTask(
        id=task_id,
        unique_key=f"unique:{task_id}",
        task_type="asset",
        status=status,
        payload_={"message_id": f"msg_{task_id}"},
        available_at=available_at or now,
        created_at=now,
        updated_at=now,
    )
    session.add(row)
    session.commit()
    return row


def test_post_commit_task_claim_retry_heartbeat_and_completion(session):
    now = datetime.now(timezone.utc)
    row = _task(session, "pct_retry", available_at=now)

    claimed = conversation_service.claim_next_post_commit_task(
        session,
        lease_owner="worker-a",
        lease_seconds=60,
        now=now,
    )
    assert claimed is not None and claimed.id == row.id
    assert claimed.status == "processing"
    assert claimed.attempt_count == 1
    assert claimed.lease_owner == "worker-a"

    assert conversation_service.heartbeat_post_commit_task(
        session,
        claimed.id,
        lease_owner="wrong-worker",
        lease_seconds=60,
        now=now + timedelta(seconds=1),
    ) is False
    assert conversation_service.heartbeat_post_commit_task(
        session,
        claimed.id,
        lease_owner="worker-a",
        lease_seconds=60,
        now=now + timedelta(seconds=1),
    ) is True

    conversation_service.mark_post_commit_task_failed(
        session,
        claimed,
        RuntimeError("temporary side effect failure"),
        lease_owner="worker-a",
        max_attempts=3,
        now=now + timedelta(seconds=2),
    )
    session.expire_all()
    retry = session.get(PostCommitTask, row.id)
    assert retry is not None and retry.status == "queued"
    assert retry.last_error == "RuntimeError: temporary side effect failure"
    retry_available_at = retry.available_at.replace(tzinfo=timezone.utc)
    assert retry_available_at > now + timedelta(seconds=2)
    assert conversation_service.claim_next_post_commit_task(
        session,
        lease_owner="worker-b",
        lease_seconds=60,
        now=now + timedelta(seconds=2),
    ) is None

    reclaimed = conversation_service.claim_next_post_commit_task(
        session,
        lease_owner="worker-b",
        lease_seconds=60,
        now=retry.available_at + timedelta(milliseconds=1),
    )
    assert reclaimed is not None and reclaimed.id == row.id
    assert reclaimed.attempt_count == 2
    conversation_service.mark_post_commit_task_completed(
        session,
        reclaimed,
        lease_owner="worker-b",
    )
    session.expire_all()
    completed = session.get(PostCommitTask, row.id)
    assert completed is not None and completed.status == "completed"
    assert completed.completed_at is not None
    assert completed.lease_owner is None


def test_expired_post_commit_task_lease_is_requeued(session):
    now = datetime.now(timezone.utc)
    row = _task(session, "pct_expired", status="processing")
    row.lease_owner = "dead-worker"
    row.lease_expires_at = now - timedelta(seconds=1)
    session.add(row)
    session.commit()

    assert conversation_service.requeue_expired_post_commit_tasks(session, now=now) == 1
    session.expire_all()
    recovered = session.get(PostCommitTask, row.id)
    assert recovered is not None and recovered.status == "queued"
    assert recovered.lease_owner is None
    assert recovered.lease_expires_at is None


def test_post_commit_dispatcher_claims_and_completes_task(engine):
    with Session(engine) as session:
        row = _task(session, "pct_dispatch")
        task_id = row.id

    ran = threading.Event()

    def runner(claimed_task_id: str, lease_owner: str) -> None:
        with Session(engine) as session:
            task = session.get(PostCommitTask, claimed_task_id)
            assert task is not None and task.status == "processing"
            conversation_service.mark_post_commit_task_completed(
                session,
                task,
                lease_owner=lease_owner,
            )
        ran.set()

    dispatcher = PostCommitDispatcher(
        engine,
        max_workers=1,
        poll_interval_seconds=0.05,
        lease_seconds=10,
        heartbeat_interval_seconds=0.05,
    )
    dispatcher.start(runner)
    try:
        assert ran.wait(2.0)
    finally:
        dispatcher.stop()

    with Session(engine) as session:
        completed = session.get(PostCommitTask, task_id)
        assert completed is not None and completed.status == "completed"
        assert completed.attempt_count == 1


def test_retired_continuity_task_is_noop_when_completion_commit_fails(session):
    conversation_id = "conv_effect_atomic"
    character_id = "char_effect_atomic"
    session.add(Conversation(id=conversation_id, mode="user_character"))
    session.add(Message(
        id="msg_effect_source",
        conversation_id=conversation_id,
        speaker_type="user",
        speaker_id="user_001",
        content="좋아?",
        action="손을 내민다",
    ))
    session.add(Message(
        id="msg_effect_reply",
        conversation_id=conversation_id,
        speaker_type="character",
        speaker_id=character_id,
        content="응",
        emotion="warm",
    ))
    task = PostCommitTask(
        id="pct_effect_atomic",
        unique_key="unique:pct_effect_atomic",
        task_type="continuity",
        status="processing",
        payload_={
            "conversation_id": conversation_id,
            "character_id": character_id,
            "source_message_id": "msg_effect_source",
            "character_message_id": "msg_effect_reply",
            "counterpart_type": "user",
            "counterpart_id": "user_001",
        },
        attempt_count=1,
        lease_owner="worker-effect",
        lease_expires_at=datetime.now(timezone.utc) + timedelta(seconds=60),
    )
    session.add(task)
    session.commit()
    session.exec(text(
        "CREATE TRIGGER fail_post_commit_completion "
        "BEFORE UPDATE ON post_commit_tasks "
        "WHEN OLD.id = 'pct_effect_atomic' AND NEW.status = 'completed' "
        "BEGIN SELECT RAISE(ABORT, 'forced task completion failure'); END"
    ))
    session.commit()

    with pytest.raises(IntegrityError, match="forced task completion failure"):
        asyncio.run(_execute_claimed_post_commit_task(session, task, lease_owner="worker-effect"))
    session.rollback()
    session.expire_all()

    relationship = session.get(
        ConversationRelationshipState,
        (conversation_id, character_id, "user", "user_001"),
    )
    refreshed_task = session.get(PostCommitTask, task.id)
    assert relationship is None
    assert refreshed_task is not None and refreshed_task.status == "processing"
