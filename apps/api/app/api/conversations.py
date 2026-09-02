from datetime import datetime
import asyncio
import json
import logging
import threading
from dataclasses import dataclass

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response
from fastapi.responses import StreamingResponse
from sqlmodel import Session, col, select
from app.db.models import BattleMatchRecord, CharacterMemory, Conversation, ConversationParticipant, ConversationRelationshipState, GenerationJobEvent, Message, MessageGenerationJob, PostCommitTask, SceneState
from app.db.session import engine, get_session
from app.engine.character_runtime import CharacterRuntime, CharacterRuntimeError, format_runtime_source_message, generation_control_reserve_tokens
from app.engine import prompts
from app.engine.llm_client import LLMClient, LLMUnavailableError
from app.engine.prompt_harness import PromptHarness, approx_tokens
from app.engine.input_markup import parse_input_markup
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
from app.services import asset_service, battle_ledger_service, character_service, chat_command_service, context_preview_service, conversation_service, domain_actor_service, genre_domain_service, model_provider_service, prompt_snapshot_service, runtime_settings_service, system_prompt_service, tts_service, usage_service
from app.services.context_management_service import AutomaticContextPlan, ContextCapacity, resolve_safe_model_context_capacity, sync_turn_group_id
from app.services.generation_dispatcher import generation_dispatcher
from app.services.post_commit_dispatcher import post_commit_dispatcher

router = APIRouter(prefix="/conversations", tags=["conversations"])
logger = logging.getLogger(__name__)
_scene_compression_locks_guard = threading.Lock()
_scene_compression_locks: dict[str, threading.Lock] = {}
AUTOMATIC_PREGENERATION_MAX_COMPRESSION_BATCHES = 3


class ContextMaintenanceRequired(RuntimeError):
    """Recoverable fail-closed generation error with content-free diagnostics."""

    code = "context_maintenance_required"

    def __init__(self, *, projected_tokens: int, working_tokens: int, backlog_groups: int, batches: int):
        self.projected_tokens = max(0, int(projected_tokens))
        self.working_tokens = max(0, int(working_tokens))
        self.backlog_groups = max(0, int(backlog_groups))
        self.batches = max(0, int(batches))
        super().__init__(
            f"{self.code}: projected_tokens={self.projected_tokens}; "
            f"working_tokens={self.working_tokens}; backlog_groups={self.backlog_groups}; "
            f"catchup_batches={self.batches}"
        )


@dataclass(frozen=True)
class AutomaticGenerationContextEvaluation:
    scene_state: SceneState
    messages: tuple[Message, ...]
    harness: PromptHarness
    plan: AutomaticContextPlan
    mandatory_without_current: int
    job_source_message_ids: dict[str, str]


async def _ensure_automatic_generation_context(
    *,
    evaluate,
    compress_once,
    capacity: ContextCapacity,
    max_batches: int = AUTOMATIC_PREGENERATION_MAX_COMPRESSION_BATCHES,
) -> tuple[AutomaticGenerationContextEvaluation, int]:
    """Refresh exact coverage; once hard catch-up starts, continue below high."""

    batches = 0
    max_batches = max(0, int(max_batches))
    while True:
        try:
            evaluation = evaluate()
        except prompts.ContextCoverageError as exc:
            raise ContextMaintenanceRequired(
                projected_tokens=0,
                working_tokens=capacity.working_input_tokens,
                backlog_groups=0,
                batches=batches,
            ) from exc

        plan = evaluation.plan
        status = plan.pressure.status
        catchup_in_progress = batches > 0
        needs_compression = status == "hard" or (
            catchup_in_progress and status == "high"
        )
        if not needs_compression:
            return evaluation, batches
        if not plan.has_foldable_backlog:
            if status != "hard":
                return evaluation, batches
            raise ContextMaintenanceRequired(
                projected_tokens=plan.pressure.projected_input_tokens,
                working_tokens=plan.pressure.working_input_tokens,
                backlog_groups=plan.pressure.backlog_group_count,
                batches=batches,
            )
        if batches >= max_batches:
            if status != "hard":
                return evaluation, batches
            raise ContextMaintenanceRequired(
                projected_tokens=plan.pressure.projected_input_tokens,
                working_tokens=plan.pressure.working_input_tokens,
                backlog_groups=plan.pressure.backlog_group_count,
                batches=batches,
            )
        progressed = await compress_once(evaluation)
        batches += 1
        if not progressed:
            raise ContextMaintenanceRequired(
                projected_tokens=plan.pressure.projected_input_tokens,
                working_tokens=plan.pressure.working_input_tokens,
                backlog_groups=plan.pressure.backlog_group_count,
                batches=batches,
            )


def _scene_compression_lock(conversation_id: str) -> threading.Lock:
    with _scene_compression_locks_guard:
        return _scene_compression_locks.setdefault(conversation_id, threading.Lock())


@router.post("", response_model=ConversationRead)
def create_conversation(payload: ConversationCreate, session: Session = Depends(get_session)):
    conversation = conversation_service.create_conversation(session, payload)
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
    conversation = conversation_service.update_conversation(session, conversation_id, payload)
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
    session: Session = Depends(get_session),
):
    if not conversation_service.get_conversation(session, conversation_id):
        raise HTTPException(status_code=404, detail="Conversation not found")
    messages = conversation_service.list_messages(
        session,
        conversation_id,
        limit=limit,
        before_id=before_id,
        recent_turns=recent_turns,
    )
    return [asset_service.serialize_message(session, message) for message in messages]


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


def runtime_user_message(payload: MessageCreate, *, speaker_name: str | None = None) -> str:
    return format_runtime_source_message(
        speaker_type=payload.speaker_type,
        speaker_id=payload.speaker_id,
        content=payload.content,
        action=payload.action,
        speaker_name=speaker_name,
    )


def _battle_control_value(control: object, key: str):
    if isinstance(control, dict):
        return control.get(key)
    return getattr(control, key, None)


def _battle_character_name(session: Session, character_id: str | None) -> str:
    if not character_id:
        return ""
    character = character_service.get_character(session, character_id)
    return character.name if character else character_id


def build_battle_control_runtime_context(
    session: Session,
    payload: MessageCreate,
    applied_record=None,
    conversation_id: str | None = None,
) -> dict[str, object] | None:
    control = payload.battle_control
    if not control:
        return None
    action = str(_battle_control_value(control, "action") or "").strip().lower()
    if not action:
        return None
    record = applied_record
    if record is None:
        match_id = str(_battle_control_value(control, "match_id") or "").strip()
        if match_id:
            candidate = session.get(BattleMatchRecord, match_id)
            if candidate is not None and (conversation_id is None or candidate.conversation_id == conversation_id):
                record = candidate
    active_pair = None
    if record:
        active_pair = {
            "participant_a_id": record.participant_a_id,
            "participant_a_name": _battle_character_name(session, record.participant_a_id),
            "participant_b_id": record.participant_b_id,
            "participant_b_name": _battle_character_name(session, record.participant_b_id),
        }
    elif action == "start":
        participant_a_id = str(_battle_control_value(control, "participant_a_id") or "").strip()
        participant_b_id = str(_battle_control_value(control, "participant_b_id") or "").strip()
        if participant_a_id and participant_b_id:
            active_pair = {
                "participant_a_id": participant_a_id,
                "participant_a_name": _battle_character_name(session, participant_a_id),
                "participant_b_id": participant_b_id,
                "participant_b_name": _battle_character_name(session, participant_b_id),
            }
    context: dict[str, object] = {"action": action}
    if active_pair:
        context["active_pair"] = active_pair
    if action == "progress":
        favored_id = str(_battle_control_value(control, "favored_character_id") or "").strip()
        advantage = _battle_control_value(control, "advantage")
        if favored_id:
            context["favored_character_id"] = favored_id
            context["favored_character_name"] = _battle_character_name(session, favored_id)
        if advantage is not None:
            context["advantage_percent"] = round(float(advantage) * 100)
    if action == "end":
        winner_id = str(_battle_control_value(control, "winner_id") or "").strip()
        if winner_id:
            context["winner_id"] = winner_id
            context["winner_name"] = _battle_character_name(session, winner_id)
    return context


def select_runtime_room_characters(
    *,
    room_characters,
    recent_messages: list[Message],
    payload: MessageCreate,
    battle_control_context: dict[str, object] | None = None,
):
    """Choose the active speakers for generation in oversized league rooms.

    League conversations may keep every roster member as a participant while the
    visible chat beat only involves a smaller active cast.  Passing the full roster
    into the one-call multi-character prompt lets early participants consume the
    character-card budget and makes later active characters lose their identity.
    """
    if len(room_characters) <= 4:
        return room_characters

    participant_ids = {character.id for character in room_characters}
    active_ids: set[str] = set()

    active_pair = (battle_control_context or {}).get("active_pair") if battle_control_context else None
    if isinstance(active_pair, dict):
        for key in ("participant_a_id", "participant_b_id"):
            value = str(active_pair.get(key) or "").strip()
            if value in participant_ids:
                active_ids.add(value)

    if payload.speaker_type == "character" and payload.speaker_id in participant_ids:
        active_ids.add(payload.speaker_id)

    for message in recent_messages[-24:]:
        if message.speaker_type == "character" and message.speaker_id in participant_ids:
            active_ids.add(message.speaker_id)

    if len(active_ids) >= 2:
        selected = [character for character in room_characters if character.id in active_ids]
        return selected[:6]
    return room_characters


async def persist_generated_reply_messages(
    *,
    session: Session,
    conversation_id: str,
    conversation,
    payload: MessageCreate,
    incoming_message: Message,
    generated_replies,
    room_characters,
    scene_state: SceneState | None,
    is_multi_room: bool,
    min_bubbles: int = 1,
    silent_character_ids: set[str] | None = None,
    suppress_storytelling: bool = False,
) -> list[Message]:
    character_ids = {character.id for character in room_characters}
    silent_character_ids = silent_character_ids or set()
    generated_replies = list(generated_replies or [])
    required_bubbles = max(1, int(min_bubbles))
    if len(generated_replies) < required_bubbles:
        raise CharacterRuntimeError(
            f"Validated response had {len(generated_replies)} replies before persistence; at least {required_bubbles} are required"
        )
    for index, generated in enumerate(generated_replies, start=1):
        if generated.reply_type == "storytelling":
            if suppress_storytelling or "command_overlay" in (generated.text or "").lower():
                raise CharacterRuntimeError(
                    f"Validated response reply {index} would be filtered before persistence"
                )
            continue
        if generated.character_id not in character_ids or generated.character_id in silent_character_ids:
            raise CharacterRuntimeError(
                f"Validated response reply {index} used a non-persistable character"
            )

    persisted_messages: list[Message] = []
    for generated in generated_replies:
        if generated.reply_type == "storytelling":
            persisted_messages.append(conversation_service.build_message(
                conversation_id,
                MessageCreate(speaker_type="storytelling", speaker_id="storyteller", content=generated.text),
                emotion=generated.emotion,
                action=generated.action,
                thought=generated.thought,
            ))
            continue
        persisted_messages.append(conversation_service.build_message(
            conversation_id,
            MessageCreate(speaker_type="character", speaker_id=generated.character_id, content=generated.text),
            emotion=generated.emotion,
            action=generated.action,
            thought=generated.thought,
        ))
    return persisted_messages


def build_generated_turn_post_commit_tasks(
    *,
    session: Session,
    conversation,
    scene_state: SceneState | None,
    incoming_message: Message,
    generated_messages: list[Message],
    enqueue_compression: bool,
    character_ids: list[str],
    generation_job_id: str | None = None,
    generation_attempt_id: str | None = None,
    compression_raw_tail_token_budget: int | None = None,
    compression_mandatory_prompt_tokens: int | None = None,
) -> list[dict]:
    if generation_job_id:
        identity = f"job:{generation_job_id}"
    elif generation_attempt_id:
        identity = f"sync:{generation_attempt_id}"
    else:
        raise ValueError("generation attempt identity is required")
    tasks: list[dict] = []
    reserved_asset_ids: dict[str, set[str]] = {}
    for reply_index, message in enumerate(generated_messages):
        if message.speaker_type != "character" or not message.speaker_id:
            continue
        task_prefix = f"{identity}:reply:{reply_index}"
        selected_asset = asset_service.select_asset_for_message(
            session,
            conversation_id=conversation.id,
            character_id=message.speaker_id,
            emotion=message.emotion,
            action=message.action,
            content=message.content,
            scene_state=scene_state,
            excluded_asset_ids=reserved_asset_ids.setdefault(message.speaker_id, set()),
            selection_seed=message.id,
        )
        if selected_asset is not None:
            reserved_asset_ids[message.speaker_id].add(selected_asset.id)
            tasks.append({
                "unique_key": f"{task_prefix}:asset",
                "task_type": "asset",
                "payload": {
                    "conversation_id": conversation.id,
                    "message_id": message.id,
                    "asset_id": selected_asset.id,
                },
            })
        if conversation.tts_enabled:
            tasks.append({
                "unique_key": f"{task_prefix}:tts",
                "task_type": "tts",
                "payload": {"conversation_id": conversation.id, "message_id": message.id},
            })

    if enqueue_compression:
        tasks.append({
            "unique_key": f"{identity}:compression",
            "task_type": "compression",
            "payload": {
                "conversation_id": conversation.id,
                "generated_character_count": sum(1 for message in generated_messages if message.speaker_type == "character"),
                "character_ids": character_ids,
                "raw_tail_token_budget": compression_raw_tail_token_budget,
                "mandatory_prompt_tokens": compression_mandatory_prompt_tokens,
            },
        })
    return tasks


async def _run_scene_compression_in_session(
    session: Session,
    conversation_id: str,
    *,
    generated_character_count: int,
    character_ids: list[str],
    raw_tail_token_budget: int | None = None,
    mandatory_prompt_tokens: int | None = None,
) -> bool:
    if generated_character_count <= 0:
        return True
    conversation = conversation_service.get_conversation(session, conversation_id)
    if not conversation:
        return True
    runtime_setting = runtime_settings_service.get_effective_setting(session, conversation_id)
    current_messages = conversation_service.list_messages(session, conversation_id)
    current_scene = session.get(SceneState, conversation_id) or SceneState(conversation_id=conversation_id)
    job_mapping = conversation_service.generation_job_source_message_ids(session, conversation_id)
    character_names = {
        character_id: character.name
        for character_id in character_ids
        if (character := character_service.get_character(session, character_id)) is not None
    }
    history_token_estimator = conversation_service.automatic_context_message_token_estimator(
        character_names
    )
    if get_settings().context_management_mode == "automatic":
        selected_model_option = model_provider_service.option_by_key(session, runtime_setting.model_key)
        fallback_model_option = model_provider_service.option_by_key(
            session,
            getattr(runtime_setting, "fallback_model_key", None),
        )
        automatic_capacity = resolve_safe_model_context_capacity(
            selected_model_option,
            fallback_model_option,
            room_prompt_budget_tokens=prompts.prompt_budget_for_context(
                genre_mode=getattr(conversation, "genre_mode", None),
                is_multi_room=len(character_ids) >= 2,
            ),
        )
        execution_plan = conversation_service.automatic_context_plan(
            current_scene,
            current_messages,
            capacity=automatic_capacity,
            mandatory_prompt_tokens=max(0, int(mandatory_prompt_tokens or 0)),
            job_source_message_ids=job_mapping,
            token_estimator=history_token_estimator,
        )
        if execution_plan.pressure.status == "low":
            return True
        raw_tail_token_budget = execution_plan.raw_tail_token_budget
    selector_kwargs = {
        "raw_tail_token_budget": raw_tail_token_budget,
        "job_source_message_ids": job_mapping,
        "token_estimator": history_token_estimator,
    }
    try:
        pending_batch, _ = conversation_service.select_incremental_compression_batch(
            current_scene,
            current_messages,
            **selector_kwargs,
        )
    except ValueError:
        pending_batch = current_messages
    if not pending_batch:
        return True

    before = session.get(SceneState, conversation_id)
    before_revision = int(getattr(before, "compression_revision", 0) or 0)
    before_boundary = getattr(before, "last_compression_source_message_id", None)
    compression_overrides = runtime_settings_service.compression_llm_overrides_for_setting(runtime_setting, session=session)
    compression_fallback_overrides = runtime_settings_service.compression_fallback_llm_overrides_for_setting(runtime_setting, session=session)
    await conversation_service.update_scene_orchestration_summary(
        session,
        conversation_id,
        conversation_service.list_messages(session, conversation_id),
        llm_client=LLMClient(profile="compression", purpose="conversation_compression", overrides=compression_overrides),
        fallback_llm_client=(
            LLMClient(profile="compression", purpose="conversation_compression_fallback", overrides=compression_fallback_overrides)
            if compression_fallback_overrides else None
        ),
        character_ids=character_ids,
        **selector_kwargs,
    )

    session.expire_all()
    after = session.get(SceneState, conversation_id)
    after_revision = int(getattr(after, "compression_revision", 0) or 0)
    after_boundary = getattr(after, "last_compression_source_message_id", None)
    if after_revision > before_revision and after_boundary != before_boundary:
        return True

    selector_scene = after or SceneState(conversation_id=conversation_id)
    try:
        remaining_batch, _ = conversation_service.select_incremental_compression_batch(
            selector_scene,
            conversation_service.list_messages(session, conversation_id),
            **selector_kwargs,
        )
    except ValueError as exc:
        raise RuntimeError(
            f"compression boundary validation failed for conversation {conversation_id}"
        ) from exc
    if not remaining_batch:
        return True
    raise RuntimeError(
        "compression completed without boundary or revision progress "
        f"for conversation {conversation_id}; foldable_messages={len(remaining_batch)}"
    )


async def _run_scene_compression(conversation_id: str, *, generated_character_count: int, character_ids: list[str]) -> None:
    with Session(engine) as session:
        await _run_scene_compression_in_session(
            session,
            conversation_id,
            generated_character_count=generated_character_count,
            character_ids=character_ids,
        )


def _run_scene_compression_sync(conversation_id: str, generated_character_count: int, character_ids: list[str]) -> None:
    lock = _scene_compression_lock(conversation_id)
    if not lock.acquire(blocking=False):
        logger.info("Scene compression already running for conversation %s", conversation_id)
        return
    try:
        asyncio.run(_run_scene_compression(conversation_id, generated_character_count=generated_character_count, character_ids=character_ids))
    finally:
        lock.release()


def _start_scene_compression_worker(conversation_id: str, *, generated_character_count: int, character_ids: list[str]) -> None:
    # Compatibility hook for callers that still invoke the legacy name. Durable
    # generation enqueues compression in PostCommitTask instead of spawning a thread.
    return None


async def _execute_claimed_post_commit_task(
    session: Session,
    task: PostCommitTask,
    *,
    lease_owner: str,
) -> None:
    if task.status != "processing" or task.lease_owner != lease_owner:
        raise RuntimeError("Post-commit task lease is not owned by this worker")
    payload = dict(task.payload_ or {})
    if task.task_type == "asset":
        message = session.get(Message, payload.get("message_id"))
        asset_id = payload.get("asset_id")
        if message is not None and asset_id:
            asset_service.attach_asset_to_message(session, message.id, str(asset_id), commit=False)
    elif task.task_type == "tts":
        message = session.get(Message, payload.get("message_id"))
        if message is not None:
            character = character_service.get_character(session, message.speaker_id) if message.speaker_id else None
            tts_service.ensure_message_tts(session, message, character=character, commit=False)
    elif task.task_type == "continuity":
        # Compatibility no-op for already queued tasks from the retired
        # auto-derived relationship/continuity pipeline.
        pass
    elif task.task_type == "compression":
        conversation_id = str(payload.get("conversation_id") or "")
        if conversation_id:
            await _run_scene_compression_in_session(
                session,
                conversation_id,
                generated_character_count=int(payload.get("generated_character_count") or 0),
                character_ids=[str(item) for item in (payload.get("character_ids") or []) if item],
                raw_tail_token_budget=(
                    int(payload["raw_tail_token_budget"])
                    if payload.get("raw_tail_token_budget") is not None else None
                ),
                mandatory_prompt_tokens=(
                    int(payload["mandatory_prompt_tokens"])
                    if payload.get("mandatory_prompt_tokens") is not None else None
                ),
            )
            session.expire_all()
            refreshed = session.get(PostCommitTask, task.id)
            if refreshed is None:
                raise RuntimeError("Post-commit task disappeared during compression")
            task = refreshed
    else:
        raise ValueError(f"Unsupported post-commit task type: {task.task_type}")
    if not conversation_service.mark_post_commit_task_completed(
        session,
        task,
        lease_owner=lease_owner,
        commit=False,
    ):
        raise RuntimeError("Post-commit task lease was lost before completion")
    session.commit()


def _run_post_commit_task_sync(task_id: str, lease_owner: str) -> None:
    with Session(engine) as session:
        task = session.get(PostCommitTask, task_id)
        if task is None:
            return
        try:
            asyncio.run(_execute_claimed_post_commit_task(session, task, lease_owner=lease_owner))
        except Exception:
            session.rollback()
            raise


async def _process_inline_post_commit_tasks(bind, task_specs: list[dict]) -> None:
    inline_types = {"asset"}
    for task_spec in task_specs:
        if task_spec.get("task_type") not in inline_types:
            continue
        lease_owner = f"inline:{threading.get_ident()}"
        with Session(bind) as session:
            claimed = conversation_service.claim_post_commit_task_by_unique_key(
                session,
                str(task_spec["unique_key"]),
                lease_owner=lease_owner,
                lease_seconds=60,
            )
            if claimed is None:
                continue
            try:
                await _execute_claimed_post_commit_task(session, claimed, lease_owner=lease_owner)
            except Exception as exc:
                session.rollback()
                refreshed = session.get(PostCommitTask, claimed.id)
                if refreshed is not None and refreshed.status == "processing" and refreshed.lease_owner == lease_owner:
                    conversation_service.mark_post_commit_task_failed(
                        session,
                        refreshed,
                        exc,
                        lease_owner=lease_owner,
                    )
                logger.exception("Inline post-commit task failed: %s", claimed.id)


async def generate_replies_from_message(
    *,
    session: Session,
    conversation_id: str,
    conversation,
    payload: MessageCreate,
    incoming_message: Message,
    recent_messages: list[Message] | None = None,
    include_incoming_in_response: bool = True,
    applied_battle_record=None,
    generation_job: MessageGenerationJob | None = None,
) -> list[Message]:
    replies = [incoming_message] if include_incoming_in_response else []
    participants = conversation_service.get_participants(session, conversation_id)
    character_participants = [p for p in participants if p.participant_type == "character"]
    room_cast_roles = {
        participant.participant_id: participant.role
        for participant in character_participants
        if participant.role
    }
    room_characters = [
        character
        for participant in character_participants
        if (character := character_service.get_character(session, participant.participant_id))
    ]

    effective_mode = "character_character" if len(room_characters) >= 2 else "user_character"
    should_generate = (
        (payload.speaker_type in {"user", "system"} and room_characters)
        or (effective_mode == "character_character" and payload.speaker_type == "character" and room_characters)
    )
    if not should_generate:
        return replies

    scene_state = conversation_service.build_live_scene_state(session, conversation, session.get(SceneState, conversation_id))
    runtime_setting = runtime_settings_service.get_effective_setting(session, conversation_id)
    preset_key = getattr(runtime_setting, "response_length_preset", runtime_settings_service.DEFAULT_RESPONSE_LENGTH_PRESET)
    token_target = runtime_settings_service.preset_token_target(preset_key)
    llm_overrides = runtime_settings_service.llm_overrides_for_setting(runtime_setting, session=session)
    llm_overrides["min_output_tokens"] = token_target
    fallback_overrides = runtime_settings_service.fallback_llm_overrides_for_setting(runtime_setting, session=session)
    fallback_client = None
    if fallback_overrides:
        fallback_overrides["min_output_tokens"] = token_target
        fallback_client = LLMClient(profile="chat", purpose="chat_generation_fallback", overrides=fallback_overrides)
    runtime = CharacterRuntime(
        llm_client=LLMClient(profile="chat", purpose="chat_generation", overrides=llm_overrides),
        fallback_llm_client=fallback_client,
    )
    is_multi_room = effective_mode == "character_character"
    min_reply_bubbles = runtime_settings_service.min_bubbles_for_preset(preset_key, is_multi_room=is_multi_room)
    max_reply_bubbles = runtime_settings_service.max_bubbles_for_preset(preset_key, is_multi_room=is_multi_room)
    prompt_settings = system_prompt_service.prompt_settings_map()
    directive = (
        prompt_settings.get("directive_scene_direction", "Acknowledge this scene direction as true state and continue the room. Do not treat it as user dialogue.")
        if payload.speaker_type == "system"
        else prompt_settings.get("directive_natural_chat", "Generate the next natural chat bubbles for this room. In multi-character rooms, let the listed characters interact in one model call.")
    )
    command_ids = list((incoming_message.metadata_ or {}).get("command_ids") or (incoming_message.metadata_ or {}).get("active_command_ids") or [])
    if not command_ids:
        single_command_id = (incoming_message.metadata_ or {}).get("command_id") or (incoming_message.metadata_ or {}).get("active_command_id")
        command_ids = [single_command_id] if single_command_id else []
    commands = []
    seen_command_ids = set()
    for command_id in command_ids:
        if not command_id or command_id in seen_command_ids:
            continue
        command = chat_command_service.get_chat_command(session, command_id)
        if command and command.enabled:
            commands.append(command)
            seen_command_ids.add(command.id)
    commands.sort(key=lambda item: (-item.priority, item.name))
    overlay_command_active = any((getattr(command, "postprocess_target", "") or "") in {"first_bubble", "last_bubble"} for command in commands)
    generation_prompts = [command.generation_prompt for command in commands if getattr(command, "generation_prompt", "").strip()]
    if generation_prompts:
        command_generation_intro = prompt_settings.get("command_generation_prompt_intro", "")
        directive_parts = [directive]
        if command_generation_intro.strip():
            directive_parts.append(command_generation_intro.strip())
        directive_parts.extend(
            f"- !{command.name}: {command.generation_prompt}"
            for command in commands
            if getattr(command, "generation_prompt", "").strip()
        )
        directive = "\n\n".join(part for part in directive_parts if part)
    if overlay_command_active:
        overlay_guard = prompt_settings.get("command_generation_overlay_guard", "")
        if overlay_guard.strip():
            directive = "\n\n".join(part for part in [directive, overlay_guard.strip()] if part)
    active_character_ids = {character.id for character in room_characters}
    silent_character_ids = {
        participant.participant_id
        for participant in character_participants
        if (participant.role or "").strip().lower().replace("-", "_").replace(" ", "_") == "silent"
    }
    genre_mode = conversation_service.normalize_genre_mode(getattr(conversation, "genre_mode", None))
    official_domain_context = genre_domain_service.get_official_context(session, conversation)
    battle_control_context = build_battle_control_runtime_context(
        session,
        payload,
        applied_record=applied_battle_record,
        conversation_id=conversation_id,
    )
    recent_for_prompt = recent_messages if recent_messages is not None else conversation_service.list_messages(session, conversation_id)
    room_characters = select_runtime_room_characters(
        room_characters=room_characters,
        recent_messages=recent_for_prompt,
        payload=payload,
        battle_control_context=battle_control_context,
    )
    if not room_characters:
        return replies
    active_character_ids = {character.id for character in room_characters}
    character_names = {character.id: character.name for character in room_characters}
    payload_speaker_name = next((character.name for character in room_characters if character.id == payload.speaker_id), None)
    runtime_message_text = runtime_user_message(payload, speaker_name=payload_speaker_name)

    common_memory_context = conversation_service.build_continuity_context(
        conversation_service.list_common_room_memories(session, conversation_id),
        None,
    )
    continuity_by_character = {
        conversation_service.COMMON_ROOM_MEMORY_CHARACTER_ID: common_memory_context,
    }
    external_memory_context_by_character = {}
    for character in room_characters:
        local_context = conversation_service.build_continuity_context(
            conversation_service.list_character_memories(session, conversation_id, character.id),
            None,
        )
        # Runtime prompts use only manual room/character user notes as durable
        # memory. Auto-derived relationship scores and external recall are not
        # injected because they duplicate raw/arc context and are unstable.
        external_memory_context_by_character[character.id] = ""
        continuity_by_character[character.id] = local_context

    context_mode = get_settings().context_management_mode
    selected_model_option = model_provider_service.option_by_key(session, runtime_setting.model_key)
    room_prompt_budget = prompts.prompt_budget_for_context(
        genre_mode=genre_mode,
        is_multi_room=is_multi_room,
    )
    fallback_model_option = (
        model_provider_service.option_by_key(
            session,
            getattr(runtime_setting, "fallback_model_key", None),
        )
        if fallback_client is not None
        else None
    )
    capacity = resolve_safe_model_context_capacity(
        selected_model_option,
        fallback_model_option,
        room_prompt_budget_tokens=room_prompt_budget,
    )
    job_mapping = conversation_service.generation_job_source_message_ids(
        session,
        conversation_id,
        extra={generation_job.id: incoming_message.id} if generation_job else None,
    )

    latest_offstage_context = ""

    def build_generation_harness(current_scene: SceneState, current_messages: list[Message]):
        nonlocal latest_offstage_context
        base_recall_query = "\n".join(filter(None, [
            f"genre={genre_mode}",
            current_scene.world_seed,
            current_scene.current_conflict,
            current_scene.summary,
            runtime_message_text,
        ]))[:800]
        offstage_recall = domain_actor_service.build_offstage_actor_recall_context(
            session,
            conversation,
            text=base_recall_query,
            active_character_ids=active_character_ids,
            genre_mode=genre_mode,
            query=base_recall_query,
        )
        latest_offstage_context = offstage_recall.content
        return prompts.build_multi_character_prompt_harness(
            characters=room_characters,
            recent_messages=[message for message in current_messages if message.id != incoming_message.id],
            user_message=runtime_message_text,
            scene_state=current_scene,
            directive=directive,
            conversation_mode=effective_mode,
            genre_mode=genre_mode,
            continuity_context_by_character=continuity_by_character,
            relationship_context_by_character={},
            external_memory_context_by_character=external_memory_context_by_character,
            offstage_actor_context=offstage_recall.content,
            min_bubbles=min_reply_bubbles,
            max_bubbles=max_reply_bubbles,
            min_output_tokens=token_target,
            prompt_settings=prompt_settings,
            battle_control_context=battle_control_context,
            official_domain_context=official_domain_context,
            room_cast_roles=room_cast_roles,
            provider_type=getattr(selected_model_option, "provider_type", None),
            total_budget_tokens=room_prompt_budget,
            context_management_mode=context_mode,
        )

    prepared_harness = None
    pressure_plan = None
    generation_mandatory_without_current = 0
    catchup_batches = 0
    if context_mode == "automatic":
        compression_overrides = runtime_settings_service.compression_llm_overrides_for_setting(runtime_setting, session=session)
        compression_fallback_overrides = runtime_settings_service.compression_fallback_llm_overrides_for_setting(runtime_setting, session=session)

        rendered_history_token_estimator = conversation_service.automatic_context_message_token_estimator(
            character_names
        )

        def generation_history_token_estimator(message: Message) -> int:
            if message.id == incoming_message.id:
                return 0
            return rendered_history_token_estimator(message)

        current_turn_control_tokens = generation_control_reserve_tokens(
            speaker_type=payload.speaker_type,
            min_bubbles=min_reply_bubbles,
            max_bubbles=max_reply_bubbles,
        )

        def evaluate_automatic_context() -> AutomaticGenerationContextEvaluation:
            current_messages = conversation_service.list_messages(session, conversation_id)
            current_job_mapping = conversation_service.generation_job_source_message_ids(
                session,
                conversation_id,
                extra=(
                    {generation_job.id: incoming_message.id}
                    if generation_job else None
                ),
            )
            current_scene = conversation_service.build_live_scene_state(
                session,
                conversation,
                session.get(SceneState, conversation_id),
            )
            harness = build_generation_harness(current_scene, current_messages)
            recent_entry = next(
                (entry for entry in harness.ledger if entry.key == "recent_messages"),
                None,
            )
            recent_section = next(
                (section for section in harness.sections if section.key == "recent_messages"),
                None,
            )
            current_input_tokens = approx_tokens(runtime_message_text)
            mandatory_tokens = (
                harness.used_tokens
                - min(
                    int(getattr(recent_entry, "used_tokens", 0) or 0),
                    approx_tokens(getattr(recent_section, "content", "") or ""),
                )
                + current_input_tokens
                + current_turn_control_tokens
            )
            plan = conversation_service.automatic_context_plan(
                session.get(SceneState, conversation_id) or SceneState(conversation_id=conversation_id),
                current_messages,
                capacity=capacity,
                mandatory_prompt_tokens=mandatory_tokens,
                job_source_message_ids=current_job_mapping,
                token_estimator=generation_history_token_estimator,
            )
            return AutomaticGenerationContextEvaluation(
                scene_state=current_scene,
                messages=tuple(current_messages),
                harness=harness,
                plan=plan,
                mandatory_without_current=max(0, mandatory_tokens - current_input_tokens),
                job_source_message_ids=current_job_mapping,
            )

        async def compress_automatic_context(
            evaluation: AutomaticGenerationContextEvaluation,
        ) -> bool:
            persisted_before = session.get(SceneState, conversation_id)
            before_revision = int(
                getattr(persisted_before, "compression_revision", 0) or 0
            )
            before_boundary = getattr(
                persisted_before,
                "last_compression_source_message_id",
                None,
            )
            await conversation_service.update_scene_orchestration_summary(
                session,
                conversation_id,
                list(evaluation.messages),
                llm_client=LLMClient(profile="compression", purpose="conversation_compression", overrides=compression_overrides),
                fallback_llm_client=(
                    LLMClient(profile="compression", purpose="conversation_compression_fallback", overrides=compression_fallback_overrides)
                    if compression_fallback_overrides else None
                ),
                character_ids=[character.id for character in room_characters],
                raw_tail_token_budget=evaluation.plan.raw_tail_token_budget,
                job_source_message_ids=evaluation.job_source_message_ids,
                token_estimator=generation_history_token_estimator,
            )
            session.expire_all()
            persisted_after = session.get(SceneState, conversation_id)
            after_revision = int(
                getattr(persisted_after, "compression_revision", 0) or 0
            )
            after_boundary = getattr(
                persisted_after,
                "last_compression_source_message_id",
                None,
            )
            return (
                after_revision > before_revision
                and after_boundary != before_boundary
            )

        evaluation, catchup_batches = await _ensure_automatic_generation_context(
            evaluate=evaluate_automatic_context,
            compress_once=compress_automatic_context,
            capacity=capacity,
        )
        scene_state = evaluation.scene_state
        prepared_harness = evaluation.harness
        pressure_plan = evaluation.plan
        generation_mandatory_without_current = evaluation.mandatory_without_current
        job_mapping = evaluation.job_source_message_ids
    else:
        prepared_harness = build_generation_harness(scene_state, recent_for_prompt)
        recent_entry = next(
            (entry for entry in prepared_harness.ledger if entry.key == "recent_messages"),
            None,
        )
        recent_section = next(
            (section for section in prepared_harness.sections if section.key == "recent_messages"),
            None,
        )
        generation_mandatory_without_current = (
            prepared_harness.used_tokens
            - min(
                int(getattr(recent_entry, "used_tokens", 0) or 0),
                approx_tokens(getattr(recent_section, "content", "") or ""),
            )
        )

    async def persist_prompt_snapshot(snapshot: dict):
        try:
            prompt_snapshot_service.record_prompt_snapshot(session, **snapshot)
        except Exception:
            session.rollback()
            logger.exception("Non-critical prompt snapshot persistence failed for conversation %s", conversation_id)

    generated_replies = await runtime.generate_replies(
        characters=room_characters,
        recent_messages=[message for message in recent_for_prompt if message.id != incoming_message.id],
        user_message=runtime_message_text,
        scene_state=scene_state,
        directive=directive,
        conversation_mode=effective_mode,
        genre_mode=genre_mode,
        continuity_context_by_character=continuity_by_character,
        relationship_context_by_character={},
        external_memory_context_by_character=external_memory_context_by_character,
        offstage_actor_context=latest_offstage_context,
        min_bubbles=min_reply_bubbles,
        max_bubbles=max_reply_bubbles,
        min_output_tokens=token_target,
        prompt_settings=prompt_settings,
        conversation_id=conversation_id,
        battle_control_context=battle_control_context,
        official_domain_context=official_domain_context,
        room_cast_roles=room_cast_roles,
        persist_replies=None,
        source_message_id=incoming_message.id,
        source_speaker_type=payload.speaker_type,
        prompt_snapshot_callback=persist_prompt_snapshot,
        prepared_harness=prepared_harness,
    )
    generated_messages = await persist_generated_reply_messages(
        session=session,
        conversation_id=conversation_id,
        conversation=conversation,
        payload=payload,
        incoming_message=incoming_message,
        generated_replies=generated_replies,
        room_characters=room_characters,
        scene_state=scene_state,
        is_multi_room=is_multi_room,
        min_bubbles=min_reply_bubbles,
        silent_character_ids=silent_character_ids,
    )
    # Commands were resolved before generation so generation_prompt can affect the first pass.
    # Their visible mutations remain transient until the fresh finalization transaction.
    for command in commands:
        generated_messages = await chat_command_service.apply_chat_command_postprocess(
            session=session,
            command=command,
            conversation_id=conversation_id,
            source_message=incoming_message,
            generated_messages=generated_messages,
            llm_client=LLMClient(profile="chat", purpose="chat_command_postprocess", overrides=llm_overrides),
            prompt_settings=prompt_settings,
            commit=False,
        )
    character_ids = [character.id for character in room_characters]
    turn_group_id = assign_generated_turn_group(
        generated_messages,
        incoming_message=incoming_message,
        generation_job=generation_job,
    )
    generated_character_count = sum(1 for message in generated_messages if message.speaker_type == "character")
    if context_mode == "automatic":
        job_mapping = conversation_service.generation_job_source_message_ids(
            session,
            conversation_id,
            extra={turn_group_id: incoming_message.id},
        )
    enqueue_compression = generated_character_count > 0 and conversation_service.should_update_scene_orchestration_summary(
        session,
        conversation_id,
        generated_character_messages=generated_character_count,
        interval_turns=runtime_settings_service.clamp_compression_interval_turns(
            getattr(runtime_setting, "compression_interval_turns", runtime_settings_service.DEFAULT_COMPRESSION_INTERVAL_TURNS)
        ),
        prospective_messages=generated_messages,
        context_management_mode=context_mode,
        capacity=capacity,
        mandatory_prompt_tokens=generation_mandatory_without_current,
        job_source_message_ids=job_mapping,
        token_estimator=(
            rendered_history_token_estimator
            if context_mode == "automatic"
            else None
        ),
    )
    compression_raw_tail_token_budget = None
    if enqueue_compression and context_mode == "automatic":
        candidate_messages = conversation_service.list_messages(session, conversation_id)
        persisted_ids = {message.id for message in candidate_messages}
        candidate_messages.extend(message for message in generated_messages if message.id not in persisted_ids)
        enqueue_plan = conversation_service.automatic_context_plan(
            session.get(SceneState, conversation_id) or SceneState(conversation_id=conversation_id),
            candidate_messages,
            capacity=capacity,
            mandatory_prompt_tokens=generation_mandatory_without_current,
            job_source_message_ids=job_mapping,
            token_estimator=rendered_history_token_estimator,
        )
        compression_raw_tail_token_budget = enqueue_plan.raw_tail_token_budget
    post_commit_tasks = build_generated_turn_post_commit_tasks(
        session=session,
        conversation=conversation,
        scene_state=scene_state,
        incoming_message=incoming_message,
        generated_messages=generated_messages,
        enqueue_compression=enqueue_compression,
        character_ids=character_ids,
        generation_job_id=generation_job.id if generation_job else None,
        generation_attempt_id=turn_group_id,
        compression_raw_tail_token_budget=compression_raw_tail_token_budget,
        compression_mandatory_prompt_tokens=(
            generation_mandatory_without_current if context_mode == "automatic" else None
        ),
    )
    source_metadata = dict(incoming_message.metadata_ or {})
    finalization_bind = session.get_bind()
    session.rollback()
    with Session(finalization_bind) as final_session:
        final_job = final_session.get(MessageGenerationJob, generation_job.id) if generation_job else None
        if final_job and final_job.cancel_requested_at is not None:
            conversation_service.mark_message_generation_job_cancelled(final_session, final_job)
            return replies
        generated_messages = conversation_service.finalize_message_generation_turn(
            final_session,
            final_job,
            generated_messages,
            source_message_id=incoming_message.id,
            source_metadata=source_metadata,
            post_commit_tasks=post_commit_tasks,
        )
    await _process_inline_post_commit_tasks(finalization_bind, post_commit_tasks)
    post_commit_dispatcher.wake()
    replies.extend(generated_messages)
    return replies


def assign_generated_turn_group(
    generated_messages: list[Message],
    *,
    incoming_message: Message,
    generation_job: MessageGenerationJob | None,
) -> str:
    if not generated_messages:
        raise ValueError("A generation attempt requires at least one reply")
    turn_group_id = (
        generation_job.id
        if generation_job is not None
        else sync_turn_group_id(incoming_message.id, generated_messages[0].id)
    )
    for reply_index, generated_message in enumerate(generated_messages):
        generated_message.generation_job_id = turn_group_id
        generated_message.reply_index = reply_index
    return turn_group_id


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


def _message_payload_from_stored_message(message: Message) -> MessageCreate:
    metadata = dict(message.metadata_ or {})
    return MessageCreate(
        speaker_type=message.speaker_type,
        speaker_id=message.speaker_id,
        content=message.content,
        action=message.action,
        thought=message.thought,
        metadata=metadata,
        battle_control=metadata.get("battle_control"),
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


async def _run_message_generation_job_in_session(
    session: Session,
    job_id: str,
    lease_owner: str | None = None,
) -> None:
    job = conversation_service.get_message_generation_job(session, job_id)
    if not job or job.status in {"completed", "failed", "cancelled"}:
        return
    if job.status == "queued" and lease_owner is None:
        job = conversation_service.mark_message_generation_job_running(session, job)
    elif job.status != "running":
        return
    if lease_owner is not None and job.lease_owner != lease_owner:
        return
    if job.cancel_requested_at is not None:
        conversation_service.mark_message_generation_job_cancelled(session, job)
        return
    conversation = conversation_service.get_conversation(session, job.conversation_id)
    incoming_message = conversation_service.get_message(session, job.incoming_message_id)
    if not conversation or not incoming_message:
        conversation_service.mark_message_generation_job_failed(session, job, RuntimeError("Conversation or incoming message not found"))
        return
    payload = _message_payload_from_stored_message(incoming_message)
    try:
        replies = await generate_replies_from_message(
            session=session,
            conversation_id=job.conversation_id,
            conversation=conversation,
            payload=payload,
            incoming_message=incoming_message,
            include_incoming_in_response=False,
            generation_job=job,
        )
        session.expire_all()
        fresh_job = conversation_service.get_message_generation_job(session, job_id)
        if fresh_job and fresh_job.status == "running":
            conversation_service.mark_message_generation_job_completed(
                session,
                fresh_job,
                [message.id for message in replies],
            )
    except Exception as exc:
        session.rollback()
        fresh_job = conversation_service.get_message_generation_job(session, job_id)
        if fresh_job and fresh_job.status == "completed":
            logger.warning("Generation finalization committed before a late runner error for job=%s", job_id)
            return
        fresh_incoming = conversation_service.get_message(session, incoming_message.id)
        if fresh_incoming:
            conversation_service.mark_message_generation_failed(session, fresh_incoming, exc)
        fresh_job = conversation_service.get_message_generation_job(session, job_id)
        if fresh_job and fresh_job.status == "running":
            conversation_service.mark_message_generation_job_failed(session, fresh_job, exc)
        logger.exception("Message generation job failed for job=%s conversation=%s source_message=%s", job_id, job.conversation_id, incoming_message.id)


async def _run_message_generation_job(job_id: str, lease_owner: str | None = None) -> None:
    with Session(engine) as session:
        await _run_message_generation_job_in_session(session, job_id, lease_owner)


def _run_message_generation_job_sync(job_id: str, lease_owner: str) -> None:
    asyncio.run(_run_message_generation_job(job_id, lease_owner))


def _start_message_generation_worker(_job_id: str) -> None:
    generation_dispatcher.wake()


@router.post("/{conversation_id}/messages/jobs", response_model=MessageGenerationJobCreateRead)
async def create_message_generation_job(
    conversation_id: str,
    payload: MessageCreate,
    session: Session = Depends(get_session),
):
    conversation = conversation_service.get_conversation(session, conversation_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")
    payload = _with_persisted_battle_control(_prepare_message_payload_for_post(session, conversation, payload))
    if not payload.content.strip() and not (payload.action or "").strip():
        raise HTTPException(status_code=422, detail="Message content or action is required")
    metadata = payload.metadata if isinstance(payload.metadata, dict) else {}
    client_request_id = metadata.get("client_request_id")
    existing_incoming_message = conversation_service.find_incoming_message_by_client_request_id(session, conversation_id, client_request_id)
    if existing_incoming_message:
        existing_job = conversation_service.get_message_generation_job_for_incoming_message(session, existing_incoming_message.id)
        if existing_job:
            if existing_job.status == "queued":
                _start_message_generation_worker(existing_job.id)
            return MessageGenerationJobCreateRead(
                job=_serialize_generation_job(existing_job),
                incoming_message=MessageRead.model_validate(asset_service.serialize_message(session, existing_incoming_message)),
            )
    incoming_message = conversation_service.add_message(session, conversation_id, payload)
    if payload.battle_control:
        try:
            battle_ledger_service.apply_battle_control(session, conversation, payload.battle_control, message_id=incoming_message.id)
        except ValueError as exc:
            fresh_incoming = conversation_service.get_message(session, incoming_message.id)
            if fresh_incoming:
                conversation_service.delete_message(session, fresh_incoming)
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    if payload.speaker_type == "system":
        conversation_service.record_system_scene_direction(session, conversation_id, payload.content)
    job = conversation_service.create_message_generation_job(session, conversation_id, incoming_message.id)
    _start_message_generation_worker(job.id)
    return MessageGenerationJobCreateRead(
        job=_serialize_generation_job(job),
        incoming_message=MessageRead.model_validate(asset_service.serialize_message(session, incoming_message)),
    )


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
                    if event.id is not None
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
async def post_message(conversation_id: str, payload: MessageCreate, session: Session = Depends(get_session)):
    conversation = conversation_service.get_conversation(session, conversation_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")
    payload = _with_persisted_battle_control(_prepare_message_payload_for_post(session, conversation, payload))
    if not payload.content.strip() and not (payload.action or "").strip():
        raise HTTPException(status_code=422, detail="Message content or action is required")
    incoming_message = conversation_service.add_message(session, conversation_id, payload)
    applied_battle_record = None
    if payload.battle_control:
        try:
            applied_battle_record = battle_ledger_service.apply_battle_control(session, conversation, payload.battle_control, message_id=incoming_message.id)
        except ValueError as exc:
            fresh_incoming = conversation_service.get_message(session, incoming_message.id)
            if fresh_incoming:
                conversation_service.delete_message(session, fresh_incoming)
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    if payload.speaker_type == "system":
        conversation_service.record_system_scene_direction(session, conversation_id, payload.content)
    try:
        replies = await generate_replies_from_message(
            session=session,
            conversation_id=conversation_id,
            conversation=conversation,
            payload=payload,
            incoming_message=incoming_message,
            include_incoming_in_response=True,
            applied_battle_record=applied_battle_record,
        )
    except ContextMaintenanceRequired as exc:
        fresh_incoming = conversation_service.get_message(session, incoming_message.id)
        if fresh_incoming:
            conversation_service.mark_message_generation_failed(session, fresh_incoming, exc)
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
        fresh_incoming = conversation_service.get_message(session, incoming_message.id)
        if fresh_incoming:
            conversation_service.mark_message_generation_failed(session, fresh_incoming, exc)
        logger.exception(
            "LLM generation failed for conversation=%s source_message=%s speaker_type=%s speaker_id=%s",
            conversation_id,
            incoming_message.id,
            payload.speaker_type,
            payload.speaker_id,
        )
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        fresh_incoming = conversation_service.get_message(session, incoming_message.id)
        if fresh_incoming:
            conversation_service.mark_message_generation_failed(session, fresh_incoming, exc)
        logger.exception(
            "Message generation failed for conversation=%s source_message=%s speaker_type=%s speaker_id=%s",
            conversation_id,
            incoming_message.id,
            payload.speaker_type,
            payload.speaker_id,
        )
        raise
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
    replies = await generate_replies_from_message(
        session=session,
        conversation_id=conversation_id,
        conversation=conversation,
        payload=message_payload,
        incoming_message=selected_message,
        recent_messages=recent_messages,
        include_incoming_in_response=False,
    )
    return [asset_service.serialize_message(session, message) for message in replies]
