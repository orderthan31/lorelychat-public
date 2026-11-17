"""Durable generation and post-commit jobs: leases, receipts and atomic finalization."""

from uuid import uuid4
from datetime import datetime, timedelta, timezone
from sqlmodel import Session, select
from sqlalchemy import text, update as sa_update
from app.db.models import GenerationJobEvent, MessageGenerationJob, PostCommitTask, Message
from app.services.conversation_memory_policy import (
    compact_text,
)


def sortable_datetime(value: datetime | None) -> datetime:
    if value is None:
        return datetime.min.replace(tzinfo=timezone.utc)
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def append_generation_job_event(
    session: Session,
    job: MessageGenerationJob,
    status: str,
    *,
    payload: dict | None = None,
    state_version: int | None = None,
) -> GenerationJobEvent:
    event = GenerationJobEvent(
        job_id=job.id,
        conversation_id=job.conversation_id,
        status=status,
        state_version=job.state_version if state_version is None else int(state_version),
        payload_=dict(payload or {}),
    )
    session.add(event)
    return event


def create_message_generation_job(session: Session, conversation_id: str, incoming_message_id: str) -> MessageGenerationJob:
    job = MessageGenerationJob(
        id=f"job_{uuid4().hex[:12]}",
        conversation_id=conversation_id,
        incoming_message_id=incoming_message_id,
        status="queued",
    )
    session.add(job)
    append_generation_job_event(session, job, "queued")
    session.commit()
    session.refresh(job)
    return job


def get_message_generation_job_for_incoming_message(session: Session, incoming_message_id: str) -> MessageGenerationJob | None:
    return session.exec(
        select(MessageGenerationJob)
        .where(MessageGenerationJob.incoming_message_id == incoming_message_id)
        .order_by(MessageGenerationJob.created_at.desc())
        .limit(1)
    ).first()


def find_incoming_message_by_client_request_id(session: Session, conversation_id: str, client_request_id: str | None) -> Message | None:
    if not client_request_id:
        return None
    # Legacy sources predate the acceptance table. Query their JSON identity in
    # the database, across every speaker and the full room history, without
    # materializing private historical prose into Python during acceptance.
    return session.exec(select(Message).where(
        Message.conversation_id == conversation_id,
        text("json_extract(messages.metadata, '$.client_request_id') = :client_request_id"),
    ).params(client_request_id=client_request_id).order_by(Message.created_at.desc(), Message.id.desc()).limit(1)).first()


def requeue_expired_message_generation_jobs(
    session: Session,
    *,
    now: datetime | None = None,
    execution_mode: str | None = None,
) -> int:
    current_time = now or datetime.now(timezone.utc)
    jobs = session.exec(select(MessageGenerationJob).where(
        ((MessageGenerationJob.status == "running") &
         (MessageGenerationJob.lease_expires_at.is_(None) | (MessageGenerationJob.lease_expires_at <= current_time))) |
        ((MessageGenerationJob.status == "queued") & MessageGenerationJob.cancel_requested_at.is_not(None)),
        (MessageGenerationJob.execution_options["execution_mode"].as_string() == execution_mode) if execution_mode is not None else True,
    ).order_by(MessageGenerationJob.created_at, MessageGenerationJob.id).limit(100)).all()
    recovered = 0
    for job in jobs:
        if job.status == "queued" and job.cancel_requested_at is None:
            continue
        if job.status == "running" and job.lease_expires_at is not None and sortable_datetime(job.lease_expires_at) > sortable_datetime(current_time):
            continue
        cancelled = job.cancel_requested_at is not None
        predicate = [MessageGenerationJob.id == job.id,
                     MessageGenerationJob.status == job.status,
                     MessageGenerationJob.state_version == job.state_version]
        if job.status == "running":
            predicate.append((MessageGenerationJob.lease_expires_at.is_(None)) | (MessageGenerationJob.lease_expires_at <= current_time))
        result = session.exec(sa_update(MessageGenerationJob).where(*predicate).values(
            status="cancelled" if cancelled else "queued", lease_owner=None,
            lease_expires_at=None, heartbeat_at=None, updated_at=current_time,
            completed_at=current_time if cancelled else None,
            state_version=MessageGenerationJob.state_version + 1,
        ).execution_options(synchronize_session=False))
        if result.rowcount == 1:
            append_generation_job_event(session, job, "cancelled" if cancelled else "requeued",
                payload={"reason": "expired_lease"}, state_version=job.state_version + 1)
            recovered += 1
    if recovered:
        session.commit()
        session.expire_all()
    else:
        session.rollback()
    return recovered


def mark_stale_message_generation_jobs_failed(session: Session, *, older_than_minutes: int = 30) -> int:
    # Backward-compatible startup hook: unfinished running jobs are now recovered,
    # not terminally failed. Queued jobs remain available to the durable dispatcher.
    return requeue_expired_message_generation_jobs(session)


def get_message_generation_job(session: Session, job_id: str) -> MessageGenerationJob | None:
    return session.get(MessageGenerationJob, job_id)


def claim_next_message_generation_job(
    session: Session,
    *,
    lease_owner: str,
    lease_seconds: int = 180,
    now: datetime | None = None,
    job_id: str | None = None,
    execution_mode: str | None = None,
) -> MessageGenerationJob | None:
    current_time = now or datetime.now(timezone.utc)
    lease_until = current_time + timedelta(seconds=max(5, int(lease_seconds)))
    # Correlated eligibility excludes blocked rooms IN SQL before applying a
    # limit. A long blocked prefix cannot hide an independent runnable room.
    table = MessageGenerationJob.__table__
    other = table.alias("earlier_room_job")
    blocker = select(other.c.id).where(
        other.c.conversation_id == table.c.conversation_id,
        other.c.id != table.c.id,
        (other.c.status == "running") | (
            (other.c.status == "queued") & other.c.cancel_requested_at.is_(None) & (
                (other.c.created_at < table.c.created_at) |
                ((other.c.created_at == table.c.created_at) & (other.c.id < table.c.id))
            )
        ),
    ).exists()
    from sqlalchemy import func
    running_table = table.alias("all_running_jobs")
    running_count = select(func.count()).select_from(running_table).where(running_table.c.status == "running").scalar_subquery()
    eligible = (~blocker) & (running_count < 2)
    identity_filter = (table.c.id == job_id) if job_id is not None else True
    # The default durable dispatcher is real-intended. Mock acceptance is an
    # immutable execution scope, never permission for a paid send after restart.
    mode_value = MessageGenerationJob.execution_options["execution_mode"].as_string()
    mode_filter = (mode_value == execution_mode) if execution_mode is not None else (func.coalesce(mode_value, "") != "mock")
    candidates = session.exec(select(MessageGenerationJob).where(
        MessageGenerationJob.status == "queued",
        MessageGenerationJob.cancel_requested_at.is_(None), eligible, identity_filter, mode_filter,
    ).order_by(MessageGenerationJob.created_at, MessageGenerationJob.id).limit(32)).all()
    for candidate in candidates:
        result = session.exec(
            sa_update(MessageGenerationJob)
            .where(
                MessageGenerationJob.id == candidate.id,
                eligible,
                mode_filter,
                MessageGenerationJob.status == "queued",
                MessageGenerationJob.cancel_requested_at.is_(None),
            )
            .values(
                status="running",
                lease_owner=lease_owner,
                lease_expires_at=lease_until,
                heartbeat_at=current_time,
                attempt_count=MessageGenerationJob.attempt_count + 1,
                state_version=MessageGenerationJob.state_version + 1,
                updated_at=current_time,
                error_message=None,
                completed_at=None,
            )
            .execution_options(synchronize_session=False)
        )
        if result.rowcount != 1:
            session.rollback()
            continue
        append_generation_job_event(
            session,
            candidate,
            "running",
            payload={"lease_owner": lease_owner},
            state_version=candidate.state_version + 1,
        )
        session.commit()
        session.expire_all()
        return session.get(MessageGenerationJob, candidate.id)
    return None


def heartbeat_message_generation_job(
    session: Session,
    job_id: str,
    *,
    lease_owner: str,
    lease_seconds: int = 180,
    now: datetime | None = None,
) -> bool:
    current_time = now or datetime.now(timezone.utc)
    result = session.exec(
        sa_update(MessageGenerationJob)
        .where(
            MessageGenerationJob.id == job_id,
            MessageGenerationJob.status == "running",
            MessageGenerationJob.lease_owner == lease_owner,
            MessageGenerationJob.lease_expires_at.is_(None) | (MessageGenerationJob.lease_expires_at > current_time),
        )
        .values(
            heartbeat_at=current_time,
            lease_expires_at=current_time + timedelta(seconds=max(5, int(lease_seconds))),
            updated_at=current_time,
            state_version=MessageGenerationJob.state_version + 1,
        )
        .execution_options(synchronize_session=False)
    )
    session.commit()
    return result.rowcount == 1


def is_message_generation_job_cancel_requested(session: Session, job_id: str) -> bool:
    job = session.get(MessageGenerationJob, job_id)
    return bool(job and (job.cancel_requested_at is not None or job.status == "cancelled"))


def request_message_generation_job_cancel(
    session: Session,
    job: MessageGenerationJob,
    *,
    now: datetime | None = None,
) -> MessageGenerationJob:
    current_time = now or datetime.now(timezone.utc)
    with session.no_autoflush:
        result = session.exec(sa_update(MessageGenerationJob).where(
            MessageGenerationJob.id == job.id, MessageGenerationJob.status.in_(["queued", "running"]),
            MessageGenerationJob.cancel_requested_at.is_(None),
        ).values(cancel_requested_at=current_time, updated_at=current_time,
                 state_version=MessageGenerationJob.state_version + 1)
          .execution_options(synchronize_session=False))
    if result.rowcount != 1:
        session.rollback()
        session.refresh(job)
        return job
    session.refresh(job)
    event_status = "cancel_requested"
    if job.status == "queued":
        job.status = "cancelled"
        job.completed_at = current_time
        job.lease_owner = None
        job.lease_expires_at = None
        job.heartbeat_at = None
        session.add(job)
        event_status = "cancelled"
    append_generation_job_event(session, job, event_status)
    session.commit()
    session.refresh(job)
    return job


def _fence_generation_terminal(session: Session, job: MessageGenerationJob, *, allow_cancel: bool = True,
    expected_owner: str | None = None, expected_attempt: int | None = None, allow_unclaimed: bool = False) -> bool:
    owner = job.lease_owner if expected_owner is None and expected_attempt is None else expected_owner
    attempt = job.attempt_count if expected_attempt is None else expected_attempt
    allowed_state = MessageGenerationJob.status == "running"
    if allow_unclaimed and expected_attempt is None:
        # Backward-compatible receipt seeding/completion helper, never used for
        # generation admission. Real runner callers supply captured attempts.
        allowed_state = allowed_state | ((MessageGenerationJob.status == "queued") & MessageGenerationJob.lease_owner.is_(None) & (MessageGenerationJob.attempt_count == 0))
    predicates = [MessageGenerationJob.id == job.id, allowed_state,
                  MessageGenerationJob.lease_owner == owner, MessageGenerationJob.attempt_count == attempt]
    if expected_attempt is not None:
        predicates.append(MessageGenerationJob.lease_expires_at.is_(None) | (MessageGenerationJob.lease_expires_at > datetime.now(timezone.utc)))
    if not allow_cancel:
        predicates.append(MessageGenerationJob.cancel_requested_at.is_(None))
    with session.no_autoflush:
        result = session.exec(sa_update(MessageGenerationJob).where(*predicates)
            .values(updated_at=MessageGenerationJob.updated_at).execution_options(synchronize_session=False))
    if result.rowcount != 1:
        session.rollback()
        session.refresh(job)
        return False
    session.refresh(job)
    return True


def mark_message_generation_job_cancelled(
    session: Session,
    job: MessageGenerationJob,
    *,
    reason: str = "cancel_requested",
) -> MessageGenerationJob:
    if not _fence_generation_terminal(session, job):
        return job
    now = datetime.now(timezone.utc)
    job.status = "cancelled"
    job.completed_at = now
    job.updated_at = now
    job.lease_owner = None
    job.lease_expires_at = None
    job.heartbeat_at = None
    job.state_version += 1
    session.add(job)
    append_generation_job_event(session, job, "cancelled", payload={"reason": reason})
    session.commit()
    session.refresh(job)
    return job


def mark_message_generation_job_running(session: Session, job: MessageGenerationJob) -> MessageGenerationJob:
    now = datetime.now(timezone.utc)
    job.status = "running"
    job.updated_at = now
    job.heartbeat_at = now
    job.state_version += 1
    session.add(job)
    append_generation_job_event(session, job, "running")
    session.commit()
    session.refresh(job)
    return job


def mark_message_generation_job_completed(
    session: Session,
    job: MessageGenerationJob,
    generated_message_ids: list[str],
    *,
    commit: bool = True,
    expected_owner: str | None = None,
    expected_attempt: int | None = None,
) -> MessageGenerationJob:
    if not _fence_generation_terminal(session, job, allow_cancel=False, expected_owner=expected_owner, expected_attempt=expected_attempt, allow_unclaimed=commit):
        return job
    now = datetime.now(timezone.utc)
    job.status = "completed"
    job.error_message = None
    job.generated_message_ids = generated_message_ids
    job.updated_at = now
    job.completed_at = now
    job.lease_owner = None
    job.lease_expires_at = None
    job.heartbeat_at = None
    job.state_version += 1
    session.add(job)
    append_generation_job_event(session, job, "completed", payload={"generated_message_count": len(generated_message_ids)})
    if commit:
        session.commit()
        session.refresh(job)
    return job


def enqueue_post_commit_task(
    session: Session,
    *,
    unique_key: str,
    task_type: str,
    payload: dict,
) -> PostCommitTask:
    existing = session.exec(
        select(PostCommitTask).where(PostCommitTask.unique_key == unique_key)
    ).first()
    if existing:
        return existing
    task = PostCommitTask(
        id=f"pct_{uuid4().hex[:12]}",
        unique_key=unique_key,
        task_type=task_type,
        status="queued",
        payload_=dict(payload),
    )
    session.add(task)
    return task


def requeue_expired_post_commit_tasks(
    session: Session,
    *,
    now: datetime | None = None,
) -> int:
    current_time = now or datetime.now(timezone.utc)
    result = session.exec(sa_update(PostCommitTask).where(
        PostCommitTask.status == "processing",
        PostCommitTask.lease_expires_at.is_(None) | (PostCommitTask.lease_expires_at <= current_time),
    ).values(status="queued", lease_owner=None, lease_expires_at=None,
             available_at=current_time, updated_at=current_time).execution_options(synchronize_session=False))
    session.commit()
    session.expire_all()
    return result.rowcount


def claim_next_post_commit_task(
    session: Session,
    *,
    lease_owner: str,
    lease_seconds: int = 180,
    now: datetime | None = None,
) -> PostCommitTask | None:
    current_time = now or datetime.now(timezone.utc)
    lease_until = current_time + timedelta(seconds=max(5, int(lease_seconds)))
    candidates = session.exec(
        select(PostCommitTask)
        .where(
            PostCommitTask.status == "queued",
            PostCommitTask.available_at <= current_time,
        )
        .order_by(PostCommitTask.available_at, PostCommitTask.created_at, PostCommitTask.id)
        .limit(50)
    ).all()
    for candidate in candidates:
        result = session.exec(
            sa_update(PostCommitTask)
            .where(
                PostCommitTask.id == candidate.id,
                PostCommitTask.status == "queued",
                PostCommitTask.available_at <= current_time,
            )
            .values(
                status="processing",
                lease_owner=lease_owner,
                lease_expires_at=lease_until,
                attempt_count=PostCommitTask.attempt_count + 1,
                last_error=None,
                updated_at=current_time,
            )
            .execution_options(synchronize_session=False)
        )
        if result.rowcount != 1:
            session.rollback()
            continue
        session.commit()
        session.expire_all()
        return session.get(PostCommitTask, candidate.id)
    return None


def claim_post_commit_task_by_unique_key(
    session: Session,
    unique_key: str,
    *,
    lease_owner: str,
    lease_seconds: int = 180,
    now: datetime | None = None,
) -> PostCommitTask | None:
    current_time = now or datetime.now(timezone.utc)
    result = session.exec(
        sa_update(PostCommitTask)
        .where(
            PostCommitTask.unique_key == unique_key,
            PostCommitTask.status == "queued",
            PostCommitTask.available_at <= current_time,
        )
        .values(
            status="processing",
            lease_owner=lease_owner,
            lease_expires_at=current_time + timedelta(seconds=max(5, int(lease_seconds))),
            attempt_count=PostCommitTask.attempt_count + 1,
            last_error=None,
            updated_at=current_time,
        )
        .execution_options(synchronize_session=False)
    )
    session.commit()
    if result.rowcount != 1:
        return None
    session.expire_all()
    return session.exec(select(PostCommitTask).where(PostCommitTask.unique_key == unique_key)).first()


def heartbeat_post_commit_task(
    session: Session,
    task_id: str,
    *,
    lease_owner: str,
    lease_seconds: int = 180,
    now: datetime | None = None,
) -> bool:
    current_time = now or datetime.now(timezone.utc)
    result = session.exec(
        sa_update(PostCommitTask)
        .where(
            PostCommitTask.id == task_id,
            PostCommitTask.status == "processing",
            PostCommitTask.lease_owner == lease_owner,
            PostCommitTask.lease_expires_at.is_(None) | (PostCommitTask.lease_expires_at > current_time),
        )
        .values(
            lease_expires_at=current_time + timedelta(seconds=max(5, int(lease_seconds))),
            updated_at=current_time,
        )
        .execution_options(synchronize_session=False)
    )
    session.commit()
    return result.rowcount == 1


def mark_post_commit_task_completed(
    session: Session,
    task: PostCommitTask,
    *,
    lease_owner: str,
    commit: bool = True,
    expected_attempt: int | None = None,
) -> bool:
    now = datetime.now(timezone.utc)
    with session.no_autoflush:
        result = session.exec(sa_update(PostCommitTask).where(
            PostCommitTask.id == task.id, PostCommitTask.status == "processing",
            PostCommitTask.lease_owner == lease_owner,
            PostCommitTask.attempt_count == (task.attempt_count if expected_attempt is None else expected_attempt),
        ).values(status="completed", lease_owner=None, lease_expires_at=None,
                 last_error=None, completed_at=now, updated_at=now)
          .execution_options(synchronize_session=False))
    if result.rowcount != 1:
        session.rollback()  # Roll back staged DB effects as well as the ack.
        return False
    if commit:
        session.commit()
        session.refresh(task)
    return True


def mark_post_commit_task_failed(
    session: Session,
    task: PostCommitTask,
    exc: Exception,
    *,
    lease_owner: str,
    max_attempts: int = 5,
    now: datetime | None = None,
) -> bool:
    current_time = now or datetime.now(timezone.utc)
    terminal = task.attempt_count >= max(1, int(max_attempts))
    with session.no_autoflush:
        result = session.exec(sa_update(PostCommitTask).where(
            PostCommitTask.id == task.id, PostCommitTask.status == "processing",
            PostCommitTask.lease_owner == lease_owner,
            PostCommitTask.attempt_count == task.attempt_count,
        ).values(status="failed" if terminal else "queued", lease_owner=None,
                 lease_expires_at=None, last_error=compact_text(f"{type(exc).__name__}: {exc}", 1000),
                 available_at=current_time if terminal else current_time + timedelta(seconds=min(300, 2 ** max(1, task.attempt_count))),
                 updated_at=current_time, completed_at=current_time if terminal else None)
          .execution_options(synchronize_session=False))
    if result.rowcount != 1:
        session.rollback()
        return False
    session.commit()
    session.refresh(task)
    return True


class GenerationReceiptDeleted(ValueError):
    code = "generation_receipt_deleted"


class GenerationReceiptCorrupt(ValueError):
    code = "generation_receipt_inconsistent"


def finalize_message_generation_turn(
    session: Session,
    job: MessageGenerationJob | None,
    generated_messages: list[Message],
    *,
    source_message_id: str | None = None,
    source_metadata: dict | None = None,
    post_commit_tasks: list[dict] | None = None,
    expected_owner: str | None = None,
    expected_attempt: int | None = None,
) -> list[Message]:
    try:
        if job is not None:
            enforce_lease_expiry = expected_attempt is not None
            expected_owner = job.lease_owner if expected_owner is None and expected_attempt is None else expected_owner
            expected_attempt = job.attempt_count if expected_attempt is None else expected_attempt
            # A no-op UPDATE obtains the write boundary before reading metadata,
            # fencing cancellations/owner transfer with the same DB transaction.
            with session.no_autoflush:
                result = session.exec(sa_update(MessageGenerationJob).where(
                    MessageGenerationJob.id == job.id,
                    MessageGenerationJob.status == "running",
                    MessageGenerationJob.lease_owner == expected_owner,
                    MessageGenerationJob.attempt_count == expected_attempt,
                    (MessageGenerationJob.lease_expires_at.is_(None) | (MessageGenerationJob.lease_expires_at > datetime.now(timezone.utc))) if enforce_lease_expiry else True,
                    MessageGenerationJob.cancel_requested_at.is_(None),
                ).values(updated_at=MessageGenerationJob.updated_at)
                  .execution_options(synchronize_session=False))
            session.refresh(job)
            existing_messages = list(session.exec(select(Message).where(
                Message.generation_job_id == job.id,
            ).order_by(Message.reply_index)).all())
            if job.status == "completed":
                expected = list(job.generated_message_ids or [])
                if ([m.id for m in existing_messages] != expected or
                    [m.reply_index for m in existing_messages] != list(range(len(expected))) or not expected):
                    from app.db.models import GenerationReplyDeletion
                    deleted = set(session.exec(select(GenerationReplyDeletion.message_id).where(GenerationReplyDeletion.job_id == job.id)).all())
                    surviving = [mid for mid in expected if mid not in deleted]
                    if deleted and [m.id for m in existing_messages] == surviving and all(m.reply_index == expected.index(m.id) for m in existing_messages):
                        raise GenerationReceiptDeleted("generation receipt includes intentional user deletions; replay never restores content")
                    raise GenerationReceiptCorrupt("generation receipt is inconsistent; replay never restores content")
                session.rollback()
                return existing_messages
            if result.rowcount != 1:
                raise ValueError("generation ownership fence rejected finalization")
            if existing_messages or job.generated_message_ids:
                raise ValueError("generation receipt exists without completed state")
            if not generated_messages:
                raise ValueError("generation receipt cannot be empty")

        if source_message_id is not None and source_metadata is not None:
            source_message = session.get(Message, source_message_id)
            if not source_message:
                raise ValueError(f"Source message '{source_message_id}' was not found during finalization")
            current_metadata = dict(source_message.metadata_ or {})
            current_metadata.update({key: value for key, value in source_metadata.items()
                                     if key.startswith(("generation_", "command_postprocess_"))})
            source_message.metadata_ = current_metadata
            session.add(source_message)

        for reply_index, message in enumerate(generated_messages):
            if job is not None:
                message.generation_job_id = job.id
                message.reply_index = reply_index
            session.add(message)
        for task_spec in post_commit_tasks or []:
            enqueue_post_commit_task(
                session,
                unique_key=str(task_spec["unique_key"]),
                task_type=str(task_spec["task_type"]),
                payload=dict(task_spec.get("payload") or {}),
            )
        if job is not None:
            mark_message_generation_job_completed(
                session,
                job,
                [message.id for message in generated_messages],
                commit=False,
            )
        session.commit()
    except Exception:
        session.rollback()
        raise
    for message in generated_messages:
        session.refresh(message)
    if job is not None:
        session.refresh(job)
    return generated_messages


def mark_message_generation_job_failed(session: Session, job: MessageGenerationJob, exc: Exception,
    *, expected_owner: str | None = None, expected_attempt: int | None = None) -> MessageGenerationJob:
    if not _fence_generation_terminal(session, job, expected_owner=expected_owner, expected_attempt=expected_attempt, allow_unclaimed=True):
        return job
    if job.cancel_requested_at is not None:
        return mark_message_generation_job_cancelled(session, job)
    source = session.get(Message, job.incoming_message_id)
    if source is not None:
        source.metadata_ = {**(source.metadata_ or {}), "generation_status": "failed",
            "generation_error_type": type(exc).__name__, "generation_error_message": compact_text(str(exc), 1000),
            **({"generation_error_code": exc.code} if isinstance(getattr(exc, "code", None), str) else {})}
        session.add(source)
    if source is not None and (source.metadata_ or {}).get("command_postprocess_status") == "pending":
        source.metadata_ = {
            **(source.metadata_ or {}),
            "command_postprocess_status": "skipped_generation_failed",
            "command_postprocess_error_code": "generation_failed",
        }
        session.add(source)
    now = datetime.now(timezone.utc)
    job.status = "failed"
    job.error_message = compact_text(str(exc), limit=1000)
    job.updated_at = now
    job.completed_at = now
    job.lease_owner = None
    job.lease_expires_at = None
    job.heartbeat_at = None
    job.state_version += 1
    session.add(job)
    failure_payload = {"error_type": type(exc).__name__}
    error_code = getattr(exc, "code", None)
    if isinstance(error_code, str) and error_code:
        failure_payload["error_code"] = error_code
    append_generation_job_event(
        session,
        job,
        "failed",
        payload=failure_payload,
    )
    session.commit()
    session.refresh(job)
    return job
