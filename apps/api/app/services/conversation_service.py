"""Conversation CRUD, manual notes and relationship reads.

Existing callers may keep using this facade; durable jobs and compression own
their implementations in dedicated services, with no upward imports here.
"""

import re
from typing import Any
from uuid import uuid4
from datetime import datetime, timezone
from app.engine import free_body_protocol as free_body
from sqlmodel import Session, select, delete
from sqlalchemy import text
from app.db.models import Character, Conversation, ConversationParticipant, ConversationReadState, ConversationRelationshipState, CharacterMemory, Message, SceneState, MessageAsset, BattleMatchRecord, BattleStanding, RuntimeSetting, WorldSetting
from app.schemas.conversations import ConversationCreate, ConversationUpdate, MessageCreate, ParticipantCreate
from app.services import external_memory_service
from app.services.conversation_job_service import (
    GenerationReceiptCorrupt,
    GenerationReceiptDeleted,
    append_generation_job_event,
    claim_next_message_generation_job,
    claim_next_post_commit_task,
    claim_post_commit_task_by_unique_key,
    create_message_generation_job,
    enqueue_post_commit_task,
    finalize_message_generation_turn,
    find_incoming_message_by_client_request_id,
    get_message_generation_job,
    get_message_generation_job_for_incoming_message,
    heartbeat_message_generation_job,
    heartbeat_post_commit_task,
    is_message_generation_job_cancel_requested,
    mark_message_generation_job_cancelled,
    mark_message_generation_job_completed,
    mark_message_generation_job_failed,
    mark_message_generation_job_running,
    mark_post_commit_task_completed,
    mark_post_commit_task_failed,
    mark_stale_message_generation_jobs_failed,
    request_message_generation_job_cancel,
    requeue_expired_message_generation_jobs,
    requeue_expired_post_commit_tasks,
    sortable_datetime,
)
from app.services.conversation_compression_service import (
    BATTLE_COMPRESSION_CONTRACT,
    COMPRESSION_BATCH_MESSAGE_LIMIT,
    COMPRESSION_RECENT_MESSAGE_LIMIT,
    DURABLE_RECENT_KEYWORDS,
    EXPLICIT_LOCATION_KEYWORDS,
    FAST_COMPRESSION_CONTRACT,
    MAX_SCENE_MEMORY_BULLETS,
    MAX_SCENE_MEMORY_BULLET_CHARS,
    QUALITY_CRITIC_RESPONSE_FORMAT,
    QUALITY_CRITIC_SYSTEM,
    QUALITY_EXTRACTION_RESPONSE_FORMAT,
    QUALITY_EXTRACTION_SYSTEM,
    QUALITY_FINAL_SYSTEM,
    SCENE_SUMMARY_RAW_MARKER_RE,
    SceneSummaryRevisionConflict,
    TRANSIENT_STORY_ARC_MARKERS,
    apply_conversation_compression_update,
    automatic_context_history_token_estimator,
    automatic_context_message_token_estimator,
    automatic_context_message_tokens,
    automatic_context_plan,
    build_compact_scene_memory,
    build_compression_memory_source,
    build_compression_prompt_harness,
    build_quality_compression_evidence_source,
    build_scene_memory_source,
    build_structured_scene_patch,
    compression_error_summary,
    compression_genre_contract,
    compression_raw_tail_start,
    compression_source_fingerprint,
    durable_recent_story_arc_bullets,
    extract_durable_memories_with_llm,
    extract_summary_section_bullets,
    filter_durable_story_arc_bullets,
    format_long_term_memories_for_compression,
    generation_job_source_message_ids,
    infer_scene_location_from_recent,
    infer_scene_location_from_text,
    latest_public_scene_anchor,
    latest_visible_scene_anchor,
    messages_after_compression_boundary,
    normalize_compression_strategy,
    previous_story_arc_for_compression,
    relationship_archetype_blocks_memory,
    select_incremental_compression_batch,
    should_update_scene_orchestration_summary,
    summarize_conversation_state_with_llm,
    summarize_scene_memory_with_llm,
    update_scene_orchestration_summary,
    update_scene_summary_manually,
    validate_compression_update_for_persistence,
    validate_conversation_compression_update,
    validate_scene_memory_summary,
)
from app.services.conversation_memory_policy import (
    BATTLE_FINAL_TOKENS,
    BATTLE_FLOW_NOISE_TOKENS,
    BATTLE_LEDGER_KEYS,
    BATTLE_RANKING_TOKENS,
    DURABLE_MEMORY_NOISE_TOKENS,
    DURABLE_MEMORY_POSITIVE_TOKENS,
    LEAGUE_HISTORY_TOKENS,
    LEAGUE_STANDINGS_TOKENS,
    LEAGUE_TOKENS,
    MAX_SCENE_MEMORY_CHARS,
    MEMORY_DEDUPE_STOPWORDS,
    battle_ranking_memory_key,
    clamp_state_value,
    compact_block_text,
    compact_text,
    contains_rank_ledger_claim,
    extract_ledger_name_id_pairs,
    has_ledger_name_id_mismatch,
    is_battle_flow_noise,
    is_battle_ledger_key,
    is_durable_room_memory,
    is_league_ledger_key,
    league_memory_key,
    memories_are_similar,
    memory_already_exists,
    memory_ledger_key,
    memory_similarity_tokens,
    normalize_memory_fingerprint,
    official_battle_memory_noise,
    parse_json_object_from_text,
    scene_summary_copies_recent_raw_text,
    strip_rank_claims_from_scene_summary,
)
from app.services.conversation_queries import (
    COMMON_ROOM_MEMORY_CHARACTER_ID,
    GLOBAL_CHARACTER_MEMORY_CONVERSATION_ID,
    MAX_CHARACTER_MEMORY_ITEMS,
    MAX_CONTEXT_MEMORY_SCAN_ITEMS,
    get_character_memory,
    get_conversation,
    get_message,
    get_participants,
    list_character_memories,
    list_common_room_memories,
    list_continuity_memories_for_character,
    list_messages,
    list_visible_durable_memories_for_compression,
    visible_memory_character_ids,
)
from app.services.genre_domain_service import (
    normalize_conversation_genre,
    VALID_GENRE_MODES,
    normalize_genre_mode,
)


MAX_RELATIONSHIP_CONTEXT_ITEMS = 2


DEFAULT_USER_ID = "user_001"


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
    if payload.world_setting_id is not None and world_setting is None:
        raise ValueError("World setting not found")
    effective_genre_mode = normalize_conversation_genre(world_setting.genre_mode if world_setting else payload.genre_mode)
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
    # Scene/history state is independent of the optional world.
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
    if "world_setting_id" in data and data["world_setting_id"] is not None:
        if not data["world_setting_id"] or not session.get(WorldSetting, data["world_setting_id"]):
            raise ValueError("World setting not found")
    if "title" in data and data["title"] is not None:
        title = data["title"].strip()
        if title:
            conversation.title = title
    if "thumbnail_url" in data:
        conversation.thumbnail_url = (data.get("thumbnail_url") or "").strip() or None
    if "genre_mode" in data:
        conversation.genre_mode = normalize_conversation_genre(data["genre_mode"])
    if "world_setting_id" in data:
        conversation.world_setting_id = data["world_setting_id"]
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
    active_match = _active_battle_match(session, conversation.id) if normalize_conversation_genre(conversation.genre_mode) == "battle" else None
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


def _record_generation_reply_deletion(session: Session, message: Message) -> None:
    from app.db.models import GenerationReplyDeletion
    if message.generation_job_id and session.get(GenerationReplyDeletion, message.id) is None:
        session.add(GenerationReplyDeletion(message_id=message.id, job_id=message.generation_job_id,
            conversation_id=message.conversation_id, reply_index=message.reply_index))


def delete_message(session: Session, message: Message) -> None:
    _record_generation_reply_deletion(session, message)
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
        _record_generation_reply_deletion(session, found_by_id[message_id])
        session.delete(found_by_id[message_id])
    conversation = get_conversation(session, conversation_id)
    if conversation:
        conversation.updated_at = datetime.now(timezone.utc)
        session.add(conversation)
    session.commit()
    return deleted_ids, missing_ids


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
    genre_mode = normalize_conversation_genre(getattr(conversation, "genre_mode", None))
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
    genre_mode = normalize_conversation_genre(getattr(conversation, "genre_mode", None))
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
    if metadata.get("command_postprocess_status") == "pending":
        metadata.update({
            "command_postprocess_status": "skipped_generation_failed",
            "command_postprocess_error_code": "generation_failed",
        })
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


def conversation_has_battle_ledger(memories: list[CharacterMemory]) -> bool:
    return any(is_battle_ledger_key(battle_ranking_memory_key(memory.content)) for memory in memories)


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
            f"- {memory.memory_type}/{memory.importance}: {memory.content if memory.memory_type == 'user_note' else compact_text(memory.content, 100)}"
            for memory in memories
            if memory.character_id == COMMON_ROOM_MEMORY_CHARACTER_ID
        ]
        character_lines = [
            f"- {memory.memory_type}/{memory.importance}: {memory.content if memory.memory_type == 'user_note' else compact_text(memory.content, 100)}"
            for memory in memories
            if memory.character_id != COMMON_ROOM_MEMORY_CHARACTER_ID
        ]
        if common_lines:
            sections.append("[Common room memory known by all characters]\n" + "\n".join(common_lines[:MAX_CHARACTER_MEMORY_ITEMS]))
        if character_lines:
            sections.append("[Long-term character memory]\n" + "\n".join(character_lines[:MAX_CHARACTER_MEMORY_ITEMS]))
    return "\n\n".join(sections)


def update_message_bubble(session: Session, message: Message, changes: dict) -> Message:
    editable_fields = {"content", "emotion", "action", "thought", "raw_body"}
    normalized_changes = {key: changes[key] for key in editable_fields if key in changes}
    if not normalized_changes:
        raise ValueError("At least one editable bubble field is required")

    existing_metadata = dict(message.metadata_ or {})
    is_free_body = existing_metadata.get("response_protocol") in free_body.PROTOCOLS
    if "raw_body" in normalized_changes and not is_free_body:
        raise ValueError("raw_body edits require a free-body message")
    if is_free_body and set(normalized_changes) & {"content", "action", "thought"}:
        raise ValueError("Edit raw_body to preserve ordered free-body semantics")
    parsed_parts = None
    if "raw_body" in normalized_changes:
        parsed_parts = free_body.parse_body(normalized_changes["raw_body"])
        if message.speaker_type == "storytelling" and any(p["type"] == "thought" for p in parsed_parts):
            raise ValueError("Narration cannot own private thoughts")
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
    if parsed_parts is not None:
        values = free_body.aggregates(parsed_parts)
        content, action, thought = values["dialogue"], values["action"], values["thought"]
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
    if is_free_body:
        # Emotion-only updates retain the exact body and interleaving.
        if parsed_parts is not None:
            metadata["raw_body"] = normalized_changes["raw_body"]
            metadata["render_parts"] = parsed_parts
    else:
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


def delete_conversation(session: Session, conversation_id: str) -> bool:
    conversation = session.get(Conversation, conversation_id)
    if not conversation:
        return False
    # Explicit whole-room deletion also removes content-free durable identities.
    # Ordinary message deletion retains them to fence replay/resurrection.
    # Delete queued external work before deleting the source/job rows it names.
    params = {"conversation_id": conversation_id}
    session.execute(text("""
        DELETE FROM post_commit_tasks
        WHERE CASE WHEN json_valid(payload) THEN json_extract(payload, '$.conversation_id') END = :conversation_id
           OR CASE WHEN json_valid(payload) THEN json_extract(payload, '$.job_id') END IN
                (SELECT id FROM message_generation_jobs WHERE conversation_id = :conversation_id)
           OR CASE WHEN json_valid(payload) THEN json_extract(payload, '$.message_id') END IN
                (SELECT id FROM messages WHERE conversation_id = :conversation_id)
    """), params)
    for table in ("generation_job_events", "generation_reply_deletions", "generation_acceptance_receipts", "message_generation_jobs"):
        session.execute(text(f"DELETE FROM {table} WHERE conversation_id = :conversation_id"), params)
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
