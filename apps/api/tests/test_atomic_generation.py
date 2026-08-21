from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.db.models import GenerationJobEvent, Message, MessageGenerationJob, PostCommitTask
from app.services import conversation_service


def _running_job(session: Session, job_id: str = "job_atomic") -> MessageGenerationJob:
    job = MessageGenerationJob(
        id=job_id,
        conversation_id="conv_atomic",
        incoming_message_id="msg_source",
        status="running",
        lease_owner="worker-atomic",
        lease_expires_at=datetime.now(timezone.utc),
    )
    session.add(job)
    session.commit()
    return job


def _generated(content: str, speaker_id: str = "char_aria") -> Message:
    return Message(
        id=f"msg_{content.lower()}",
        conversation_id="conv_atomic",
        speaker_type="character",
        speaker_id=speaker_id,
        content=content,
    )


def test_generated_turn_rolls_back_every_bubble_and_job_state_on_insert_failure(session):
    job = _running_job(session)
    session.exec(text(
        "CREATE TRIGGER fail_second_atomic_reply "
        "BEFORE INSERT ON messages "
        "WHEN NEW.generation_job_id = 'job_atomic' AND NEW.reply_index = 1 "
        "BEGIN SELECT RAISE(ABORT, 'forced second bubble failure'); END"
    ))
    session.commit()

    with pytest.raises(IntegrityError, match="forced second bubble failure"):
        conversation_service.finalize_message_generation_turn(
            session,
            job,
            [_generated("FIRST"), _generated("SECOND")],
            post_commit_tasks=[{
                "unique_key": "job_atomic:asset:0",
                "task_type": "asset",
                "payload": {"message_id": "msg_first"},
            }],
        )

    session.expire_all()
    messages = session.exec(
        select(Message).where(Message.generation_job_id == job.id)
    ).all()
    refreshed_job = session.get(MessageGenerationJob, job.id)
    tasks = session.exec(select(PostCommitTask)).all()
    completed_events = session.exec(
        select(GenerationJobEvent).where(
            GenerationJobEvent.job_id == job.id,
            GenerationJobEvent.status == "completed",
        )
    ).all()

    assert messages == []
    assert refreshed_job is not None and refreshed_job.status == "running"
    assert refreshed_job.generated_message_ids == []
    assert tasks == []
    assert completed_events == []


def test_generated_turn_completion_is_idempotent_by_job_and_reply_index(session):
    job = _running_job(session, "job_replay")
    tasks = [{
        "unique_key": "job_replay:asset:0",
        "task_type": "asset",
        "payload": {"message_id": "msg_original"},
    }]

    first = conversation_service.finalize_message_generation_turn(
        session,
        job,
        [_generated("ORIGINAL")],
        post_commit_tasks=tasks,
    )
    session.expire_all()
    completed = session.get(MessageGenerationJob, job.id)
    assert completed is not None

    replay = conversation_service.finalize_message_generation_turn(
        session,
        completed,
        [_generated("DUPLICATE")],
        post_commit_tasks=tasks,
    )

    persisted = session.exec(
        select(Message)
        .where(Message.generation_job_id == job.id)
        .order_by(Message.reply_index)
    ).all()
    persisted_tasks = session.exec(select(PostCommitTask)).all()
    completed_events = session.exec(
        select(GenerationJobEvent).where(
            GenerationJobEvent.job_id == job.id,
            GenerationJobEvent.status == "completed",
        )
    ).all()

    assert [message.content for message in first] == ["ORIGINAL"]
    assert [message.content for message in replay] == ["ORIGINAL"]
    assert [(message.reply_index, message.content) for message in persisted] == [(0, "ORIGINAL")]
    assert len(persisted_tasks) == 1
    assert len(completed_events) == 1
