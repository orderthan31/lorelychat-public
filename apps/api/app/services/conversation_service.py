import json
import logging
import re
from collections import Counter
from collections.abc import Callable, Mapping
from typing import Any
from uuid import uuid4
from datetime import datetime, timedelta, timezone
from app.engine.llm_client import LLMClient, LLMUnavailableError, chat_with_optional_conversation_id
from app.engine import prompts
from sqlmodel import Session, select, delete
from sqlalchemy import text, update as sa_update
from app.db.models import Character, Conversation, ConversationParticipant, ConversationReadState, GenerationJobEvent, MessageGenerationJob, PostCommitTask, ConversationRelationshipState, CharacterMemory, Message, SceneState, MessageAsset, BattleMatchRecord, BattleStanding, RuntimeSetting, WorldSetting
from app.schemas.conversations import ConversationCreate, ConversationUpdate, MessageCreate, ParticipantCreate
from app.services import external_memory_service, genre_domain_service, system_prompt_service
from app.services.context_management_service import (
    AutomaticContextPlan,
    ContextCapacity,
    build_automatic_context_plan,
    group_complete_turns,
    select_raw_tail_groups,
)


MAX_SCENE_MEMORY_CHARS = 2200
MAX_SCENE_MEMORY_BULLETS = 18
MAX_SCENE_MEMORY_BULLET_CHARS = 180
MAX_CHARACTER_MEMORY_ITEMS = 3
MAX_RELATIONSHIP_CONTEXT_ITEMS = 2
MAX_CONTEXT_MEMORY_SCAN_ITEMS = 8
COMMON_ROOM_MEMORY_CHARACTER_ID = "__room__"
GLOBAL_CHARACTER_MEMORY_CONVERSATION_ID = "__global__"
VALID_GENRE_MODES = {"battle", "romance", "fantasy", "slice_of_life", "mystery", "custom"}
DEFAULT_GENRE_MODE = "battle"
DEFAULT_USER_ID = "user_001"

logger = logging.getLogger(__name__)


def compression_error_summary(exc: BaseException) -> str:
    text = compact_text(str(exc) or type(exc).__name__, 500)
    text = re.sub(r"key=[^\s&]+", "key=[REDACTED]", text, flags=re.IGNORECASE)
    text = re.sub(r"API key[^.;\n]*", "API key error", text, flags=re.IGNORECASE)
    return compact_text(f"{type(exc).__name__}: {text}", 500)


def normalize_genre_mode(value: str | None) -> str:
    cleaned = (value or DEFAULT_GENRE_MODE).strip().lower().replace("-", "_")
    return cleaned if cleaned in VALID_GENRE_MODES else DEFAULT_GENRE_MODE


def sortable_datetime(value: datetime | None) -> datetime:
    if value is None:
        return datetime.min.replace(tzinfo=timezone.utc)
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def build_default_title(session: Session, payload: ConversationCreate) -> str:
    title = (payload.title or "").strip()
    if title:
        return title
    character_names = []
    for participant in sorted(payload.participants, key=lambda item: item.order_index if item.order_index is not None else 999):
        if participant.type != "character":
            continue
        character = session.get(Character, participant.id)
        character_names.append(character.name if character else participant.id)
    if payload.mode == "character_character" and len(character_names) >= 2:
        return f"{character_names[0]}-{character_names[1]} 자동대화"
    if character_names:
        return f"{character_names[0]} 대화방"
    return "새 대화방"


def first_character_participant_id(participants: list[ParticipantCreate]) -> str | None:
    ordered = sorted(participants, key=lambda item: item.order_index if item.order_index is not None else 999)
    for participant in ordered:
        if participant.type == "character" and participant.id:
            return participant.id
    return None


def scene_payload_from_world_setting(world: WorldSetting | None, scene: Any | None) -> dict:
    base = {}
    if world:
        base.update({
            "location": world.location,
            "mood": world.mood,
            "world_seed": world.world_seed,
            "opening_scene": world.opening_scene,
            "opening_line": world.opening_line,
            "tone_preset": world.tone_preset,
            "relationship_archetype": world.relationship_archetype,
            "compression_focus": world.compression_focus,
        })
    if scene:
        scene_data = scene.model_dump(exclude={"user_description"})
        for key, value in scene_data.items():
            if value not in (None, ""):
                base[key] = value
    return base


def user_description_from_scene(scene: object | None) -> str:
    return (getattr(scene, "user_description", None) or "").strip()


def opening_line_from_scene_data(scene_data: dict) -> str:
    return (scene_data.get("opening_line") or "").strip()


def create_conversation(session: Session, payload: ConversationCreate) -> Conversation:
    world_setting = session.get(WorldSetting, payload.world_setting_id) if payload.world_setting_id else None
    effective_genre_mode = normalize_genre_mode(world_setting.genre_mode if world_setting else payload.genre_mode)
    scene_data = scene_payload_from_world_setting(world_setting, payload.scene)
    conversation = Conversation(
        id=f"conv_{uuid4().hex[:12]}",
        title=build_default_title(session, payload),
        thumbnail_url=(payload.thumbnail_url or "").strip() or None,
        world_setting_id=world_setting.id if world_setting else None,
        mode=desired_mode_for_participants(payload.participants),
        genre_mode=effective_genre_mode,
        created_by=payload.created_by,
        tts_enabled=payload.tts_enabled,
    )
    session.add(conversation)
    for idx, participant in enumerate(payload.participants):
        session.add(ConversationParticipant(
            conversation_id=conversation.id,
            participant_type=participant.type,
            participant_id=participant.id,
            role=participant.role,
            order_index=participant.order_index if participant.order_index is not None else idx,
        ))
    if scene_data or payload.scene:
        user_description = user_description_from_scene(payload.scene)
        if user_description:
            scene_data["user_description"] = user_description
        session.add(SceneState(conversation_id=conversation.id, **scene_data))
        opening_line = opening_line_from_scene_data(scene_data)
        first_character_id = first_character_participant_id(payload.participants)
        if opening_line and first_character_id:
            session.add(Message(
                id=f"msg_{uuid4().hex[:12]}",
                conversation_id=conversation.id,
                speaker_type="character",
                speaker_id=first_character_id,
                content=opening_line,
                metadata_={"source": "world_setting_opening_line" if world_setting else "room_opening_line"},
            ))
    session.commit()
    session.refresh(conversation)
    return conversation


def list_conversations(session: Session) -> list[Conversation]:
    stmt = select(Conversation).order_by(text("updated_at DESC"), text("created_at DESC"), text("id DESC"))
    return list(session.exec(stmt).all())


def latest_message_for_conversation(session: Session, conversation_id: str) -> Message | None:
    tie_breaker = text("rowid DESC") if session.get_bind().dialect.name == "sqlite" else text("id DESC")
    stmt = (
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(text("created_at DESC"), tie_breaker)
        .limit(1)
    )
    return session.exec(stmt).first()


def unread_messages_for_conversation(session: Session, conversation_id: str, *, user_id: str = DEFAULT_USER_ID) -> list[Message]:
    read_state = session.get(ConversationReadState, (conversation_id, user_id))
    stmt = select(Message).where(Message.conversation_id == conversation_id, Message.speaker_type != "user")
    if read_state and read_state.last_read_at:
        stmt = stmt.where(Message.created_at > read_state.last_read_at)
    return list(session.exec(stmt).all())


def serialize_conversation_for_list(session: Session, conversation: Conversation, *, user_id: str = DEFAULT_USER_ID) -> dict:
    latest = latest_message_for_conversation(session, conversation.id)
    unread_count = len(unread_messages_for_conversation(session, conversation.id, user_id=user_id))
    world_setting = session.get(WorldSetting, conversation.world_setting_id) if conversation.world_setting_id else None
    participants = get_participants(session, conversation.id)
    data = conversation.model_dump()
    data.update({
        # Conversation thumbnail_url is a legacy room-level field.  Room list cards
        # should represent the linked world setting after world settings were split
        # into their own product concept.
        "thumbnail_url": world_setting.thumbnail_url if world_setting else None,
        "participants": [
            {
                "type": participant.participant_type,
                "id": participant.participant_id,
                "role": participant.role,
                "order_index": participant.order_index,
            }
            for participant in participants
        ],
        "last_message_id": latest.id if latest else None,
        "last_message_at": latest.created_at if latest else None,
        "has_unread": unread_count > 0,
        "unread_count": unread_count,
    })
    return data


def mark_conversation_read(session: Session, conversation_id: str, *, user_id: str = DEFAULT_USER_ID) -> ConversationReadState | None:
    conversation = get_conversation(session, conversation_id)
    if not conversation:
        return None
    latest = latest_message_for_conversation(session, conversation_id)
    now = datetime.now(timezone.utc)
    read_state = session.get(ConversationReadState, (conversation_id, user_id))
    if not read_state:
        read_state = ConversationReadState(conversation_id=conversation_id, user_id=user_id, last_read_at=now)
    read_state.last_read_message_id = latest.id if latest else None
    read_state.last_read_at = latest.created_at if latest else now
    session.add(read_state)
    session.commit()
    session.refresh(read_state)
    return read_state


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
    recent_user_messages = session.exec(
        select(Message)
        .where(Message.conversation_id == conversation_id, Message.speaker_type == "user")
        .order_by(Message.created_at.desc())
        .limit(100)
    ).all()
    for message in recent_user_messages:
        metadata = message.metadata_ if isinstance(message.metadata_, dict) else {}
        if metadata.get("client_request_id") == client_request_id:
            return message
    return None


def requeue_expired_message_generation_jobs(
    session: Session,
    *,
    now: datetime | None = None,
) -> int:
    current_time = now or datetime.now(timezone.utc)
    running_jobs = session.exec(
        select(MessageGenerationJob).where(MessageGenerationJob.status == "running")
    ).all()
    recovered = 0
    for job in running_jobs:
        if job.lease_expires_at is not None and sortable_datetime(job.lease_expires_at) > sortable_datetime(current_time):
            continue
        previous_owner = job.lease_owner
        job.status = "queued"
        job.lease_owner = None
        job.lease_expires_at = None
        job.heartbeat_at = None
        job.updated_at = current_time
        job.state_version += 1
        session.add(job)
        append_generation_job_event(
            session,
            job,
            "requeued",
            payload={"reason": "expired_lease", "previous_lease_owner": previous_owner},
        )
        recovered += 1
    if recovered:
        session.commit()
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
) -> MessageGenerationJob | None:
    current_time = now or datetime.now(timezone.utc)
    lease_until = current_time + timedelta(seconds=max(5, int(lease_seconds)))
    candidates = session.exec(
        select(MessageGenerationJob)
        .where(
            MessageGenerationJob.status == "queued",
            MessageGenerationJob.cancel_requested_at.is_(None),
        )
        .order_by(MessageGenerationJob.created_at, MessageGenerationJob.id)
        .limit(100)
    ).all()
    for candidate in candidates:
        active_for_conversation = session.exec(
            select(MessageGenerationJob).where(
                MessageGenerationJob.conversation_id == candidate.conversation_id,
                MessageGenerationJob.status.in_(["queued", "running"]),
            )
        ).all()
        candidate_key = (sortable_datetime(candidate.created_at), candidate.id)
        blocked = any(
            other.id != candidate.id
            and (
                other.status == "running"
                or (
                    other.status == "queued"
                    and (sortable_datetime(other.created_at), other.id) < candidate_key
                )
            )
            for other in active_for_conversation
        )
        if blocked:
            continue
        result = session.exec(
            sa_update(MessageGenerationJob)
            .where(
                MessageGenerationJob.id == candidate.id,
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
    if job.status in {"completed", "failed", "cancelled"}:
        return job
    job.cancel_requested_at = current_time
    job.updated_at = current_time
    job.state_version += 1
    event_status = "cancel_requested"
    if job.status == "queued":
        job.status = "cancelled"
        job.completed_at = current_time
        job.lease_owner = None
        job.lease_expires_at = None
        job.heartbeat_at = None
        event_status = "cancelled"
    session.add(job)
    append_generation_job_event(session, job, event_status)
    session.commit()
    session.refresh(job)
    return job


def mark_message_generation_job_cancelled(
    session: Session,
    job: MessageGenerationJob,
    *,
    reason: str = "cancel_requested",
) -> MessageGenerationJob:
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
) -> MessageGenerationJob:
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
    tasks = session.exec(select(PostCommitTask).where(PostCommitTask.status == "processing")).all()
    recovered = 0
    for task in tasks:
        if task.lease_expires_at is not None and sortable_datetime(task.lease_expires_at) > sortable_datetime(current_time):
            continue
        task.status = "queued"
        task.lease_owner = None
        task.lease_expires_at = None
        task.available_at = current_time
        task.updated_at = current_time
        session.add(task)
        recovered += 1
    if recovered:
        session.commit()
    return recovered


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
) -> bool:
    if task.status != "processing" or task.lease_owner != lease_owner:
        return False
    now = datetime.now(timezone.utc)
    task.status = "completed"
    task.lease_owner = None
    task.lease_expires_at = None
    task.last_error = None
    task.completed_at = now
    task.updated_at = now
    session.add(task)
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
    if task.status != "processing" or task.lease_owner != lease_owner:
        return False
    current_time = now or datetime.now(timezone.utc)
    terminal = task.attempt_count >= max(1, int(max_attempts))
    task.status = "failed" if terminal else "queued"
    task.lease_owner = None
    task.lease_expires_at = None
    task.last_error = compact_text(f"{type(exc).__name__}: {exc}", 1000)
    task.available_at = current_time if terminal else current_time + timedelta(seconds=min(300, 2 ** max(1, task.attempt_count)))
    task.updated_at = current_time
    task.completed_at = current_time if terminal else None
    session.add(task)
    session.commit()
    session.refresh(task)
    return True


def finalize_message_generation_turn(
    session: Session,
    job: MessageGenerationJob | None,
    generated_messages: list[Message],
    *,
    source_message_id: str | None = None,
    source_metadata: dict | None = None,
    post_commit_tasks: list[dict] | None = None,
) -> list[Message]:
    try:
        existing_messages = []
        if job is not None:
            existing_messages = list(session.exec(
                select(Message)
                .where(Message.generation_job_id == job.id)
                .order_by(Message.reply_index, Message.created_at)
            ).all())
        if existing_messages:
            if job is not None and job.status != "completed":
                mark_message_generation_job_completed(
                    session,
                    job,
                    [message.id for message in existing_messages],
                    commit=False,
                )
                session.commit()
            return existing_messages

        if source_message_id is not None and source_metadata is not None:
            source_message = session.get(Message, source_message_id)
            if not source_message:
                raise ValueError(f"Source message '{source_message_id}' was not found during finalization")
            source_message.metadata_ = dict(source_metadata)
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


def mark_message_generation_job_failed(session: Session, job: MessageGenerationJob, exc: Exception) -> MessageGenerationJob:
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


def get_conversation(session: Session, conversation_id: str) -> Conversation | None:
    return session.get(Conversation, conversation_id)


def desired_mode_for_participants(participants: list[ConversationParticipant] | list[ParticipantCreate]) -> str:
    character_count = sum(
        1
        for item in participants
        if getattr(item, "participant_type", getattr(item, "type", None)) == "character"
    )
    return "character_character" if character_count >= 2 else "user_character"


def reconcile_conversation_mode(session: Session, conversation: Conversation, participants: list[ConversationParticipant] | None = None) -> Conversation:
    participants = participants if participants is not None else get_participants(session, conversation.id)
    conversation.mode = desired_mode_for_participants(participants)
    conversation.updated_at = datetime.now(timezone.utc)
    session.add(conversation)
    return conversation


def update_conversation(session: Session, conversation_id: str, payload: ConversationUpdate) -> Conversation | None:
    conversation = get_conversation(session, conversation_id)
    if not conversation:
        return None
    data = payload.model_dump(exclude_unset=True)
    now = datetime.now(timezone.utc)
    if "title" in data and data["title"] is not None:
        title = data["title"].strip()
        if title:
            conversation.title = title
    if "thumbnail_url" in data:
        conversation.thumbnail_url = (data.get("thumbnail_url") or "").strip() or None
    if "genre_mode" in data and data["genre_mode"] is not None:
        conversation.genre_mode = normalize_genre_mode(data["genre_mode"])
    if "world_setting_id" in data:
        world_setting_id = (data.get("world_setting_id") or "").strip() or None
        if world_setting_id and not session.get(WorldSetting, world_setting_id):
            world_setting_id = None
        conversation.world_setting_id = world_setting_id
    if "tts_enabled" in data and data["tts_enabled"] is not None:
        conversation.tts_enabled = bool(data["tts_enabled"])
    if "participants" in data and payload.participants is not None:
        for idx, participant_payload in enumerate(payload.participants):
            participant = session.get(ConversationParticipant, (conversation_id, participant_payload.type, participant_payload.id))
            if not participant:
                continue
            participant.role = participant_payload.role
            participant.order_index = participant_payload.order_index if participant_payload.order_index is not None else idx
            session.add(participant)
        reconcile_conversation_mode(session, conversation)
    if "scene" in data and data["scene"] is not None:
        scene_payload = payload.scene
        scene_state = session.get(SceneState, conversation_id)
        if not scene_state:
            scene_state = SceneState(conversation_id=conversation_id)
        scene_data = scene_payload.model_dump(exclude_unset=True, exclude={"user_description"})
        for key, value in scene_data.items():
            setattr(scene_state, key, value.strip() if isinstance(value, str) else value)
        if "user_description" in scene_payload.model_fields_set and scene_payload.user_description is not None:
            user_description = scene_payload.user_description.strip()
            scene_state.user_description = user_description or None
        scene_state.updated_at = now
        session.add(scene_state)
    conversation.updated_at = now
    session.add(conversation)
    session.commit()
    session.refresh(conversation)
    return conversation


def add_participant(session: Session, conversation_id: str, payload: ParticipantCreate) -> ConversationParticipant | None:
    conversation = get_conversation(session, conversation_id)
    if not conversation:
        return None
    existing = session.get(ConversationParticipant, (conversation_id, payload.type, payload.id))
    if existing:
        return existing
    participants = get_participants(session, conversation_id)
    order_index = payload.order_index if payload.order_index is not None else (max([p.order_index or 0 for p in participants], default=-1) + 1)
    participant = ConversationParticipant(
        conversation_id=conversation_id,
        participant_type=payload.type,
        participant_id=payload.id,
        role=payload.role,
        order_index=order_index,
    )
    session.add(participant)
    reconcile_conversation_mode(session, conversation, [*participants, participant])
    session.commit()
    session.refresh(participant)
    return participant


def remove_participant(session: Session, conversation_id: str, participant_type: str, participant_id: str) -> bool | None:
    conversation = get_conversation(session, conversation_id)
    if not conversation:
        return None
    participant = session.get(ConversationParticipant, (conversation_id, participant_type, participant_id))
    if not participant:
        return False
    session.delete(participant)
    session.flush()
    remaining = [p for p in get_participants(session, conversation_id) if not (p.participant_type == participant_type and p.participant_id == participant_id)]
    reconcile_conversation_mode(session, conversation, remaining)
    session.commit()
    return True


def get_participants(session: Session, conversation_id: str) -> list[ConversationParticipant]:
    stmt = select(ConversationParticipant).where(ConversationParticipant.conversation_id == conversation_id).order_by(ConversationParticipant.order_index)
    return list(session.exec(stmt).all())


def list_messages(
    session: Session,
    conversation_id: str,
    *,
    limit: int | None = None,
    before_id: str | None = None,
    recent_turns: int | None = None,
) -> list[Message]:
    upper_message = session.get(Message, before_id) if before_id else None
    if before_id and (not upper_message or upper_message.conversation_id != conversation_id):
        return []

    conditions = [Message.conversation_id == conversation_id]
    if upper_message:
        conditions.append(Message.created_at < upper_message.created_at)

    if recent_turns:
        turn_conditions = [Message.conversation_id == conversation_id, Message.speaker_type.in_({"user", "system"})]
        if upper_message:
            turn_conditions.append(Message.created_at < upper_message.created_at)
        turn_stmt = (
            select(Message)
            .where(*turn_conditions)
            .order_by(Message.created_at.desc(), Message.id.desc())
            .limit(max(1, min(50, recent_turns)))
        )
        anchors = list(session.exec(turn_stmt).all())
        if not anchors:
            stmt = select(Message).where(*conditions).order_by(text("created_at DESC"), text("id DESC")).limit(max(1, min(200, recent_turns)))
            return list(reversed(session.exec(stmt).all()))
        if len(anchors) >= recent_turns:
            conditions.append(Message.created_at >= anchors[-1].created_at)
        stmt = select(Message).where(*conditions).order_by(Message.created_at, Message.id)
        return list(session.exec(stmt).all())

    stmt = select(Message).where(*conditions)
    if limit:
        stmt = stmt.order_by(Message.created_at.desc(), Message.id.desc()).limit(max(1, min(200, limit)))
        return list(reversed(session.exec(stmt).all()))
    stmt = stmt.order_by(Message.created_at, Message.id)
    return list(session.exec(stmt).all())


def get_message(session: Session, message_id: str) -> Message | None:
    return session.get(Message, message_id)


def _display_name_for_speaker(session: Session, speaker_type: str, speaker_id: str | None) -> str:
    if speaker_type == "user":
        return "user"
    if speaker_type == "system":
        return "system"
    if speaker_id:
        character = session.get(Character, speaker_id)
        if character:
            return character.name
    return speaker_id or speaker_type


def _character_display_name(session: Session, character_id: str | None) -> str:
    if not character_id:
        return "-"
    character = session.get(Character, character_id)
    return character.name if character else character_id


def _active_battle_match(session: Session, conversation_id: str) -> BattleMatchRecord | None:
    records = list(session.exec(
        select(BattleMatchRecord).where(
            BattleMatchRecord.conversation_id == conversation_id,
            BattleMatchRecord.genre_mode == "battle",
            BattleMatchRecord.result_status == "in_progress",
        )
    ).all())
    records.sort(key=lambda item: (sortable_datetime(item.updated_at), sortable_datetime(item.created_at), item.id), reverse=True)
    return records[0] if records else None


def build_live_scene_state(session: Session, conversation: Conversation, scene_state: SceneState | None = None) -> SceneState:
    """Return a UI/prompt view with live raw-tail scene fields.

    Persisted ``summary`` remains the durable story-arc source of truth. Short-lived
    drawer fields are derived from recent visible messages without writing them
    back to the database, so they cannot diverge into a second long-term memory.
    """
    base = scene_state or SceneState(conversation_id=conversation.id)
    recent_messages = list_messages(session, conversation.id, limit=8)
    latest = next((message for message in reversed(recent_messages) if (message.content or message.action)), None)
    latest_line = ""
    if latest:
        speaker = _display_name_for_speaker(session, latest.speaker_type, latest.speaker_id)
        visible = compact_text(" ".join(part for part in [latest.action, latest.content] if part), 180)
        latest_line = f"{speaker}: {visible}" if visible else speaker

    live_patch = build_structured_scene_patch(base, recent_messages)
    live_conflict = live_patch["current_conflict"]
    active_match = _active_battle_match(session, conversation.id) if normalize_genre_mode(conversation.genre_mode) == "battle" else None
    if active_match:
        participant_a = _character_display_name(session, active_match.participant_a_id)
        participant_b = _character_display_name(session, active_match.participant_b_id)
        metadata = active_match.metadata_ if isinstance(active_match.metadata_, dict) else {}
        order = metadata.get("match_order")
        phase = metadata.get("current_phase")
        order_prefix = f"#{order} " if order else ""
        phase_suffix = f" · phase={phase}" if phase else ""
        live_conflict = f"진행 중: {order_prefix}{participant_a} vs {participant_b}{phase_suffix}"

    return SceneState(
        conversation_id=conversation.id,
        location=live_patch["location"] or base.location,
        time_label=base.time_label,
        mood=live_patch["mood"] or base.mood,
        world_seed=base.world_seed,
        opening_scene=base.opening_scene,
        opening_line=base.opening_line,
        tone_preset=base.tone_preset,
        relationship_archetype=base.relationship_archetype,
        current_conflict=live_conflict or base.current_conflict,
        compression_focus=base.compression_focus,
        last_event=latest_line or live_patch["last_event"] or base.last_event,
        tension_level=base.tension_level,
        romance_level=base.romance_level,
        user_description=base.user_description,
        summary=base.summary,
        last_compression_error=base.last_compression_error,
        last_compression_attempt_at=base.last_compression_attempt_at,
        last_compressed_at=base.last_compressed_at,
        last_compression_source_message_id=base.last_compression_source_message_id,
        compression_revision=base.compression_revision,
        updated_at=max(sortable_datetime(base.updated_at), sortable_datetime(latest.created_at if latest else None)).replace(tzinfo=None),
    )


def delete_message(session: Session, message: Message) -> None:
    session.exec(delete(MessageAsset).where(MessageAsset.message_id == message.id))
    conversation = get_conversation(session, message.conversation_id)
    session.delete(message)
    if conversation:
        conversation.updated_at = datetime.now(timezone.utc)
        session.add(conversation)
    session.commit()


def delete_messages(session: Session, conversation_id: str, message_ids: list[str]) -> tuple[list[str], list[str]]:
    unique_ids = list(dict.fromkeys(message_ids))
    if not unique_ids:
        return [], []
    messages = session.exec(
        select(Message).where(Message.conversation_id == conversation_id, Message.id.in_(unique_ids))
    ).all()
    found_by_id = {message.id: message for message in messages}
    deleted_ids = [message_id for message_id in unique_ids if message_id in found_by_id]
    missing_ids = [message_id for message_id in unique_ids if message_id not in found_by_id]
    if not deleted_ids:
        return [], missing_ids
    session.exec(delete(MessageAsset).where(MessageAsset.message_id.in_(deleted_ids)))
    for message_id in deleted_ids:
        session.delete(found_by_id[message_id])
    conversation = get_conversation(session, conversation_id)
    if conversation:
        conversation.updated_at = datetime.now(timezone.utc)
        session.add(conversation)
    session.commit()
    return deleted_ids, missing_ids


def get_character_memory(session: Session, memory_id: str) -> CharacterMemory | None:
    return session.get(CharacterMemory, memory_id)


def add_character_memory(
    session: Session,
    conversation_id: str,
    *,
    character_id: str = COMMON_ROOM_MEMORY_CHARACTER_ID,
    memory_type: str = "user_note",
    content: str,
    importance: int = 5,
) -> CharacterMemory:
    normalized_character_id = normalize_memory_character_id(character_id)
    memory = CharacterMemory(
        id=f"mem_{uuid4().hex[:12]}",
        conversation_id=conversation_id,
        character_id=normalized_character_id,
        memory_type="user_note",
        content=compact_text(content, 500, preserve_linebreaks=True),
        importance=max(1, min(5, int(importance or 5))),
    )
    session.add(memory)
    conversation = get_conversation(session, conversation_id)
    if conversation:
        conversation.updated_at = datetime.now(timezone.utc)
        session.add(conversation)
    session.commit()
    session.refresh(memory)
    genre_mode = normalize_genre_mode(getattr(conversation, "genre_mode", None)) if conversation else DEFAULT_GENRE_MODE
    external_memory_service.sync_local_memory_to_external(memory, genre_mode=genre_mode)
    return memory


def normalize_memory_character_id(character_id: str | None) -> str:
    normalized_character_id = character_id or COMMON_ROOM_MEMORY_CHARACTER_ID
    if normalized_character_id in {"room", "common", "all", "shared", "__common__"}:
        normalized_character_id = COMMON_ROOM_MEMORY_CHARACTER_ID
    return normalized_character_id


def update_character_memory(
    session: Session,
    memory: CharacterMemory,
    *,
    character_id: str | None = None,
    memory_type: str | None = None,
    content: str | None = None,
    importance: int | None = None,
) -> CharacterMemory:
    if character_id is not None:
        memory.character_id = normalize_memory_character_id(character_id)
    memory.memory_type = "user_note"
    if content is not None:
        memory.content = compact_text(content, 500, preserve_linebreaks=True)
    if importance is not None:
        memory.importance = max(1, min(5, int(importance or 5)))
    memory.updated_at = datetime.now(timezone.utc)
    conversation = get_conversation(session, memory.conversation_id)
    if conversation:
        conversation.updated_at = memory.updated_at
        session.add(conversation)
    session.add(memory)
    session.commit()
    session.refresh(memory)
    genre_mode = normalize_genre_mode(getattr(conversation, "genre_mode", None)) if conversation else DEFAULT_GENRE_MODE
    external_memory_service.sync_local_memory_to_external(memory, genre_mode=genre_mode)
    return memory


def delete_character_memory(session: Session, memory: CharacterMemory) -> None:
    conversation = get_conversation(session, memory.conversation_id)
    session.delete(memory)
    if conversation:
        conversation.updated_at = datetime.now(timezone.utc)
        session.add(conversation)
    session.commit()


def build_message(
    conversation_id: str,
    payload: MessageCreate,
    *,
    emotion: str | None = None,
    action: str | None = None,
    thought: str | None = None,
) -> Message:
    return Message(
        id=f"msg_{uuid4().hex[:12]}",
        conversation_id=conversation_id,
        speaker_type=payload.speaker_type,
        speaker_id=payload.speaker_id,
        content=payload.content,
        emotion=emotion,
        action=action if action is not None else payload.action,
        thought=thought if thought is not None else payload.thought,
        metadata_=dict(payload.metadata or {}),
    )


def add_message(
    session: Session,
    conversation_id: str,
    payload: MessageCreate,
    *,
    emotion: str | None = None,
    action: str | None = None,
    thought: str | None = None,
) -> Message:
    message = build_message(
        conversation_id,
        payload,
        emotion=emotion,
        action=action,
        thought=thought,
    )
    session.add(message)
    session.commit()
    session.refresh(message)
    return message


def mark_message_generation_failed(session: Session, message: Message, error: Exception) -> Message:
    metadata = dict(message.metadata_ or {})
    metadata.update({
        "generation_status": "failed",
        "generation_error_type": type(error).__name__,
        "generation_error_message": compact_text(str(error), limit=1000),
    })
    error_code = getattr(error, "code", None)
    if isinstance(error_code, str) and error_code:
        metadata["generation_error_code"] = error_code
    message.metadata_ = metadata
    session.add(message)
    session.commit()
    session.refresh(message)
    return message


def compact_text(value: str | None, limit: int = 180, *, preserve_linebreaks: bool = False) -> str:
    if preserve_linebreaks:
        lines = [" ".join(line.split()) for line in (value or "").splitlines()]
        compacted_lines = []
        previous_blank = False
        for line in lines:
            is_blank = not line
            if is_blank and previous_blank:
                continue
            compacted_lines.append(line)
            previous_blank = is_blank
        text = "\n".join(compacted_lines).strip()
    else:
        text = " ".join((value or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def compact_block_text(value: str | None, limit: int = 900) -> str:
    """Compact a structured block while preserving line breaks and bullets.

    Scene memory is intentionally a small structured block. Collapsing it with
    `compact_text()` turns headings and bullets into one dense sentence, which
    makes the next prompt harder to read and encourages raw-log leakage.
    """
    lines = []
    for line in (value or "").splitlines():
        stripped = " ".join(line.strip().split())
        if not stripped:
            continue
        indent = "  " if line[:1].isspace() and stripped.startswith("-") else ""
        lines.append(f"{indent}{stripped}")
    text = "\n".join(lines).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


RELATIONSHIP_FACT_NOISE_RE = re.compile(
    r"(dialogue=|action=|emotion=|thought=|speaker_id=|counterpart message:|character reply:|"
    r"다음\s*진행\s*판단|판결|승인|여부|사용자에게|유저에게|"
    r"이전\s*압축|원문|재압축|메타|진단)",
    re.IGNORECASE,
)

RELATIONSHIP_ABSTRACT_PLACEHOLDER_RE = re.compile(
    r"^(?:.*)(긴장감과\s*호감|호감과\s*긴장|관계가\s*(?:깊어지고|복잡해지고|진전되고)|"
    r"긴장(?:과|감).*누적|호감.*누적|갈등.*누적|신뢰.*누적)(?:.*)$",
    re.IGNORECASE,
)

SCENE_SUMMARY_RAW_MARKER_RE = re.compile(
    r"(dialogue=|action=|emotion=|thought=|private=|stance=|speaker_id=|speaker_type=|directive=|counterpart message:|character reply:)",
    re.IGNORECASE,
)


def relationship_fact_for_display(value: str | None) -> str | None:
    """Return a relationship fact safe for prompt/display.

    Only structured-output residue and meta-control instructions are blocked here.
    General Korean nouns are not blocked: a word can be relationship-relevant in
    the right sentence. Generic score-derived placeholders are handled by cleanup
    and regeneration instead of broad keyword filtering.
    """
    text = compact_text(value, 180)
    if not text or RELATIONSHIP_FACT_NOISE_RE.search(text) or RELATIONSHIP_ABSTRACT_PLACEHOLDER_RE.search(text):
        return None
    return text


def is_generic_relationship_dynamic(value: str | None) -> bool:
    text = compact_text(value, 180)
    return text.startswith("상대와의 관계에서 ") or text.startswith("아직 뚜렷한 관계 변화 없이")


def relationship_state_has_noisy_text(state: ConversationRelationshipState) -> bool:
    candidates = [state.current_dynamic or "", *(state.unresolved_hooks or [])]
    return any(bool(item and RELATIONSHIP_FACT_NOISE_RE.search(item)) for item in candidates)


def clean_relationship_dynamic_for_storage(value: str | None) -> str:
    return relationship_fact_for_display(value) or ""


def clean_relationship_hook_for_storage(value: str | None) -> str:
    text = compact_text(value, 100)
    if not text or SCENE_SUMMARY_RAW_MARKER_RE.search(text) or RELATIONSHIP_FACT_NOISE_RE.search(text):
        return ""
    # Relationship hooks are only for unresolved future-facing relationship beats.
    # Raw dialogue/action belongs to recent history or scene summary, not the
    # relationship table. Do not keyword-ban ordinary nouns; require hook intent.
    hook_markers = ["다음", "앞으로", "이어", "계속", "미해결", "오픈", "단서", "목표", "계획", "future", "next", "continue", "unresolved", "hook", "clue", "goal", "plan"]
    if not any(marker in text.lower() or marker in text for marker in hook_markers):
        return ""
    return text


def build_relationship_fact_from_scores(state: ConversationRelationshipState) -> str:
    positive = []
    if state.trust_level > 0:
        positive.append("신뢰")
    if state.affinity_level > 0:
        positive.append("친밀감")
    if state.cooperation_level > 0:
        positive.append("협력")
    friction = []
    if state.tension_level > 0:
        friction.append("긴장")
    if state.conflict_level > 0:
        friction.append("갈등")
    if positive and friction:
        return f"상호 {', '.join(positive)}은 있으나 {', '.join(friction)}도 남아 있는 관계."
    if positive:
        return f"상호 {', '.join(positive)}이 중심인 안정적 관계."
    if friction:
        return f"상호 {', '.join(friction)}이 두드러지는 경계 관계."
    return "아직 뚜렷한 관계 방향이 정해지지 않은 관찰 단계."


def clamp_state_value(value: int, *, low: int = -5, high: int = 5) -> int:
    return max(low, min(high, value))


def summarize_message_for_memory(message: Message) -> str:
    parts = []
    if message.action:
        parts.append(f"action={compact_text(message.action, 100)}")
    if message.content:
        parts.append(f"dialogue={compact_text(message.content, 160)}")
    if message.emotion:
        parts.append(f"emotion={compact_text(message.emotion, 40)}")
    return " | ".join(parts) or compact_text(message.content, 160)


def list_character_memories(session: Session, conversation_id: str, character_id: str, *, limit: int = MAX_CHARACTER_MEMORY_ITEMS) -> list[CharacterMemory]:
    conversation_ids = [conversation_id]
    if character_id != COMMON_ROOM_MEMORY_CHARACTER_ID:
        conversation_ids.append(GLOBAL_CHARACTER_MEMORY_CONVERSATION_ID)
    stmt = (
        select(CharacterMemory)
        .where(
            CharacterMemory.conversation_id.in_(conversation_ids),
            CharacterMemory.character_id == character_id,
            CharacterMemory.memory_type == "user_note",
        )
        .order_by(CharacterMemory.importance.desc(), CharacterMemory.updated_at.desc())
        .limit(max(limit * 6, limit))
    )
    durable = [memory for memory in session.exec(stmt).all() if is_durable_room_memory(memory.content, memory.memory_type, memory.importance)]
    merged: list[CharacterMemory] = []
    seen: set[str] = set()
    for memory in durable:
        fingerprint = normalize_memory_fingerprint(memory.content)
        if not fingerprint or fingerprint in seen:
            continue
        seen.add(fingerprint)
        merged.append(memory)
    return merged[:limit]


def list_common_room_memories(session: Session, conversation_id: str, *, limit: int = MAX_CHARACTER_MEMORY_ITEMS) -> list[CharacterMemory]:
    return list_character_memories(session, conversation_id, COMMON_ROOM_MEMORY_CHARACTER_ID, limit=limit)


def visible_memory_character_ids(active_character_ids: set[str] | list[str] | tuple[str, ...]) -> set[str]:
    return set(active_character_ids) | {COMMON_ROOM_MEMORY_CHARACTER_ID}


def list_visible_durable_memories_for_compression(
    session: Session,
    conversation_id: str,
    active_character_ids: set[str] | list[str] | tuple[str, ...],
    *,
    limit: int = MAX_CONTEXT_MEMORY_SCAN_ITEMS,
) -> list[CharacterMemory]:
    scoped_ids = visible_memory_character_ids(active_character_ids)
    raw = session.exec(
        select(CharacterMemory)
        .where(
            CharacterMemory.conversation_id == conversation_id,
            CharacterMemory.character_id.in_(scoped_ids),
            CharacterMemory.memory_type == "user_note",
        )
        .order_by(CharacterMemory.importance.desc(), CharacterMemory.updated_at.desc())
        .limit(limit * 4)
    ).all()
    memories: list[CharacterMemory] = []
    seen: set[str] = set()
    for memory in raw:
        if not is_durable_room_memory(memory.content, memory.memory_type, memory.importance):
            continue
        fingerprint = normalize_memory_fingerprint(memory.content)
        if not fingerprint or fingerprint in seen:
            continue
        seen.add(fingerprint)
        memories.append(memory)
        if len(memories) >= limit:
            break
    return memories


def list_continuity_memories_for_character(session: Session, conversation_id: str, character_id: str, *, limit: int = MAX_CHARACTER_MEMORY_ITEMS) -> list[CharacterMemory]:
    """Return room-common memory first, then character-specific memory for prompt injection."""
    common = list_common_room_memories(session, conversation_id, limit=limit)
    character_specific = list_character_memories(session, conversation_id, character_id, limit=limit)
    merged: list[CharacterMemory] = []
    seen: set[str] = set()
    for memory in [*common, *character_specific]:
        fingerprint = normalize_memory_fingerprint(memory.content)
        if not fingerprint or fingerprint in seen:
            continue
        seen.add(fingerprint)
        merged.append(memory)
    return merged[: limit * 2]


def get_relationship_state(
    session: Session,
    conversation_id: str,
    character_id: str,
    *,
    counterpart_type: str = "user",
    counterpart_id: str = "user_001",
) -> ConversationRelationshipState | None:
    return session.get(ConversationRelationshipState, (conversation_id, character_id, counterpart_type, counterpart_id))


def list_relationship_states_for_character(
    session: Session,
    conversation_id: str,
    character_id: str,
    *,
    limit: int = MAX_RELATIONSHIP_CONTEXT_ITEMS,
    active_counterpart_ids: set[str] | None = None,
) -> list[ConversationRelationshipState]:
    stmt = (
        select(ConversationRelationshipState)
        .where(
            ConversationRelationshipState.conversation_id == conversation_id,
            ConversationRelationshipState.character_id == character_id,
            ConversationRelationshipState.counterpart_type != "system",
        )
        .order_by(ConversationRelationshipState.updated_at.desc())
        .limit(max(limit * 8, 24))
    )
    states = list(session.exec(stmt).all())
    if not active_counterpart_ids:
        return states[:limit]

    active_ids = set(active_counterpart_ids)
    states = [
        state for state in states
        if state.counterpart_type == "user"
        or state.counterpart_id in active_ids
        or state.counterpart_id == COMMON_ROOM_MEMORY_CHARACTER_ID
    ]

    def relevance(state: ConversationRelationshipState) -> tuple[int, datetime]:
        score = 0
        if state.counterpart_type == "user":
            score += 30
        if state.counterpart_id in active_counterpart_ids or state.counterpart_id == COMMON_ROOM_MEMORY_CHARACTER_ID:
            score += 100
        elif state.counterpart_type == "character":
            score -= 60
        if state.current_dynamic:
            score += 5
        if state.unresolved_hooks:
            score += 5
        return score, sortable_datetime(state.updated_at)

    return sorted(states, key=relevance, reverse=True)[:limit]


def get_or_create_relationship_state(
    session: Session,
    conversation_id: str,
    character_id: str,
    *,
    counterpart_type: str = "user",
    counterpart_id: str = "user_001",
    commit: bool = True,
) -> ConversationRelationshipState:
    state = get_relationship_state(
        session,
        conversation_id,
        character_id,
        counterpart_type=counterpart_type,
        counterpart_id=counterpart_id,
    )
    if state:
        return state
    state = ConversationRelationshipState(
        conversation_id=conversation_id,
        character_id=character_id,
        counterpart_type=counterpart_type,
        counterpart_id=counterpart_id,
    )
    session.add(state)
    if commit:
        session.commit()
        session.refresh(state)
    return state


def record_long_term_memory(
    session: Session,
    *,
    conversation_id: str,
    character_id: str,
    source_message: Message,
    memory_type: str = "event",
    importance: int = 2,
) -> CharacterMemory | None:
    content = summarize_message_for_memory(source_message)
    if not content:
        return None
    memory = CharacterMemory(
        id=f"mem_{uuid4().hex[:12]}",
        conversation_id=conversation_id,
        character_id=character_id,
        memory_type=memory_type,
        content=content,
        importance=max(1, min(5, importance)),
    )
    session.add(memory)
    session.commit()
    session.refresh(memory)
    return memory


def update_relationship_state_from_turn(
    session: Session,
    *,
    conversation_id: str,
    character_id: str,
    user_message: Message | None = None,
    character_message: Message | None = None,
    counterpart_type: str = "user",
    counterpart_id: str = "user_001",
    commit: bool = True,
) -> ConversationRelationshipState | None:
    if counterpart_type == "system" or counterpart_id == COMMON_ROOM_MEMORY_CHARACTER_ID:
        return None
    state = get_or_create_relationship_state(
        session,
        conversation_id,
        character_id,
        counterpart_type=counterpart_type,
        counterpart_id=counterpart_id,
        commit=commit,
    )
    if user_message:
        if user_message.action:
            state.affinity_level = clamp_state_value(state.affinity_level + 1)
            state.cooperation_level = clamp_state_value(state.cooperation_level + 1)
        if "?" in user_message.content or "？" in user_message.content:
            state.cooperation_level = clamp_state_value(state.cooperation_level + 1)
        if any(token in user_message.content for token in ["싫", "그만", "짜증", "화나", "아니"]):
            state.tension_level = clamp_state_value(state.tension_level + 1)
            state.conflict_level = clamp_state_value(state.conflict_level + 1)
        if any(token in user_message.content for token in ["고마", "좋", "괜찮", "편해"]):
            state.trust_level = clamp_state_value(state.trust_level + 1)
        hook = clean_relationship_hook_for_storage(user_message.content or user_message.action)
        if not hook and (user_message.content or user_message.action):
            hook = "다음 턴에서 최근 사용자 상태를 이어받기"
        hooks = [item for item in (state.unresolved_hooks or []) if item != hook]
        state.unresolved_hooks = ([hook] + hooks)[:3] if hook else hooks[:3]
    if character_message:
        state.current_mood = compact_text(character_message.emotion, 60) if character_message.emotion else state.current_mood
    if not relationship_fact_for_display(state.current_dynamic):
        state.current_dynamic = build_relationship_fact_from_scores(state)
    state.updated_at = datetime.now(timezone.utc)
    session.add(state)
    if commit:
        session.commit()
        session.refresh(state)
    return state


def parse_json_object_from_text(text: str) -> dict | None:
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        pass
    decoder = json.JSONDecoder()
    for index, char in enumerate(text):
        if char != "{":
            continue
        try:
            data, _ = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            return data
    return None


def normalize_memory_type(value: str | None) -> str:
    allowed = {"fact", "preference", "event", "boundary", "open_hook", "user_note"}
    text = (value or "event").strip().lower()
    return text if text in allowed else "event"


DURABLE_MEMORY_POSITIVE_TOKENS = [
    "세계관", "설정", "규칙", "장소", "공간", "상황", "사건", "단서", "비밀", "약속",
    "목표", "계획", "임무", "금지", "경계", "선호", "사실", "오픈 훅", "앞으로",
    "다음", "관계가", "바뀜", "변화", "고정", "확정", "기억해야",
    "world", "worldbuilding", "setting", "rule", "location", "place", "situation", "event", "clue",
    "secret", "promise", "goal", "plan", "mission", "boundary", "preference", "fact", "hook",
    "changed", "established", "confirmed", "entered", "arrived", "residence", "lodging", "identity",
]
DURABLE_MEMORY_NOISE_TOKENS = [
    "말했다", "대답했다", "웃었다", "바라봤", "고개", "표정", "기분", "감정", "반응했다",
    "생각했다", "느꼈", "한마디", "대사", "잠깐", "살짝", "미소", "침묵",
    "said", "replied", "answered", "smiled", "looked", "glanced", "nodded", "emotion", "mood",
    "reacted", "thought", "felt", "line", "briefly", "slightly", "silence",
]
BATTLE_RANKING_TOKENS = ["서열", "랭킹", "순위", "위", "승리", "패배", "격파", "굴복", "도전 금지", "락", "결투", "대전", "히스토리", "스토리"]
BATTLE_FLOW_NOISE_TOKENS = ["직전", "대기", "핵심 변수", "공세", "공격 강도", "패턴", "버티", "이성을 잃", "주도권을 역전"]
BATTLE_FINAL_TOKENS = ["확정", "공식", "현재", "유지", "승리", "패배", "격파", "굴복", "도전 금지 락", "락 해제"]
# Official battle/ranking outcomes are owned by battle/domain events, not by
# generic durable-memory compression. Existing ledger helpers stay for legacy rows
# and manual/domain updates, but LLM memory extraction should not mint new rows.
BATTLE_LEDGER_KEYS = {"ranking_snapshot", "battle_history", "battle_rule"}
LEAGUE_TOKENS = ["리그", "승점", "승무패", "정규리그", "순위표", "승점표", "경기 결과", "전적", "무승부", "league", "standings", "points", "match result", "record"]
LEAGUE_STANDINGS_TOKENS = ["순위표", "승점표", "standings", "points table"]
LEAGUE_HISTORY_TOKENS = ["경기 결과", "경기 히스토리", "매치 히스토리", "match history", "match result"]


def normalize_memory_fingerprint(text: str) -> str:
    return "".join(ch for ch in text.lower() if ch.isalnum() or "가" <= ch <= "힣")[:180]


MEMORY_DEDUPE_STOPWORDS = {
    "그리고", "그러나", "하지만", "현재", "이번", "모든", "오직", "통해", "으로", "하며",
    "한다", "된다", "이루어진다", "이루어짐", "간주됨", "상태", "상황", "사실", "공식",
    "the", "and", "or", "to", "of", "a", "an", "is", "are", "was", "were", "only",
}


def memory_similarity_tokens(text: str) -> set[str]:
    normalized = re.sub(r"char_[0-9a-f]+", " ", text.lower())
    raw_tokens = re.findall(r"[가-힣a-z0-9]{2,}", normalized)
    return {token for token in raw_tokens if token not in MEMORY_DEDUPE_STOPWORDS and not token.isdigit()}


def memories_are_similar(left: str, right: str) -> bool:
    left_tokens = memory_similarity_tokens(left)
    right_tokens = memory_similarity_tokens(right)
    if not left_tokens or not right_tokens:
        return False
    overlap = left_tokens & right_tokens
    smaller = min(len(left_tokens), len(right_tokens))
    larger = max(len(left_tokens), len(right_tokens))
    if smaller <= 4:
        return len(overlap) >= max(3, smaller)
    containment = len(overlap) / smaller
    jaccard = len(overlap) / larger
    return containment >= 0.62 or jaccard >= 0.45


def scene_summary_copies_recent_raw_text(summary: str, recent_messages: list[Message]) -> bool:
    """Reject compression that merely strips field labels but keeps recent turns verbatim."""
    normalized_summary = compact_text(summary, MAX_SCENE_MEMORY_CHARS)
    if not normalized_summary:
        return False
    for message in recent_messages[-12:]:
        for raw in [message.content, message.action, message.thought]:
            text = compact_text(raw, 260)
            if len(text) < 36:
                continue
            if text[:70] in normalized_summary or text[-70:] in normalized_summary:
                return True
            for start in range(0, max(1, len(text) - 55), 45):
                chunk = text[start:start + 55]
                if len(chunk) >= 36 and chunk in normalized_summary:
                    return True
    return False


def battle_ranking_memory_key(content: str) -> str | None:
    """Return a stable ledger key for battle/ranking memories that should be updated, not appended."""
    text = compact_text(content, 260)
    if not any(token in text for token in BATTLE_RANKING_TOKENS):
        return None
    lowered = text.lower()
    char_ids = sorted(set(re.findall(r"char_[0-9a-f]+", lowered)))
    names = sorted(set(re.findall(r"[가-힣]{2,4}(?=(?:\(|는|은|가|이|에게|를|을|의|와|과))", text)))
    if "현재까지 대전" in text or "대전 스토리" in text or "대전 히스토리" in text:
        return "battle_history"
    if "승리 조건" in text or "서열 결정" in text or "서열 결정 방식" in text or "규칙" in text:
        return "battle_rule"
    if "도전 금지" in text or "락" in text:
        subject = char_ids[0] if char_ids else (names[0] if names else "room")
        return f"challenge_lock:{subject}"
    if "현재 확정된 주요 서열" in text or "랭킹" in text or "순위" in text or ("서열" in text and re.search(r"\d+위", text)):
        return "ranking_snapshot"
    if any(token in text for token in ["승리", "패배", "격파", "굴복"]):
        if len(char_ids) >= 2:
            return "battle_result:" + ":".join(char_ids[:2])
        if len(names) >= 2:
            return "battle_result:" + ":".join(names[:2])
        return "battle_result:room"
    return None


def league_memory_key(content: str) -> str | None:
    """Return a stable ledger key for league standings/match memories."""
    text = compact_text(content, 320)
    lowered = text.lower()
    if not any(token in text or token in lowered for token in LEAGUE_TOKENS):
        return None
    char_ids = sorted(set(re.findall(r"char_[0-9a-f]+", lowered)))
    names = sorted(set(re.findall(r"[가-힣]{2,4}(?=(?:\(|는|은|가|이|에게|를|을|의|와|과|:))", text)))
    if any(token in text or token in lowered for token in LEAGUE_STANDINGS_TOKENS):
        return "league_standings"
    if any(token in text or token in lowered for token in LEAGUE_HISTORY_TOKENS):
        return "league_match_history"
    if "승점" in text and (len(char_ids) + len(names)) >= 2:
        return "league_standings"
    if any(token in text for token in ["승", "패", "무"]) and (char_ids or names):
        subject = char_ids[0] if char_ids else names[0]
        return f"league_record:{subject}"
    return None


def memory_ledger_key(content: str) -> str | None:
    return battle_ranking_memory_key(content) or league_memory_key(content)


def is_league_ledger_key(ledger_key: str | None) -> bool:
    return bool(ledger_key and (ledger_key in {"league_standings", "league_match_history"} or ledger_key.startswith("league_record:")))


def is_battle_flow_noise(content: str) -> bool:
    text = compact_text(content, 260)
    if not any(token in text for token in BATTLE_RANKING_TOKENS):
        return False
    if any(token in text for token in ["직전", "대기", "핵심 변수", "다음 전투"]):
        return True
    if any(token in text for token in BATTLE_FLOW_NOISE_TOKENS) and not any(token in text for token in BATTLE_FINAL_TOKENS):
        return True
    return False


def is_battle_ledger_key(ledger_key: str | None) -> bool:
    if not ledger_key:
        return False
    return ledger_key in BATTLE_LEDGER_KEYS or ledger_key.startswith("battle_result:")


def contains_rank_ledger_claim(text: str) -> bool:
    has_rank_number = bool(re.search(r"\d+\s*위", text or ""))
    has_official_rank_phrase = any(phrase in (text or "") for phrase in [
        "현재 공개된 공식 서열", "현재 확정된 주요 서열", "공식 서열", "서열표", "랭킹표",
        "ranking snapshot", "official ranking",
    ])
    return has_official_rank_phrase or (has_rank_number and any(token in (text or "") for token in ["서열", "랭킹", "순위", "위"]))


def official_battle_memory_noise(content: str) -> bool:
    text = content or ""
    if not text:
        return False
    has_official_result = any(token in text for token in ["공식 승자", "공식 패자", "winner", "loser", "승자", "패자"])
    has_battle_result = any(token in text for token in ["승리", "패배", "격파", "굴복", "결과", "result"])
    return contains_rank_ledger_claim(text) or (has_official_result and has_battle_result)


def strip_rank_claims_from_scene_summary(summary: str) -> str:
    """Keep scene anchors, but remove model-invented official rank/ledger claims.

    Official battle/ranking canon lives in CharacterMemory ledger rows. Local compression models
    can drift while rewriting summaries, so scene summaries must not become a second source of truth.
    """
    cleaned_lines: list[str] = []
    for line in (summary or "").splitlines():
        text = line.strip()
        if not text:
            cleaned_lines.append(line)
            continue
        if contains_rank_ledger_claim(text):
            continue
        cleaned_lines.append(line)
    cleaned = "\n".join(cleaned_lines).strip()
    return cleaned or compact_text(summary, MAX_SCENE_MEMORY_CHARS)


def conversation_has_battle_ledger(memories: list[CharacterMemory]) -> bool:
    return any(is_battle_ledger_key(battle_ranking_memory_key(memory.content)) for memory in memories)


def extract_ledger_name_id_pairs(content: str) -> list[tuple[str, str]]:
    """Extract stable Korean name + character id anchors from ledger text."""
    return re.findall(r"([가-힣]{2,4})\((char_[0-9a-f]+)\)", content or "")


def has_ledger_name_id_mismatch(existing_memories: list[CharacterMemory], candidate_content: str) -> bool:
    """Reject compression ledger updates that pair an existing name/id anchor differently.

    Official battle ledgers often store rows like `김민지(char_...): 12위`. A local
    compression model can accidentally swap names and ids while rewriting a table;
    if a name or id already has a known pairing, the compression output must not
    introduce a conflicting pairing.
    """
    name_to_id: dict[str, str] = {}
    id_to_name: dict[str, str] = {}
    for memory in existing_memories:
        for name, character_id in extract_ledger_name_id_pairs(memory.content):
            name_to_id.setdefault(name, character_id)
            id_to_name.setdefault(character_id, name)
    if not name_to_id and not id_to_name:
        return False
    for name, character_id in extract_ledger_name_id_pairs(candidate_content):
        if name_to_id.get(name) not in {None, character_id}:
            return True
        if id_to_name.get(character_id) not in {None, name}:
            return True
    return False


def is_durable_room_memory(content: str, memory_type: str, importance: int = 3) -> bool:
    """Keep every non-empty manual user note; filter only auto-derived memory noise."""
    text = compact_text(content, 260)
    if memory_type == "user_note":
        return bool(text)
    if len(text) < 12 or importance < 3:
        return False
    if any(marker in text for marker in ["dialogue=", "action=", "emotion=", "speaker_id=", "character reply:", "counterpart message:"]):
        return False
    if is_battle_flow_noise(text):
        return False
    if memory_ledger_key(text):
        return True
    text_lower = text.lower()
    if any(token in text or token in text_lower for token in DURABLE_MEMORY_NOISE_TOKENS) and not any(token in text_lower or token in text for token in ["사건", "단서", "약속", "설정", "규칙", "상황", "비밀", "목표", "경계", "event", "clue", "promise", "setting", "rule", "situation", "secret", "goal", "boundary"]):
        return False
    has_positive = any(token in text or token in text_lower for token in DURABLE_MEMORY_POSITIVE_TOKENS)
    if memory_type in {"preference", "boundary"}:
        return has_positive or importance >= 4
    if memory_type == "open_hook":
        return any(token in text or token in text_lower for token in ["다음", "앞으로", "미해결", "오픈 훅", "단서", "목표", "계획", "next", "future", "unresolved", "hook", "clue", "goal", "plan"])
    return has_positive


def memory_already_exists(existing: list[CharacterMemory], content: str) -> bool:
    candidate = normalize_memory_fingerprint(content)
    if not candidate:
        return True
    for memory in existing:
        current = normalize_memory_fingerprint(memory.content)
        if not current:
            continue
        if candidate == current or candidate in current or current in candidate or memories_are_similar(memory.content, content):
            return True
    return False


def build_continuity_extraction_source(
    *,
    character_id: str,
    existing_memories: list[CharacterMemory],
    relationship_state: ConversationRelationshipState | None,
    user_message: Message | None,
    character_message: Message | None,
) -> str:
    lines = [
        f"character_id={character_id}",
        "Existing continuity:",
        build_continuity_context(existing_memories, relationship_state) or "none yet",
        "Newest turn:",
    ]
    if user_message:
        lines.append(f"counterpart message: speaker_type={user_message.speaker_type}; speaker_id={user_message.speaker_id}; {summarize_message_for_memory(user_message)}")
    if character_message:
        lines.append(f"character reply: speaker_id={character_message.speaker_id}; {summarize_message_for_memory(character_message)}")
    return "\n".join(lines)


def validate_continuity_update(data: dict | None) -> dict | None:
    if not isinstance(data, dict):
        return None
    if not any(key in data for key in ["memories_to_add", "state_patch", "unresolved_hooks"]):
        return None
    # LLM continuity extraction may update relationship state/hooks, but it must
    # never create long-term CharacterMemory rows. User notes are the only
    # supported long-term prompt memory.
    clean_memories = []
    state_patch = data.get("state_patch") or {}
    if not isinstance(state_patch, dict):
        state_patch = {}
    clean_patch = {}
    for key in ["trust_delta", "affinity_delta", "tension_delta", "conflict_delta", "cooperation_delta"]:
        try:
            clean_patch[key] = max(-2, min(2, int(state_patch.get(key) or 0)))
        except (TypeError, ValueError):
            clean_patch[key] = 0
    for key in ["current_mood"]:
        clean_patch[key] = compact_text(state_patch.get(key), 160)
    clean_patch["current_dynamic"] = clean_relationship_dynamic_for_storage(state_patch.get("current_dynamic"))
    hooks = data.get("unresolved_hooks") or state_patch.get("unresolved_hooks") or []
    if not isinstance(hooks, list):
        hooks = []
    clean_hooks = [cleaned for item in hooks if (cleaned := clean_relationship_hook_for_storage(item))][:3]
    return {"memories_to_add": clean_memories, "state_patch": clean_patch, "unresolved_hooks": clean_hooks}


async def extract_continuity_update_with_llm(
    *,
    character_id: str,
    existing_memories: list[CharacterMemory],
    relationship_state: ConversationRelationshipState | None,
    user_message: Message | None,
    character_message: Message | None,
    llm_client: LLMClient | None = None,
) -> dict | None:
    client = llm_client or LLMClient(profile="compression", purpose="continuity_extraction")
    source = build_continuity_extraction_source(
        character_id=character_id,
        existing_memories=existing_memories,
        relationship_state=relationship_state,
        user_message=user_message,
        character_message=character_message,
    )
    messages = [
        {
            "role": "system",
            "content": """You update generic continuity memory for a character-chat runtime.
Do not continue the conversation. Do not write roleplay.
Extract only durable facts, preferences, events, boundaries, and open hooks useful for later turns.
Keep it generic; do not force romance-specific interpretation.
Return only a JSON object with this shape:
{
  "memories_to_add": [{"memory_type":"fact|preference|event|boundary|open_hook", "content":"concise Korean or English memory", "importance":1-5}],
  "state_patch": {"trust_delta":-2..2, "affinity_delta":-2..2, "tension_delta":-2..2, "conflict_delta":-2..2, "cooperation_delta":-2..2, "current_mood":"", "current_dynamic":""},
  "unresolved_hooks": ["short hooks to continue later"]
}
Rules: Add 0-3 memories only. Prefer durable continuity over raw transcript. If nothing durable changed, return empty arrays and zero deltas.""",
        },
        {"role": "user", "content": source},
    ]
    try:
        response = await chat_with_optional_conversation_id(client, messages, response_format={"type": "json_object"}, conversation_id=relationship_state.conversation_id if relationship_state else None)
    except LLMUnavailableError:
        return None
    return validate_continuity_update(parse_json_object_from_text(response.content))


def apply_continuity_update(
    session: Session,
    *,
    conversation_id: str,
    character_id: str,
    update: dict,
    counterpart_type: str = "user",
    counterpart_id: str = "user_001",
) -> ConversationRelationshipState | None:
    if counterpart_type == "system" or counterpart_id == COMMON_ROOM_MEMORY_CHARACTER_ID:
        return None
    state = get_or_create_relationship_state(
        session,
        conversation_id,
        character_id,
        counterpart_type=counterpart_type,
        counterpart_id=counterpart_id,
    )
    existing_memories = list(session.exec(
        select(CharacterMemory)
        .where(CharacterMemory.conversation_id == conversation_id, CharacterMemory.character_id == character_id)
        .order_by(CharacterMemory.updated_at.desc())
        .limit(MAX_CONTEXT_MEMORY_SCAN_ITEMS * 4)
    ).all())
    for item in update.get("memories_to_add", []):
        content = item["content"]
        if not is_durable_room_memory(content, item["memory_type"], item["importance"]) or memory_already_exists(existing_memories, content):
            continue
        memory = CharacterMemory(
            id=f"mem_{uuid4().hex[:12]}",
            conversation_id=conversation_id,
            character_id=character_id,
            memory_type=item["memory_type"],
            content=content,
            importance=item["importance"],
        )
        session.add(memory)
        existing_memories.append(memory)
    patch = update.get("state_patch", {})
    state.trust_level = clamp_state_value(state.trust_level + int(patch.get("trust_delta") or 0))
    state.affinity_level = clamp_state_value(state.affinity_level + int(patch.get("affinity_delta") or 0))
    state.tension_level = clamp_state_value(state.tension_level + int(patch.get("tension_delta") or 0))
    state.conflict_level = clamp_state_value(state.conflict_level + int(patch.get("conflict_delta") or 0))
    state.cooperation_level = clamp_state_value(state.cooperation_level + int(patch.get("cooperation_delta") or 0))
    if patch.get("current_mood"):
        state.current_mood = patch["current_mood"]
    if patch.get("current_dynamic"):
        state.current_dynamic = patch["current_dynamic"]
    hooks = [cleaned for item in (update.get("unresolved_hooks") or []) if (cleaned := clean_relationship_hook_for_storage(item))]
    if hooks:
        merged = []
        for item in [*hooks, *[clean_relationship_hook_for_storage(existing) for existing in (state.unresolved_hooks or [])]]:
            if item and item not in merged:
                merged.append(item)
        state.unresolved_hooks = merged[:3]
    state.updated_at = datetime.now(timezone.utc)
    session.add(state)
    session.commit()
    session.refresh(state)
    return state


async def update_continuity_from_turn(
    session: Session,
    *,
    conversation_id: str,
    character_id: str,
    user_message: Message | None = None,
    character_message: Message | None = None,
    counterpart_type: str = "user",
    counterpart_id: str = "user_001",
    llm_client: LLMClient | None = None,
) -> ConversationRelationshipState | None:
    # Keep per-turn continuity lightweight. Durable memory extraction happens in
    # the batch compression pass so context does not grow on every bubble.
    return update_relationship_state_from_turn(
        session,
        conversation_id=conversation_id,
        character_id=character_id,
        user_message=user_message if counterpart_type != "system" else None,
        character_message=character_message,
        counterpart_type=counterpart_type,
        counterpart_id=counterpart_id,
    )

def build_continuity_context(memories: list[CharacterMemory] | None = None, relationship_state: ConversationRelationshipState | list[ConversationRelationshipState] | None = None) -> str:
    sections = []
    relationship_states = relationship_state if isinstance(relationship_state, list) else ([relationship_state] if relationship_state else [])
    relationship_states = [state for state in relationship_states if state.counterpart_type != "system"][:MAX_RELATIONSHIP_CONTEXT_ITEMS]
    for state in relationship_states:
        hooks = "; ".join(
            cleaned
            for item in (state.unresolved_hooks or [])[:1]
            if (cleaned := clean_relationship_hook_for_storage(item))
        )
        dynamic = compact_text(relationship_fact_for_display(state.current_dynamic), 90)
        if not dynamic and not hooks:
            continue
        sections.append("\n".join([
            "[Runtime relationships]",
            f"{state.counterpart_type}:{state.counterpart_id} | trust={state.trust_level}, affinity={state.affinity_level}, tension={state.tension_level}, conflict={state.conflict_level}, cooperation={state.cooperation_level}",
            f"dynamic={dynamic or '-'}",
            f"hooks={hooks or '-'}",
        ]))
    if memories:
        common_lines = [
            f"- {memory.memory_type}/{memory.importance}: {compact_text(memory.content, 100)}"
            for memory in memories
            if memory.character_id == COMMON_ROOM_MEMORY_CHARACTER_ID
        ]
        character_lines = [
            f"- {memory.memory_type}/{memory.importance}: {compact_text(memory.content, 100)}"
            for memory in memories
            if memory.character_id != COMMON_ROOM_MEMORY_CHARACTER_ID
        ]
        if common_lines:
            sections.append("[Common room memory known by all characters]\n" + "\n".join(common_lines[:MAX_CHARACTER_MEMORY_ITEMS]))
        if character_lines:
            sections.append("[Long-term character memory]\n" + "\n".join(character_lines[:MAX_CHARACTER_MEMORY_ITEMS]))
    return "\n\n".join(sections)


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
) -> AutomaticContextPlan:
    suffix = messages_after_compression_boundary(scene_state, messages)
    return build_automatic_context_plan(
        capacity=capacity,
        mandatory_prompt_tokens=mandatory_prompt_tokens,
        messages=suffix,
        job_source_message_ids=job_source_message_ids,
        token_estimator=token_estimator or automatic_context_message_tokens,
        batch_message_limit=COMPRESSION_BATCH_MESSAGE_LIMIT,
    )


def automatic_context_message_tokens(
    message: Message,
    *,
    character_names: Mapping[str, str] | None = None,
) -> int:
    """Estimate the exact message fields rendered into automatic raw history."""

    return max(
        1,
        prompts.approx_tokens(
            prompts.format_message_for_context(
                message,
                include_thought=False,
                character_names=dict(character_names or {}),
            )
        ) + 1,
    )


def automatic_context_message_token_estimator(
    character_names: Mapping[str, str] | None = None,
) -> Callable[[Message], int]:
    names = dict(character_names or {})

    def estimate(message: Message) -> int:
        return automatic_context_message_tokens(message, character_names=names)

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


async def summarize_scene_memory_with_llm(
    scene_state: SceneState,
    recent_messages: list[Message],
    llm_client: LLMClient | None = None,
    *,
    memories: list[CharacterMemory] | None = None,
    prompt_settings: dict[str, str] | None = None,
) -> str | None:
    client = llm_client or LLMClient(profile="compression", purpose="scene_compression")
    source = build_scene_memory_source(scene_state, recent_messages, include_compression_focus=False, memories=memories or [])
    settings = prompt_settings or system_prompt_service.default_prompt_settings_map()
    system_prompt = "\n\n".join(
        part.strip()
        for part in [
            settings.get("compression_scene_base", ""),
            settings.get("compression_source_scope_rules", ""),
        ]
        if part and part.strip()
    )
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": source},
    ]
    invalid_draft = ""
    invalid_reason = ""
    for attempt in range(2):
        attempt_messages = messages
        if attempt:
            attempt_messages = [
                *messages,
                {"role": "assistant", "content": invalid_draft},
                {
                    "role": "user",
                    "content": (
                        "[System retry instruction]\n"
                        f"The previous draft failed validation ({invalid_reason}). Rewrite it semantically from the original sources. "
                        "Do not cut sentences, truncate bullets, or merely keep the first 18 bullets. Merge neighboring details into larger major events and rewrite each event as one complete line. "
                        "Return one complete [Rolling Story Arc] with at most 18 bullets, at most 180 characters per bullet, at most 2200 characters total, and no other headings or body lines."
                    ),
                },
            ]
        try:
            response = await chat_with_optional_conversation_id(
                client,
                attempt_messages,
                conversation_id=scene_state.conversation_id,
            )
        except LLMUnavailableError as exc:
            raise RuntimeError(f"scene compression LLM unavailable: {compression_error_summary(exc)}") from exc
        invalid_draft = response.content or ""
        summary = validate_scene_memory_summary(invalid_draft)
        copied_raw = bool(summary and scene_summary_copies_recent_raw_text(summary, recent_messages))
        if summary and not copied_raw:
            return summary
        invalid_reason = "copied recent raw text" if copied_raw else "format/density limits"
        logger.warning(
            "Scene compression response failed validation for conversation %s: attempt=%s reason=%s len=%s preview=%r",
            scene_state.conversation_id,
            attempt + 1,
            invalid_reason,
            len(response.content or ""),
            compact_text(response.content, 300),
        )
    return None


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
) -> dict | None:
    """Run graph-orchestrated compression so timeline, memory, and relationships do not contaminate each other."""
    from app.engine.compression_graph import run_compression_graph

    async def summarize_scene_callback(state: dict) -> str:
        scene_summary = await summarize_scene_memory_with_llm(scene_state, recent_messages, llm_client=llm_client, memories=memories, prompt_settings=prompt_settings)
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


def _claim_compression_result(
    session: Session,
    conversation_id: str,
    *,
    source_message_id: str | None,
    expected_revision: int | None,
    expected_boundary_message_id: str | None = None,
    folded_message_ids: list[str] | None = None,
) -> bool:
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
        ):
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


def update_message_bubble(session: Session, message: Message, changes: dict) -> Message:
    editable_fields = {"content", "emotion", "action", "thought"}
    normalized_changes = {key: changes[key] for key in editable_fields if key in changes}
    if not normalized_changes:
        raise ValueError("At least one editable bubble field is required")

    content = message.content or ""
    action = message.action or ""
    thought = message.thought or ""
    emotion = message.emotion or ""
    if "content" in normalized_changes:
        content = str(normalized_changes["content"] or "").strip()
    if "action" in normalized_changes:
        action = str(normalized_changes["action"] or "").strip()
    if "thought" in normalized_changes:
        thought = str(normalized_changes["thought"] or "").strip()
    if "emotion" in normalized_changes:
        emotion = str(normalized_changes["emotion"] or "").strip()
    if not any((content, action, thought)):
        raise ValueError("A bubble must keep at least one dialogue, action, or thought value")

    metadata = dict(message.metadata_ or {})
    render_parts = []
    if action:
        render_parts.append({"type": "action", "text": action})
    if content:
        render_parts.append({"type": "dialogue", "text": content})
    if thought:
        render_parts.append({"type": "thought", "text": thought})
    metadata["render_parts"] = render_parts
    for key in ["tts_audio_url", "tts_text", "tts_voice_style", "tts_provider", "tts_model"]:
        metadata.pop(key, None)
    metadata["manual_edit"] = {
        "edited_at": datetime.now(timezone.utc).isoformat(),
        "fields": sorted(normalized_changes),
    }

    message.content = content
    message.action = action or None
    message.thought = thought or None
    message.emotion = emotion or None
    message.metadata_ = metadata
    session.add(message)
    session.commit()
    session.refresh(message)
    return message


def record_system_scene_direction(session: Session, conversation_id: str, content: str) -> SceneState:
    scene_state = session.get(SceneState, conversation_id)
    if not scene_state:
        scene_state = SceneState(conversation_id=conversation_id)
        session.add(scene_state)
    # The system message itself remains in the bounded raw tail. Do not also
    # append it to the compressed summary; it will be folded exactly once when
    # it ages past the persisted compression boundary.
    direction = f"시스템 지시: {compact_text(content, 260)}"
    scene_state.last_event = direction
    scene_state.updated_at = datetime.now(timezone.utc)
    session.add(scene_state)
    session.commit()
    session.refresh(scene_state)
    return scene_state


async def update_scene_orchestration_summary(
    session: Session,
    conversation_id: str,
    recent_messages: list[Message],
    llm_client: LLMClient | None = None,
    fallback_llm_client: LLMClient | None = None,
    character_ids: list[str] | None = None,
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
    genre_mode = normalize_genre_mode(getattr(conversation, "genre_mode", None)) if conversation else DEFAULT_GENRE_MODE
    official_domain_context = genre_domain_service.get_official_context(session, conversation) if conversation else ""
    room_cast_roles = {
        participant.participant_id: participant.role
        for participant in get_participants(session, conversation_id)
        if participant.participant_type == "character" and participant.role
    }
    prompt_settings = system_prompt_service.prompt_settings_map()
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
        )
        session.expire_all()
        scene_state = session.get(SceneState, conversation_id) or scene_state
        if applied:
            scene_state.last_compression_error = None
            session.add(scene_state)
            session.commit()
            session.refresh(scene_state)
    return scene_state


def delete_conversation(session: Session, conversation_id: str) -> bool:
    conversation = session.get(Conversation, conversation_id)
    if not conversation:
        return False
    session.execute(text("DELETE FROM message_assets WHERE message_id IN (SELECT id FROM messages WHERE conversation_id = :conversation_id)"), {"conversation_id": conversation_id})
    session.exec(delete(Message).where(Message.conversation_id == conversation_id))
    session.exec(delete(CharacterMemory).where(CharacterMemory.conversation_id == conversation_id))
    session.exec(delete(BattleMatchRecord).where(BattleMatchRecord.conversation_id == conversation_id))
    session.exec(delete(BattleStanding).where(BattleStanding.conversation_id == conversation_id))
    session.exec(delete(ConversationRelationshipState).where(ConversationRelationshipState.conversation_id == conversation_id))
    session.exec(delete(ConversationParticipant).where(ConversationParticipant.conversation_id == conversation_id))
    session.execute(text("DELETE FROM llm_prompt_snapshots WHERE conversation_id = :conversation_id"), {"conversation_id": conversation_id})
    session.execute(text("DELETE FROM llm_usage_events WHERE conversation_id = :conversation_id"), {"conversation_id": conversation_id})
    session.exec(delete(RuntimeSetting).where(RuntimeSetting.conversation_id == conversation_id))
    scene_state = session.get(SceneState, conversation_id)
    if scene_state:
        session.delete(scene_state)
    session.delete(conversation)
    session.commit()
    return True
