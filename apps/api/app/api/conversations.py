from datetime import datetime
import asyncio
import json
import hashlib
from uuid import uuid4
import logging
from fastapi import BackgroundTasks, APIRouter, Depends, Header, HTTPException, Query, Request, Response
from fastapi.responses import StreamingResponse
from sqlmodel import Session, col, select
from app.db.models import (
    CharacterMemory,
    Conversation,
    ConversationParticipant,
    GenerationJobEvent,
    Message,
    MessageGenerationJob,
    SceneState,
)
from app.db.session import get_session
from app.engine import prompts
from app.engine.llm_client import LLMClient, LLMUnavailableError
from app.engine.input_markup import parse_input_markup
from app.schemas.generation_jobs import GenerationJobRetryRequest, ActiveGenerationJobsRead
from app.services.hardening_core_transactions import acceptance_transaction
from app.db.models import GenerationAcceptanceReceipt
from sqlalchemy import update as sa_update, func
from app.schemas.conversations import (
    CharacterMemoryCreate,
    CharacterMemoryRead,
    CharacterMemoryUpdate,
    ConversationCompressionPreviewRead,
    ConversationContextRead,
    ConversationContextPreviewRead,
    ConversationCreate,
    ConversationRead,
    ConversationReadMarker,
    ConversationUpdate,
    ConversationUsageRead,
    MessageBulkDeleteRead,
    MessageBulkDeleteRequest,
    MessageCreate,
    MessageGenerationJobCreateRead,
    MessageGenerationJobRead,
    MessageRegenerateRequest,
    MessageUpdate,
    ParticipantCreate,
    ParticipantUpdate,
    MessageRead,
    ParticipantRead,
    RelationshipStateRead,
    SceneStateRead,
    SceneSummaryUpdate,
)
from app.core.config import get_settings
from app.services import (
    asset_service,
    battle_ledger_service,
    character_service,
    chat_command_service,
    context_preview_service,
    conversation_service,
    runtime_settings_service,
    usage_service,
)
from app.services.context_management_service import ContextMaintenanceRequired
from app.services.generation_dispatcher import generation_dispatcher

from app.services import generation_execution


router = APIRouter(prefix="/conversations", tags=["conversations"])
logger = logging.getLogger(__name__)


@router.post("", response_model=ConversationRead)
def create_conversation(payload: ConversationCreate, session: Session = Depends(get_session)):
    try:
        conversation = conversation_service.create_conversation(session, payload)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return conversation_service.serialize_conversation_for_list(session, conversation)


@router.get("", response_model=list[ConversationRead])
def list_conversations(session: Session = Depends(get_session)):
    return [conversation_service.serialize_conversation_for_list(session, conversation) for conversation in conversation_service.list_conversations(session)]


@router.get("/{conversation_id}", response_model=ConversationRead)
def get_conversation(conversation_id: str, session: Session = Depends(get_session)):
    conversation = conversation_service.get_conversation(session, conversation_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return conversation_service.serialize_conversation_for_list(session, conversation)


@router.patch("/{conversation_id}", response_model=ConversationRead)
def update_conversation(conversation_id: str, payload: ConversationUpdate, session: Session = Depends(get_session)):
    try:
        conversation = conversation_service.update_conversation(session, conversation_id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return conversation_service.serialize_conversation_for_list(session, conversation)


@router.post("/{conversation_id}/read", response_model=ConversationReadMarker)
def mark_conversation_read(conversation_id: str, session: Session = Depends(get_session)):
    marker = conversation_service.mark_conversation_read(session, conversation_id)
    if not marker:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return ConversationReadMarker(
        conversation_id=marker.conversation_id,
        user_id=marker.user_id,
        last_read_message_id=marker.last_read_message_id,
        last_read_at=marker.last_read_at,
    )


@router.delete("/{conversation_id}", status_code=204)
def delete_conversation(conversation_id: str, session: Session = Depends(get_session)):
    deleted = conversation_service.delete_conversation(session, conversation_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return Response(status_code=204)


@router.post("/{conversation_id}/active-command/clear", response_model=ConversationRead)
def clear_active_command(conversation_id: str, session: Session = Depends(get_session)):
    conversation = session.get(Conversation, conversation_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")
    chat_command_service.set_active_command_ids(session, conversation, [])
    session.refresh(conversation)
    return conversation


@router.post("/{conversation_id}/active-command/{command_id}/clear", response_model=ConversationRead)
def clear_one_active_command(conversation_id: str, command_id: str, session: Session = Depends(get_session)):
    conversation = session.get(Conversation, conversation_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")
    active_ids = [item for item in chat_command_service.active_command_ids_for_conversation(conversation) if item != command_id]
    chat_command_service.set_active_command_ids(session, conversation, active_ids)
    session.refresh(conversation)
    return conversation


@router.post("/{conversation_id}/participants", response_model=ParticipantRead)
def add_participant(conversation_id: str, payload: ParticipantCreate, session: Session = Depends(get_session)):
    character = character_service.get_character(session, payload.id) if payload.type == "character" else None
    if payload.type == "character" and not character:
        raise HTTPException(status_code=404, detail="Character not found")
    already_joined = any(
        item.participant_type == payload.type and item.participant_id == payload.id
        for item in conversation_service.get_participants(session, conversation_id)
    )
    participant = conversation_service.add_participant(session, conversation_id, payload)
    if not participant:
        raise HTTPException(status_code=404, detail="Conversation not found")
    if payload.type == "character" and character and not already_joined:
        conversation_service.add_message(
            session,
            conversation_id,
            MessageCreate(speaker_type="system", speaker_id="system", content=f"{character.name}님이 들어왔습니다."),
        )
    return ParticipantRead(
        type=participant.participant_type,
        id=participant.participant_id,
        role=participant.role,
        order_index=participant.order_index,
    )


@router.delete("/{conversation_id}/participants/{participant_type}/{participant_id}", status_code=204)
def remove_participant(conversation_id: str, participant_type: str, participant_id: str, session: Session = Depends(get_session)):
    if participant_type != "character":
        raise HTTPException(status_code=422, detail="Only character participants can be removed from the room")
    character = character_service.get_character(session, participant_id)
    removed = conversation_service.remove_participant(session, conversation_id, participant_type, participant_id)
    if removed is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    if not removed:
        raise HTTPException(status_code=404, detail="Participant not found")
    conversation_service.add_message(
        session,
        conversation_id,
        MessageCreate(
            speaker_type="system",
            speaker_id="system",
            content=f"{character.name if character else '캐릭터'}님이 나갔습니다.",
        ),
    )
    return Response(status_code=204)


@router.patch("/{conversation_id}/participants/{participant_type}/{participant_id}", response_model=ParticipantRead)
def update_participant(conversation_id: str, participant_type: str, participant_id: str, payload: ParticipantUpdate, session: Session = Depends(get_session)):
    if participant_type != "character":
        raise HTTPException(status_code=422, detail="Only character participant roles can be updated")
    participant = session.get(ConversationParticipant, (conversation_id, participant_type, participant_id))
    if not participant:
        if not conversation_service.get_conversation(session, conversation_id):
            raise HTTPException(status_code=404, detail="Conversation not found")
        raise HTTPException(status_code=404, detail="Participant not found")
    participant.role = (payload.role or "").strip() or None
    session.add(participant)
    session.commit()
    session.refresh(participant)
    return ParticipantRead(
        type=participant.participant_type,
        id=participant.participant_id,
        role=participant.role,
        order_index=participant.order_index,
    )


@router.get("/{conversation_id}/participants", response_model=list[ParticipantRead])
def list_participants(conversation_id: str, session: Session = Depends(get_session)):
    if not conversation_service.get_conversation(session, conversation_id):
        raise HTTPException(status_code=404, detail="Conversation not found")
    return [
        ParticipantRead(
            type=participant.participant_type,
            id=participant.participant_id,
            role=participant.role,
            order_index=participant.order_index,
        )
        for participant in conversation_service.get_participants(session, conversation_id)
    ]


@router.get("/{conversation_id}/messages", response_model=list[MessageRead])
def list_messages(
    conversation_id: str,
    limit: int | None = Query(default=None, ge=1, le=200),
    before_id: str | None = None,
    recent_turns: int | None = Query(default=None, ge=1, le=50),
    message_ids: list[str] | None = Query(default=None, min_length=1, max_length=100),
    session: Session = Depends(get_session),
):
    if not conversation_service.get_conversation(session, conversation_id):
        raise HTTPException(status_code=404, detail="Conversation not found")
    if message_ids is not None:
        # Bounded late-asset hydration cannot fetch another room's messages or
        # resurrect deleted rows. It is independent of the paginated tail.
        messages = list(session.exec(select(Message).where(
            Message.conversation_id == conversation_id,
            Message.id.in_(message_ids),
        ).order_by(Message.created_at, Message.id)).all())
    else:
        messages = conversation_service.list_messages(
            session,
            conversation_id,
            limit=limit,
            before_id=before_id,
            recent_turns=recent_turns,
        )
    pending_ids = asset_service.pending_asset_decision_message_ids(session, conversation_id)
    return [asset_service.serialize_message(session, message, pending_decision_ids=pending_ids) for message in messages]


@router.patch("/{conversation_id}/messages/{message_id}", response_model=MessageRead)
def update_message_bubble(
    conversation_id: str,
    message_id: str,
    payload: MessageUpdate,
    session: Session = Depends(get_session),
):
    if not conversation_service.get_conversation(session, conversation_id):
        raise HTTPException(status_code=404, detail="Conversation not found")
    message = session.get(Message, message_id)
    if not message or message.conversation_id != conversation_id:
        raise HTTPException(status_code=404, detail="Message not found")
    changes = payload.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(status_code=422, detail="At least one editable bubble field is required")
    try:
        updated = conversation_service.update_message_bubble(session, message, changes)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return asset_service.serialize_message(session, updated)


@router.get("/{conversation_id}/context", response_model=ConversationContextRead)
def get_conversation_context(conversation_id: str, session: Session = Depends(get_session)):
    conversation = conversation_service.get_conversation(session, conversation_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")
    scene = conversation_service.build_live_scene_state(session, conversation, session.get(SceneState, conversation_id))
    active_participant_ids = {
        participant.participant_id
        for participant in conversation_service.get_participants(session, conversation_id)
        if participant.participant_type == "character"
    }
    relationships = []
    visible_memory_character_ids = conversation_service.visible_memory_character_ids(active_participant_ids)
    raw_memories = list(
        session.exec(
            select(CharacterMemory)
            .where(
                CharacterMemory.conversation_id == conversation_id,
                CharacterMemory.character_id.in_(visible_memory_character_ids),
                CharacterMemory.memory_type == "user_note",
            )
            .order_by(CharacterMemory.importance.desc(), CharacterMemory.updated_at.desc())
        ).all()
    )
    memories = []
    seen_memory_fingerprints = set()
    for item in raw_memories:
        if not conversation_service.is_durable_room_memory(item.content, item.memory_type, item.importance):
            continue
        fingerprint = conversation_service.normalize_memory_fingerprint(item.content)
        if not fingerprint or fingerprint in seen_memory_fingerprints:
            continue
        seen_memory_fingerprints.add(fingerprint)
        memories.append(item)
    return ConversationContextRead(
        scene=SceneStateRead(
            location=scene.location,
            time_label=scene.time_label,
            mood=scene.mood,
            world_seed=scene.world_seed,
            opening_scene=scene.opening_scene,
            opening_line=scene.opening_line,
            tone_preset=scene.tone_preset,
            relationship_archetype=scene.relationship_archetype,
            current_conflict=scene.current_conflict,
            compression_focus=scene.compression_focus,
            last_event=scene.last_event,
            tension_level=scene.tension_level,
            romance_level=scene.romance_level,
            user_description=scene.user_description,
            summary=scene.summary,
            last_compression_error=scene.last_compression_error,
            last_compression_attempt_at=scene.last_compression_attempt_at,
            last_compressed_at=scene.last_compressed_at,
            last_compression_source_message_id=scene.last_compression_source_message_id,
            compression_revision=scene.compression_revision,
        ) if scene else None,
        relationships=[
            RelationshipStateRead(
                character_id=item.character_id,
                counterpart_type=item.counterpart_type,
                counterpart_id=item.counterpart_id,
                trust_level=item.trust_level,
                affinity_level=item.affinity_level,
                tension_level=item.tension_level,
                conflict_level=item.conflict_level,
                cooperation_level=item.cooperation_level,
                current_mood=item.current_mood,
                current_dynamic=item.current_dynamic,
                relationship_fact=conversation_service.relationship_fact_for_display(item.current_dynamic),
                unresolved_hooks=item.unresolved_hooks or [],
            )
            for item in relationships
        ],
        memories=[
            CharacterMemoryRead(
                id=item.id,
                character_id=item.character_id,
                memory_type=item.memory_type,
                content=item.content,
                importance=item.importance,
            )
            for item in memories
        ],
    )


@router.patch("/{conversation_id}/scene-summary", response_model=SceneStateRead)
def update_conversation_scene_summary(
    conversation_id: str,
    payload: SceneSummaryUpdate,
    session: Session = Depends(get_session),
):
    if not conversation_service.get_conversation(session, conversation_id):
        raise HTTPException(status_code=404, detail="Conversation not found")
    try:
        scene = conversation_service.update_scene_summary_manually(
            session,
            conversation_id,
            summary=payload.summary,
            expected_revision=payload.expected_revision,
        )
    except conversation_service.SceneSummaryRevisionConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if scene is None:
        raise HTTPException(status_code=404, detail="Scene state not found")
    return SceneStateRead(**{
        field_name: getattr(scene, field_name)
        for field_name in SceneStateRead.model_fields
    })


@router.post("/{conversation_id}/compress-now", response_model=SceneStateRead)
async def compress_conversation_now(conversation_id: str, session: Session = Depends(get_session)):
    conversation = conversation_service.get_conversation(session, conversation_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")
    runtime_setting = runtime_settings_service.get_effective_setting(session, conversation_id)
    compression_overrides = runtime_settings_service.compression_llm_overrides_for_setting(runtime_setting, session=session)
    compression_fallback_overrides = runtime_settings_service.compression_fallback_llm_overrides_for_setting(runtime_setting, session=session)
    character_ids = [
        participant.participant_id
        for participant in conversation_service.get_participants(session, conversation_id)
        if participant.participant_type == "character"
    ]
    scene = await conversation_service.update_scene_orchestration_summary(
        session,
        conversation_id,
        conversation_service.list_messages(session, conversation_id),
        llm_client=LLMClient(profile="compression", purpose="conversation_compression", overrides=compression_overrides),
        fallback_llm_client=(
            LLMClient(profile="compression", purpose="conversation_compression_fallback", overrides=compression_fallback_overrides)
            if compression_fallback_overrides else None
        ),
        character_ids=character_ids,
        compression_strategy=conversation_service.normalize_compression_strategy(
            getattr(runtime_setting, "compression_strategy", runtime_settings_service.DEFAULT_COMPRESSION_STRATEGY)
        ),
    )
    return SceneStateRead(
        location=scene.location,
        time_label=scene.time_label,
        mood=scene.mood,
        world_seed=scene.world_seed,
        opening_scene=scene.opening_scene,
        opening_line=scene.opening_line,
        tone_preset=scene.tone_preset,
        relationship_archetype=scene.relationship_archetype,
        current_conflict=scene.current_conflict,
        compression_focus=scene.compression_focus,
        last_event=scene.last_event,
        tension_level=scene.tension_level,
        romance_level=scene.romance_level,
        user_description=scene.user_description,
        summary=scene.summary,
        last_compression_error=scene.last_compression_error,
        last_compression_attempt_at=scene.last_compression_attempt_at,
        last_compressed_at=scene.last_compressed_at,
        last_compression_source_message_id=scene.last_compression_source_message_id,
        compression_revision=scene.compression_revision,
    )


def read_memory(memory: CharacterMemory) -> CharacterMemoryRead:
    return CharacterMemoryRead(
        id=memory.id,
        character_id=memory.character_id,
        memory_type=memory.memory_type,
        content=memory.content,
        importance=memory.importance,
    )


def validate_memory_character_id(session: Session, conversation_id: str, character_id: str | None) -> None:
    normalized_character_id = conversation_service.normalize_memory_character_id(character_id)
    if normalized_character_id == conversation_service.COMMON_ROOM_MEMORY_CHARACTER_ID:
        return
    character_ids = {
        participant.participant_id
        for participant in conversation_service.get_participants(session, conversation_id)
        if participant.participant_type == "character"
    }
    if normalized_character_id not in character_ids:
        raise HTTPException(status_code=422, detail="Memory character_id must be a room memory id or an active character id")


@router.post("/{conversation_id}/memories", response_model=CharacterMemoryRead)
def create_conversation_memory(conversation_id: str, payload: CharacterMemoryCreate, session: Session = Depends(get_session)):
    if not conversation_service.get_conversation(session, conversation_id):
        raise HTTPException(status_code=404, detail="Conversation not found")
    validate_memory_character_id(session, conversation_id, payload.character_id)
    memory = conversation_service.add_character_memory(
        session,
        conversation_id,
        character_id=payload.character_id,
        memory_type=payload.memory_type,
        content=payload.content,
        importance=payload.importance,
    )
    return read_memory(memory)


@router.patch("/{conversation_id}/memories/{memory_id}", response_model=CharacterMemoryRead)
def update_conversation_memory(conversation_id: str, memory_id: str, payload: CharacterMemoryUpdate, session: Session = Depends(get_session)):
    if not conversation_service.get_conversation(session, conversation_id):
        raise HTTPException(status_code=404, detail="Conversation not found")
    memory = conversation_service.get_character_memory(session, memory_id)
    if not memory or memory.conversation_id != conversation_id:
        raise HTTPException(status_code=404, detail="Memory not found")
    if payload.character_id is not None:
        validate_memory_character_id(session, conversation_id, payload.character_id)
    data = payload.model_dump(exclude_unset=True)
    memory = conversation_service.update_character_memory(session, memory, **data)
    return read_memory(memory)


@router.delete("/{conversation_id}/memories/{memory_id}", status_code=204)
def delete_conversation_memory(conversation_id: str, memory_id: str, session: Session = Depends(get_session)):
    if not conversation_service.get_conversation(session, conversation_id):
        raise HTTPException(status_code=404, detail="Conversation not found")
    memory = conversation_service.get_character_memory(session, memory_id)
    if not memory or memory.conversation_id != conversation_id:
        raise HTTPException(status_code=404, detail="Memory not found")
    conversation_service.delete_character_memory(session, memory)
    return Response(status_code=204)


@router.get("/{conversation_id}/usage", response_model=ConversationUsageRead)
def get_conversation_usage(
    conversation_id: str,
    start_at: datetime | None = Query(default=None),
    end_at: datetime | None = Query(default=None),
    session: Session = Depends(get_session),
):
    if not conversation_service.get_conversation(session, conversation_id):
        raise HTTPException(status_code=404, detail="Conversation not found")
    return usage_service.summarize_usage(session, conversation_id=conversation_id, start_at=start_at, end_at=end_at)


@router.get("/usage/summary", response_model=ConversationUsageRead)
def get_all_conversation_usage(
    start_at: datetime | None = Query(default=None),
    end_at: datetime | None = Query(default=None),
    session: Session = Depends(get_session),
):
    return usage_service.summarize_usage(session, start_at=start_at, end_at=end_at)


@router.get("/{conversation_id}/context-preview", response_model=ConversationContextPreviewRead)
def get_context_preview(conversation_id: str, session: Session = Depends(get_session)):
    conversation = conversation_service.get_conversation(session, conversation_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")
    try:
        return context_preview_service.build_context_preview(session, conversation_id)
    except prompts.ContextCoverageError as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": exc.code},
        ) from exc


@router.get("/{conversation_id}/compression-preview", response_model=ConversationCompressionPreviewRead)
def get_compression_preview(conversation_id: str, session: Session = Depends(get_session)):
    conversation = conversation_service.get_conversation(session, conversation_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return context_preview_service.build_compression_preview(session, conversation_id)


@router.get("/{conversation_id}/battle-state")
def get_battle_state(conversation_id: str, session: Session = Depends(get_session)):
    conversation = conversation_service.get_conversation(session, conversation_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")
    if not battle_ledger_service.is_battle_conversation(conversation):
        raise HTTPException(status_code=404, detail="Battle state is only available for battle conversations")
    return battle_ledger_service.get_battle_state(session, conversation)

def normalize_incoming_message(payload: MessageCreate) -> MessageCreate:
    parsed = parse_input_markup(payload.content)
    if not parsed.had_markup:
        return payload
    action_parts = [part for part in [payload.action, parsed.action] if part]
    metadata = dict(payload.metadata or {})
    metadata.setdefault("render_parts", [{"type": part.type, "text": part.text} for part in parsed.parts])
    return MessageCreate(
        speaker_type=payload.speaker_type,
        speaker_id=payload.speaker_id,
        content=parsed.dialogue,
        action="\n".join(action_parts) if action_parts else None,
        thought=payload.thought,
        metadata=metadata,
        battle_control=payload.battle_control,
    )


_PUBLIC_GENERATION_ERROR_CODES = {"context_maintenance_required"}


def _public_generation_error_code(job) -> str | None:
    error_message = str(getattr(job, "error_message", "") or "")
    prefix = error_message.split(":", 1)[0].strip()
    return prefix if prefix in _PUBLIC_GENERATION_ERROR_CODES else None


def _serialize_generation_job(job) -> MessageGenerationJobRead:
    return MessageGenerationJobRead(
        id=job.id,
        conversation_id=job.conversation_id,
        incoming_message_id=job.incoming_message_id,
        status=job.status,
        error_message=job.error_message,
        error_code=_public_generation_error_code(job),
        generated_message_ids=list(job.generated_message_ids or []),
        attempt_count=job.attempt_count,
        state_version=job.state_version,
        heartbeat_at=job.heartbeat_at,
        lease_expires_at=job.lease_expires_at,
        cancel_requested_at=job.cancel_requested_at,
        created_at=job.created_at,
        updated_at=job.updated_at,
        completed_at=job.completed_at,
    )


def _with_persisted_battle_control(payload: MessageCreate) -> MessageCreate:
    """Keep async generation control state with its durable source message."""
    if not payload.battle_control:
        return payload
    return payload.model_copy(update={
        "metadata": {
            **(payload.metadata or {}),
            "battle_control": payload.battle_control.model_dump(exclude_none=True),
        },
    })


def _prepare_message_payload_for_post(session: Session, conversation: Conversation, payload: MessageCreate) -> MessageCreate:
    payload = normalize_incoming_message(payload)
    if payload.speaker_type in {"user", "system"} and payload.content.strip().startswith("!"):
        parsed_command = chat_command_service.parse_chat_command(session, payload.content)
        if not parsed_command:
            raise HTTPException(status_code=422, detail="지원하지 않는 커맨드입니다. 커맨드 관리에서 활성화된 !커맨드만 사용할 수 있습니다.")
        active_ids = chat_command_service.add_active_command(session, conversation, parsed_command.command)
        session.refresh(conversation)
        active_commands = []
        for command_id in active_ids:
            command = chat_command_service.get_chat_command(session, command_id)
            if command and command.enabled:
                active_commands.append(command)
        return payload.model_copy(update={
            "content": parsed_command.args,
            "metadata": {
                **(payload.metadata or {}),
                **chat_command_service.command_metadata(parsed_command),
                **chat_command_service.active_commands_metadata(active_commands),
                "command_ids": [command.id for command in active_commands],
            },
        })
    active_ids = chat_command_service.active_command_ids_for_conversation(conversation)
    active_commands = []
    active_enabled_ids = []
    for command_id in active_ids:
        active_command = chat_command_service.get_chat_command(session, command_id)
        if active_command and active_command.enabled:
            active_commands.append(active_command)
            active_enabled_ids.append(active_command.id)
    if active_enabled_ids != active_ids:
        chat_command_service.set_active_command_ids(session, conversation, active_enabled_ids)
        session.refresh(conversation)
    if active_commands:
        return payload.model_copy(update={
            "metadata": {
                **(payload.metadata or {}),
                **chat_command_service.active_commands_metadata(active_commands),
                "command_ids": [command.id for command in active_commands],
            },
        })
    return payload


def _start_message_generation_worker(_job_id: str) -> None:
    generation_dispatcher.wake()


def _wake_accepted_job(job_id, session, background_tasks, execution_mode: str | None):
    # Acceptance flags select intent only when creating work. Once durable,
    # that captured intent owns dispatch even if the app acceptance flag changes.
    if execution_mode == "mock":
        if get_settings().llm_mock:
            background_tasks.add_task(generation_execution.run_mock_generation_queue, session.get_bind())
    else:
        _start_message_generation_worker(job_id)


def _admit_generation_backlog(session, conversation_id):
    active = MessageGenerationJob.status.in_(["queued", "running"])
    global_count = session.exec(select(func.count()).select_from(MessageGenerationJob).where(active)).one()
    room_count = session.exec(select(func.count()).select_from(MessageGenerationJob).where(active, MessageGenerationJob.conversation_id == conversation_id)).one()
    # Admission is checked under the acceptance write lock, not a racy preflight.
    if global_count >= 1000 or room_count >= 100:
        raise HTTPException(status_code=429, detail="generation backlog capacity reached", headers={"Retry-After": "1"})


@router.post("/{conversation_id}/messages/jobs", response_model=MessageGenerationJobCreateRead)
async def create_message_generation_job(
    conversation_id: str,
    payload: MessageCreate,
    request: Request,
    background_tasks: BackgroundTasks,
    session: Session = Depends(get_session),
):
    metadata = payload.metadata if isinstance(payload.metadata, dict) else {}
    request_id = metadata.get("client_request_id")
    if request_id is not None and (not isinstance(request_id, str) or not request_id.strip() or len(request_id) > 200):
        raise HTTPException(status_code=422, detail="client_request_id must be a nonblank string of at most 200 characters")
    digest = hashlib.sha256(json.dumps(payload.model_dump(mode="json"), sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
    # All source, command activation, battle/system effects, job and receipt
    # commits below are savepoints inside one short durable transaction.
    with acceptance_transaction(session) as accept:
        conversation = conversation_service.get_conversation(accept, conversation_id)
        if not conversation:
            raise HTTPException(status_code=404, detail="Conversation not found")
        receipt = accept.exec(select(GenerationAcceptanceReceipt).where(
            GenerationAcceptanceReceipt.conversation_id == conversation_id,
            GenerationAcceptanceReceipt.client_request_id == "send:" + request_id,
        )).first() if request_id else None
        if receipt:
            if receipt.payload_sha256 != digest:
                raise HTTPException(status_code=409, detail="client_request_id payload conflict")
            incoming_message = accept.get(Message, receipt.incoming_message_id)
            job = accept.get(MessageGenerationJob, receipt.job_id)
            if incoming_message is None or job is None:
                raise HTTPException(status_code=410, detail="accepted source/job was deleted; it will not be recreated")
        else:
            new_source = False
            incoming_message = conversation_service.find_incoming_message_by_client_request_id(accept, conversation_id, request_id)
            if incoming_message:
                # Legacy orphan reconciliation reuses the source without rerunning
                # effects that may already have committed before this release.
                if (incoming_message.speaker_type, incoming_message.speaker_id, incoming_message.content, incoming_message.action or "", incoming_message.thought or "") != (payload.speaker_type, payload.speaker_id, payload.content, payload.action or "", payload.thought or ""):
                    raise HTTPException(status_code=409, detail="legacy client_request_id payload conflict")
                job = conversation_service.get_message_generation_job_for_incoming_message(accept, incoming_message.id)
                if job is None:
                    _admit_generation_backlog(accept, conversation_id)
                    job = conversation_service.create_message_generation_job(accept, conversation_id, incoming_message.id)
            else:
                _admit_generation_backlog(accept, conversation_id)
                prepared = _with_persisted_battle_control(_prepare_message_payload_for_post(accept, conversation, payload))
                if not prepared.content.strip() and not (prepared.action or "").strip():
                    raise HTTPException(status_code=422, detail="Message content or action is required")
                incoming_message = conversation_service.add_message(accept, conversation_id, prepared)
                new_source = True
                if prepared.battle_control:
                    try:
                        battle_ledger_service.apply_battle_control(accept, conversation, prepared.battle_control, message_id=incoming_message.id)
                    except ValueError as exc:
                        raise HTTPException(status_code=422, detail=str(exc)) from exc
                if prepared.speaker_type == "system":
                    conversation_service.record_system_scene_direction(accept, conversation_id, prepared.content)
                job = conversation_service.create_message_generation_job(accept, conversation_id, incoming_message.id)
            if new_source and get_settings().llm_mock and getattr(request.app.state, "mock_execution", True):
                job.execution_options = {**(job.execution_options or {}), "execution_mode": "mock"}
                accept.add(job)
            accept.add(GenerationAcceptanceReceipt(id="accept_" + uuid4().hex,
                conversation_id=conversation_id, client_request_id=("send:" + request_id) if request_id else ("anonymous:" + uuid4().hex),
                payload_sha256=digest, incoming_message_id=incoming_message.id, job_id=job.id))
        if job.status == "queued" and get_settings().llm_mock and getattr(request.app.state, "mock_execution", True) and (job.execution_options or {}).get("execution_mode") != "mock":
            raise HTTPException(status_code=409, detail="Existing live-intended work is not executable by the isolated mock runner")
        execution_mode = (job.execution_options or {}).get("execution_mode")
        result = MessageGenerationJobCreateRead(job=_serialize_generation_job(job),
            incoming_message=MessageRead.model_validate(asset_service.serialize_message(accept, incoming_message)))
    # Wake only AFTER the acceptance commit. A crash here is recovered by polling.
    if result.job.status == "queued":
        _wake_accepted_job(result.job.id, session, background_tasks, execution_mode)
    return result


@router.get("/{conversation_id}/generation-jobs/by-request/{request_id}", response_model=MessageGenerationJobCreateRead)
def generation_receipt_by_request(conversation_id: str, request_id: str, session: Session = Depends(get_session)):
    if not request_id.strip() or len(request_id) > 200 or any(ord(char) < 32 or ord(char) == 127 for char in request_id):
        raise HTTPException(status_code=422, detail="request_id must be a nonblank string of at most 200 characters without controls")
    receipt = session.exec(select(GenerationAcceptanceReceipt).where(
        GenerationAcceptanceReceipt.conversation_id == conversation_id,
        GenerationAcceptanceReceipt.client_request_id == "send:" + request_id,
    )).first()
    if receipt is None:
        raise HTTPException(status_code=404, detail="Acceptance receipt not found")
    incoming = session.get(Message, receipt.incoming_message_id)
    job = session.get(MessageGenerationJob, receipt.job_id)
    if incoming is None:
        raise HTTPException(status_code=410, detail="Accepted source was deleted; it will not be recreated")
    if job is None or incoming.conversation_id != conversation_id or job.conversation_id != conversation_id or job.incoming_message_id != incoming.id:
        raise HTTPException(status_code=409, detail="Acceptance receipt is inconsistent")
    return MessageGenerationJobCreateRead(job=_serialize_generation_job(job),
        incoming_message=MessageRead.model_validate(asset_service.serialize_message(session, incoming)))


@router.get("/{conversation_id}/generation-jobs/active", response_model=ActiveGenerationJobsRead)
def active_message_generation_jobs(conversation_id: str, session: Session = Depends(get_session)):
    if not conversation_service.get_conversation(session, conversation_id):
        raise HTTPException(status_code=404, detail="Conversation not found")
    jobs = session.exec(select(MessageGenerationJob).where(
        MessageGenerationJob.conversation_id == conversation_id,
        MessageGenerationJob.status.in_(["queued", "running"]),
    ).order_by(MessageGenerationJob.created_at, MessageGenerationJob.id)).all()
    return ActiveGenerationJobsRead(jobs=[_serialize_generation_job(job) for job in jobs])


@router.post("/{conversation_id}/generation-jobs/{job_id}/retry", response_model=MessageGenerationJobRead)
def retry_message_generation_job(conversation_id: str, job_id: str, payload: GenerationJobRetryRequest,
    request: Request, background_tasks: BackgroundTasks, session: Session = Depends(get_session)):
    requeued = False
    with acceptance_transaction(session) as accept:
        job = accept.get(MessageGenerationJob, job_id)
        if not job or job.conversation_id != conversation_id:
            raise HTTPException(status_code=404, detail="Generation job not found")
        if (job.status in {"failed", "cancelled"} and job.state_version == payload.expected_state_version
            and not job.generated_message_ids and not accept.exec(select(Message.id).where(Message.generation_job_id == job_id)).first()):
            source = accept.get(Message, job.incoming_message_id)
            if source is None or source.conversation_id != conversation_id:
                raise HTTPException(status_code=410, detail="source was deleted; retry never recreates it")
            mock_job = (job.execution_options or {}).get("execution_mode") == "mock"
            if get_settings().llm_mock and getattr(request.app.state, "mock_execution", True) and not mock_job:
                raise HTTPException(status_code=409, detail="Existing live-intended work is not executable by the isolated mock runner")
            if mock_job and not get_settings().llm_mock:
                raise HTTPException(status_code=409, detail={"job_id": job_id, "code": "mock_generation_held"})
            _admit_generation_backlog(accept, conversation_id)
            updated = accept.exec(sa_update(MessageGenerationJob).where(
                MessageGenerationJob.id == job_id, MessageGenerationJob.status.in_(["failed", "cancelled"]),
                MessageGenerationJob.state_version == payload.expected_state_version,
            ).values(status="queued", cancel_requested_at=None, error_message=None,
                completed_at=None, lease_owner=None, lease_expires_at=None, heartbeat_at=None,
                updated_at=datetime.now().astimezone(), state_version=MessageGenerationJob.state_version + 1)
              .execution_options(synchronize_session=False))
            if updated.rowcount == 1:
                requeued = True
                accept.refresh(job)
                # Retry preserves the durable execution intent; it never
                # converts live work to mock output or mock work to paid work.
                source.metadata_ = {k:v for k,v in (source.metadata_ or {}).items() if not k.startswith("generation_error") and k != "generation_status"}
                accept.add(source)
                conversation_service.append_generation_job_event(accept, job, "queued", payload={"reason":"explicit_retry"})
        execution_mode = (job.execution_options or {}).get("execution_mode")
        result = _serialize_generation_job(job)
    if requeued:
        _wake_accepted_job(job_id, session, background_tasks, execution_mode)
    return result


@router.get("/{conversation_id}/generation-jobs/{job_id}", response_model=MessageGenerationJobRead)
def get_message_generation_job(conversation_id: str, job_id: str, session: Session = Depends(get_session)):
    job = conversation_service.get_message_generation_job(session, job_id)
    if not job or job.conversation_id != conversation_id:
        raise HTTPException(status_code=404, detail="Generation job not found")
    return _serialize_generation_job(job)


_PUBLIC_GENERATION_EVENT_STATUS = {
    "queued": "queued",
    "running": "running",
    "requeued": "retrying",
    "cancel_requested": "running",
    "completed": "completed",
    "failed": "failed",
    "cancelled": "cancelled",
}
_TERMINAL_GENERATION_STATUSES = {"completed", "failed", "cancelled"}


def _serialize_generation_sse_event(event: GenerationJobEvent, job: MessageGenerationJob) -> tuple[str, dict]:
    public_status = _PUBLIC_GENERATION_EVENT_STATUS.get(event.status, "running")
    data = {
        "event_id": event.id,
        "job_id": event.job_id,
        "conversation_id": event.conversation_id,
        "state_version": event.state_version,
        "status": public_status,
        "created_at": event.created_at.isoformat(),
    }
    if public_status == "completed":
        data["generated_message_ids"] = list(job.generated_message_ids or [])
    elif public_status == "failed":
        payload = dict(event.payload_ or {})
        error_code = str(payload.get("error_code") or "")
        if error_code in _PUBLIC_GENERATION_ERROR_CODES:
            data["error_code"] = error_code
    return public_status, data


@router.get("/{conversation_id}/generation-jobs/{job_id}/events")
def stream_message_generation_job_events(
    conversation_id: str,
    job_id: str,
    request: Request,
    after_event_id: int | None = Query(default=None, ge=0),
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    heartbeat_seconds: int = Query(default=15, ge=1, le=30),
    session: Session = Depends(get_session),
):
    job = conversation_service.get_message_generation_job(session, job_id)
    if not job or job.conversation_id != conversation_id:
        raise HTTPException(status_code=404, detail="Generation job not found")
    cursor = int(after_event_id or 0)
    if last_event_id:
        try:
            cursor = max(cursor, int(last_event_id))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Invalid Last-Event-ID") from exc
    bind = session.get_bind()

    async def event_stream():
        nonlocal cursor
        loop = asyncio.get_running_loop()
        last_emit_at = loop.time()
        yield "retry: 2000\n\n"
        while True:
            if await request.is_disconnected():
                return
            with Session(bind) as stream_session:
                current_job = stream_session.get(MessageGenerationJob, job_id)
                if current_job is None or current_job.conversation_id != conversation_id:
                    return
                events = stream_session.exec(
                    select(GenerationJobEvent)
                    .where(
                        GenerationJobEvent.job_id == job_id,
                        GenerationJobEvent.conversation_id == conversation_id,
                        col(GenerationJobEvent.id) > cursor,
                    )
                    .order_by(col(GenerationJobEvent.id))
                ).all()
                serialized = [
                    (event.id, *_serialize_generation_sse_event(event, current_job))
                    for event in events
                    if event.id is not None and not (
                        event.status in _TERMINAL_GENERATION_STATUSES
                        and event.state_version < current_job.state_version
                    )
                ]
            for event_id, event_name, data in serialized:
                cursor = int(event_id)
                yield (
                    f"id: {cursor}\n"
                    f"event: {event_name}\n"
                    f"data: {json.dumps(data, ensure_ascii=False, separators=(',', ':'))}\n\n"
                )
                last_emit_at = loop.time()
                if event_name in _TERMINAL_GENERATION_STATUSES:
                    return
            if current_job.status in _TERMINAL_GENERATION_STATUSES:
                # Exhausted/equal/ahead cursors still get authoritative terminal
                # reconciliation, without inventing a new durable event ID.
                data = {"job_id": current_job.id, "conversation_id": conversation_id,
                        "state_version": current_job.state_version, "status": current_job.status}
                if current_job.status == "completed":
                    data["generated_message_ids"] = list(current_job.generated_message_ids or [])
                yield f"event: {current_job.status}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n"
                return
            now = loop.time()
            if now - last_emit_at >= heartbeat_seconds:
                yield ": heartbeat\n\n"
                last_emit_at = now
            await asyncio.sleep(0.25)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@router.post("/{conversation_id}/generation-jobs/{job_id}/cancel", response_model=MessageGenerationJobRead)
def cancel_message_generation_job(conversation_id: str, job_id: str, session: Session = Depends(get_session)):
    job = conversation_service.get_message_generation_job(session, job_id)
    if not job or job.conversation_id != conversation_id:
        raise HTTPException(status_code=404, detail="Generation job not found")
    cancelled = conversation_service.request_message_generation_job_cancel(session, job)
    generation_dispatcher.wake()
    return _serialize_generation_job(cancelled)


@router.post("/{conversation_id}/messages", response_model=list[MessageRead])
async def post_message(conversation_id: str, payload: MessageCreate, request: Request, session: Session = Depends(get_session)):
    accepted = await create_message_generation_job(conversation_id, payload, request, BackgroundTasks(), session)
    job = session.get(MessageGenerationJob, accepted.job.id)
    incoming_message = session.get(Message, accepted.incoming_message.id)
    if job.status == "queued" and (job.execution_options or {}).get("execution_mode") == "mock" and get_settings().llm_mock:
        # Legacy response still waits for the isolated runner, with its same
        # no-paid-auxiliary policy rather than bypassing the execution scope.
        await generation_execution.run_mock_generation_queue(session.get_bind())
        session.expire_all()
        job = session.get(MessageGenerationJob, accepted.job.id)
    if job.status == "completed":
        if not job.generated_message_ids:
            return [asset_service.serialize_message(session, incoming_message)]
        try:
            existing = conversation_service.finalize_message_generation_turn(session, job, [])
        except ValueError as exc:
            raise HTTPException(status_code=410, detail="completed receipt was deleted or is inconsistent; no content is recreated") from exc
        return [asset_service.serialize_message(session, message) for message in [incoming_message, *existing]]
    if job.status in {"failed", "cancelled"}:
        raise HTTPException(status_code=409, detail={"job_id":job.id,"status":job.status,"code":"explicit_retry_required"})
    owner = "compat:" + uuid4().hex
    claimed = conversation_service.claim_next_message_generation_job(session, lease_owner=owner, job_id=job.id)
    if claimed is None:
        raise HTTPException(status_code=409, detail={"job_id":job.id,"code":"durable_generation_pending"})
    job = claimed
    conversation = conversation_service.get_conversation(session, conversation_id)
    payload = generation_execution._message_payload_from_stored_message(incoming_message)
    applied_battle_record = None  # Acceptance already applied the declared effect.
    with generation_dispatcher.track_inline_job(session.get_bind(), job.id, owner):
        try:
            replies = await generation_execution.generate_replies_from_message(
                session=session,
                conversation_id=conversation_id,
                conversation=conversation,
                payload=payload,
                incoming_message=incoming_message,
                include_incoming_in_response=True,
                applied_battle_record=applied_battle_record,
                generation_job=job,
            )
        except ContextMaintenanceRequired as exc:
            session.rollback()
            conversation_service.mark_message_generation_job_failed(session, session.get(MessageGenerationJob, job.id), exc, expected_owner=owner, expected_attempt=claimed.attempt_count)
            raise HTTPException(
                status_code=503,
                detail={
                    "code": exc.code,
                    "projected_tokens": exc.projected_tokens,
                    "working_tokens": exc.working_tokens,
                    "backlog_groups": exc.backlog_groups,
                    "catchup_batches": exc.batches,
                },
            ) from exc
        except LLMUnavailableError as exc:
            session.rollback()
            conversation_service.mark_message_generation_job_failed(session, session.get(MessageGenerationJob, job.id), exc, expected_owner=owner, expected_attempt=claimed.attempt_count)
            logger.exception(
                "LLM generation failed for conversation=%s source_message=%s speaker_type=%s speaker_id=%s",
                conversation_id,
                incoming_message.id,
                payload.speaker_type,
                payload.speaker_id,
            )
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except Exception as exc:
            session.rollback()
            conversation_service.mark_message_generation_job_failed(session, session.get(MessageGenerationJob, job.id), exc, expected_owner=owner, expected_attempt=claimed.attempt_count)
            logger.exception(
                "Message generation failed for conversation=%s source_message=%s speaker_type=%s speaker_id=%s",
                conversation_id,
                incoming_message.id,
                payload.speaker_type,
                payload.speaker_id,
            )
            raise
        session.expire_all()
        current_job = session.get(MessageGenerationJob, job.id)
        if current_job and current_job.status == "running" and current_job.lease_owner == owner:
            conversation_service.mark_message_generation_job_completed(session, current_job, [message.id for message in replies if message.id != incoming_message.id])
        return [asset_service.serialize_message(session, message) for message in replies]

@router.delete("/{conversation_id}/messages/{message_id}", status_code=204)
def delete_message(conversation_id: str, message_id: str, session: Session = Depends(get_session)):
    if not conversation_service.get_conversation(session, conversation_id):
        raise HTTPException(status_code=404, detail="Conversation not found")
    message = conversation_service.get_message(session, message_id)
    if not message or message.conversation_id != conversation_id:
        raise HTTPException(status_code=404, detail="Message not found")
    conversation_service.delete_message(session, message)
    return None


@router.post("/{conversation_id}/messages/bulk-delete", response_model=MessageBulkDeleteRead)
def bulk_delete_messages(conversation_id: str, payload: MessageBulkDeleteRequest, session: Session = Depends(get_session)):
    if not conversation_service.get_conversation(session, conversation_id):
        raise HTTPException(status_code=404, detail="Conversation not found")
    deleted_ids, missing_ids = conversation_service.delete_messages(session, conversation_id, payload.message_ids)
    return MessageBulkDeleteRead(deleted_ids=deleted_ids, missing_ids=missing_ids)


@router.post("/{conversation_id}/messages/{message_id}/regenerate", response_model=list[MessageRead])
async def regenerate_after_message(
    conversation_id: str,
    message_id: str,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    payload: MessageRegenerateRequest | None = None,
    session: Session = Depends(get_session),
):
    conversation = conversation_service.get_conversation(session, conversation_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")
    payload = payload or MessageRegenerateRequest()
    if payload.mode != "after_message":
        raise HTTPException(status_code=422, detail="Only mode='after_message' is supported")
    if payload.replace_existing:
        raise HTTPException(status_code=422, detail="replace_existing is not supported for safe append-only regeneration")
    selected_message = session.get(Message, message_id)
    if not selected_message or selected_message.conversation_id != conversation_id:
        raise HTTPException(status_code=404, detail="Message not found")
    messages = conversation_service.list_messages(session, conversation_id)
    selected_index = next((idx for idx, message in enumerate(messages) if message.id == message_id), None)
    if selected_index is None:
        raise HTTPException(status_code=404, detail="Message not found")
    message_payload = MessageCreate(
        speaker_type=selected_message.speaker_type,
        speaker_id=selected_message.speaker_id,
        content=selected_message.content,
        action=selected_message.action,
        thought=selected_message.thought,
    )
    recent_messages = messages[: selected_index + 1]
    character_participant_count = sum(1 for participant in conversation_service.get_participants(session, conversation_id) if participant.participant_type == "character")
    if selected_message.speaker_type == "character" and character_participant_count < 2:
        prior_input_index = next(
            (idx for idx in range(selected_index - 1, -1, -1) if messages[idx].speaker_type in {"user", "system"}),
            None,
        )
        if prior_input_index is None:
            raise HTTPException(status_code=422, detail="No prior user or system input found for regeneration")
        prior_input = messages[prior_input_index]
        message_payload = MessageCreate(
            speaker_type=prior_input.speaker_type,
            speaker_id=prior_input.speaker_id,
            content=prior_input.content,
            action=prior_input.action,
            thought=prior_input.thought,
        )
        # For 1:1 character-bubble regeneration, exclude the selected answer so
        # the appended reply is an alternative to it, not a continuation of it.
        recent_messages = messages[:selected_index]
    if idempotency_key is not None and (not idempotency_key.strip() or len(idempotency_key) > 200):
        raise HTTPException(status_code=422, detail="Idempotency-Key must be nonblank and at most 200 characters")
    source_id = selected_message.id
    isolated_mock_request = get_settings().llm_mock and getattr(request.app.state, "mock_execution", True)
    captured_options = {"kind":"regeneration", "payload":message_payload.model_dump(mode="json"),
                        "recent_message_ids":[message.id for message in recent_messages]}
    if isolated_mock_request:
        captured_options["execution_mode"] = "mock"
    receipt_key = "regeneration:" + source_id + ":" + (idempotency_key or uuid4().hex)
    digest = hashlib.sha256(json.dumps({"source":source_id,"request":payload.model_dump(mode="json")}, sort_keys=True).encode()).hexdigest()
    with acceptance_transaction(session) as accept:
        receipt = accept.exec(select(GenerationAcceptanceReceipt).where(
            GenerationAcceptanceReceipt.conversation_id == conversation_id,
            GenerationAcceptanceReceipt.client_request_id == receipt_key,
        )).first()
        if receipt:
            if receipt.payload_sha256 != digest:
                raise HTTPException(status_code=409, detail="Idempotency-Key payload conflict")
            job = accept.get(MessageGenerationJob, receipt.job_id)
            if job is None:
                raise HTTPException(status_code=410, detail="Regeneration was deleted; it will not be recreated")
        else:
            if accept.get(Message, source_id) is None:
                raise HTTPException(status_code=410, detail="Source was deleted; it will not be recreated")
            _admit_generation_backlog(accept, conversation_id)
            job = conversation_service.create_message_generation_job(accept, conversation_id, source_id)
            job.execution_options = captured_options
            accept.add(job)
            accept.add(GenerationAcceptanceReceipt(id="accept_"+uuid4().hex, conversation_id=conversation_id,
                client_request_id=receipt_key, payload_sha256=digest, incoming_message_id=source_id, job_id=job.id))
        job_id = job.id
        mock_job = (job.execution_options or {}).get("execution_mode") == "mock"
        pending = job.status in {"queued", "running"}
        if pending and isolated_mock_request and not mock_job:
            raise HTTPException(status_code=409, detail="Existing live-intended work is not executable by the isolated mock runner")
        if pending and mock_job and not get_settings().llm_mock:
            raise HTTPException(status_code=409, detail={"job_id":job_id,"code":"mock_generation_held"})
        queued = job.status == "queued"
    # Dispatch only after acceptance commits, using the persisted mode rather
    # than relabelling a receipt on replay. The mock queue binds this request's
    # engine and scopes all auxiliary enqueue through isolated_mock_execution.
    if queued:
        if mock_job:
            await generation_execution.run_mock_generation_queue(session.get_bind())
        else:
            _start_message_generation_worker(job_id)
            await generation_execution.run_message_generation_job_in_session(session, job_id)
    session.expire_all()
    current = session.get(MessageGenerationJob, job_id)
    if current.status in {"queued", "running"}:
        raise HTTPException(status_code=409, detail={"job_id":job_id,"code":"durable_generation_pending"})
    if current.status != "completed":
        raise HTTPException(status_code=503, detail={"job_id":job_id,"status":current.status,"code":_public_generation_error_code(current)})
    if not current.generated_message_ids:
        return []
    try:
        replies = conversation_service.finalize_message_generation_turn(session, current, [])
    except ValueError as exc:
        raise HTTPException(status_code=410, detail="completed receipt was deleted or is inconsistent; no content is recreated") from exc
    return [asset_service.serialize_message(session, message) for message in replies]
