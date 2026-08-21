from __future__ import annotations

from datetime import datetime, timedelta, timezone
import threading

from sqlmodel import Session, SQLModel, select

from app.db.models import GenerationJobEvent, MessageGenerationJob
from app.db.session import configure_sqlite_runtime, make_engine
from app.services import conversation_service
from app.services.generation_dispatcher import GenerationDispatcher


def _job(
    session: Session,
    job_id: str,
    conversation_id: str,
    *,
    status: str = "queued",
    created_at: datetime | None = None,
    lease_owner: str | None = None,
    lease_expires_at: datetime | None = None,
) -> MessageGenerationJob:
    row = MessageGenerationJob(
        id=job_id,
        conversation_id=conversation_id,
        incoming_message_id=f"msg_{job_id}",
        status=status,
        created_at=created_at or datetime.now(timezone.utc),
        updated_at=created_at or datetime.now(timezone.utc),
        lease_owner=lease_owner,
        lease_expires_at=lease_expires_at,
    )
    session.add(row)
    session.commit()
    return row


def test_claim_is_compare_and_set_and_preserves_conversation_order(session):
    now = datetime.now(timezone.utc)
    first = _job(session, "job_first", "conv_same", created_at=now - timedelta(seconds=2))
    _job(session, "job_second", "conv_same", created_at=now - timedelta(seconds=1))
    other = _job(session, "job_other", "conv_other", created_at=now)

    claimed_first = conversation_service.claim_next_message_generation_job(
        session,
        lease_owner="worker-a",
        lease_seconds=60,
        now=now,
    )
    claimed_other = conversation_service.claim_next_message_generation_job(
        session,
        lease_owner="worker-b",
        lease_seconds=60,
        now=now,
    )
    no_more = conversation_service.claim_next_message_generation_job(
        session,
        lease_owner="worker-c",
        lease_seconds=60,
        now=now,
    )

    assert claimed_first is not None and claimed_first.id == first.id
    assert claimed_first.status == "running"
    assert claimed_first.lease_owner == "worker-a"
    assert claimed_first.attempt_count == 1
    assert claimed_other is not None and claimed_other.id == other.id
    assert no_more is None

    events = session.exec(select(GenerationJobEvent).order_by(GenerationJobEvent.id)).all()
    assert [(event.job_id, event.status) for event in events] == [
        ("job_first", "running"),
        ("job_other", "running"),
    ]


def test_heartbeat_requires_current_lease_owner(session):
    now = datetime.now(timezone.utc)
    _job(session, "job_heartbeat", "conv_h", status="queued", created_at=now)
    claimed = conversation_service.claim_next_message_generation_job(
        session, lease_owner="worker-a", lease_seconds=10, now=now
    )
    assert claimed is not None

    assert conversation_service.heartbeat_message_generation_job(
        session,
        claimed.id,
        lease_owner="wrong-worker",
        lease_seconds=60,
        now=now + timedelta(seconds=1),
    ) is False
    assert conversation_service.heartbeat_message_generation_job(
        session,
        claimed.id,
        lease_owner="worker-a",
        lease_seconds=60,
        now=now + timedelta(seconds=1),
    ) is True

    session.expire_all()
    refreshed = session.get(MessageGenerationJob, claimed.id)
    assert refreshed is not None
    assert refreshed.heartbeat_at is not None
    assert refreshed.state_version == 2


def test_expired_running_jobs_requeue_and_can_be_reclaimed(session):
    now = datetime.now(timezone.utc)
    expired = _job(
        session,
        "job_expired",
        "conv_expired",
        status="running",
        created_at=now - timedelta(minutes=2),
        lease_owner="dead-worker",
        lease_expires_at=now - timedelta(seconds=1),
    )

    recovered = conversation_service.requeue_expired_message_generation_jobs(session, now=now)
    assert recovered == 1
    session.expire_all()
    refreshed = session.get(MessageGenerationJob, expired.id)
    assert refreshed is not None
    assert refreshed.status == "queued"
    assert refreshed.lease_owner is None

    claimed = conversation_service.claim_next_message_generation_job(
        session, lease_owner="new-worker", lease_seconds=60, now=now
    )
    assert claimed is not None and claimed.id == expired.id
    assert claimed.attempt_count == 1

    statuses = [
        event.status
        for event in session.exec(
            select(GenerationJobEvent)
            .where(GenerationJobEvent.job_id == expired.id)
            .order_by(GenerationJobEvent.id)
        ).all()
    ]
    assert statuses == ["requeued", "running"]


def test_cancel_queued_job_is_terminal_and_running_job_is_requested(session):
    now = datetime.now(timezone.utc)
    queued = _job(session, "job_cancel_queued", "conv_q", created_at=now)
    running = _job(
        session,
        "job_cancel_running",
        "conv_r",
        status="running",
        created_at=now,
        lease_owner="worker-r",
        lease_expires_at=now + timedelta(seconds=60),
    )

    cancelled = conversation_service.request_message_generation_job_cancel(session, queued, now=now)
    requested = conversation_service.request_message_generation_job_cancel(session, running, now=now)

    assert cancelled.status == "cancelled"
    assert cancelled.completed_at is not None
    assert requested.status == "running"
    assert requested.cancel_requested_at is not None
    assert conversation_service.is_message_generation_job_cancel_requested(session, requested.id) is True

    events = session.exec(select(GenerationJobEvent).order_by(GenerationJobEvent.id)).all()
    assert [(event.job_id, event.status) for event in events] == [
        ("job_cancel_queued", "cancelled"),
        ("job_cancel_running", "cancel_requested"),
    ]


def test_supervised_dispatcher_claims_heartbeats_and_finishes_job(tmp_path):
    test_engine = make_engine(f"sqlite:///{tmp_path / 'dispatcher.db'}")
    configure_sqlite_runtime(test_engine)
    SQLModel.metadata.create_all(test_engine)
    finished = threading.Event()

    with Session(test_engine) as setup_session:
        job = _job(setup_session, "job_supervised", "conv_supervised")
        job_id = job.id

    def runner(job_id: str, lease_owner: str) -> None:
        with Session(test_engine) as worker_session:
            running = worker_session.get(MessageGenerationJob, job_id)
            assert running is not None
            assert running.status == "running"
            assert running.lease_owner == lease_owner
            conversation_service.mark_message_generation_job_completed(
                worker_session,
                running,
                ["msg_generated"],
            )
        finished.set()

    dispatcher = GenerationDispatcher(
        test_engine,
        max_workers=1,
        poll_interval_seconds=0.05,
        lease_seconds=10,
        heartbeat_interval_seconds=0.05,
    )
    dispatcher.start(runner)
    dispatcher.wake()
    try:
        assert finished.wait(3.0)
    finally:
        dispatcher.stop()

    with Session(test_engine) as check_session:
        completed = check_session.get(MessageGenerationJob, job_id)
        assert completed is not None
        assert completed.status == "completed"
        assert completed.generated_message_ids == ["msg_generated"]
        assert completed.lease_owner is None
        statuses = [
            event.status
            for event in check_session.exec(
                select(GenerationJobEvent)
                .where(GenerationJobEvent.job_id == job_id)
                .order_by(GenerationJobEvent.id)
            ).all()
        ]
        assert statuses == ["running", "completed"]
