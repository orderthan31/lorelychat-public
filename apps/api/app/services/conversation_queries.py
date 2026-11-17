"""Read-only conversation, message-window and manual-memory queries shared by services."""

from sqlmodel import Session, select
from sqlalchemy import text
from app.db.models import Conversation, ConversationParticipant, CharacterMemory, Message
from app.services.conversation_memory_policy import (
    is_durable_room_memory,
    normalize_memory_fingerprint,
)


MAX_CHARACTER_MEMORY_ITEMS = 3


MAX_CONTEXT_MEMORY_SCAN_ITEMS = 8


COMMON_ROOM_MEMORY_CHARACTER_ID = "__room__"


GLOBAL_CHARACTER_MEMORY_CONVERSATION_ID = "__global__"


def get_conversation(session: Session, conversation_id: str) -> Conversation | None:
    return session.get(Conversation, conversation_id)


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
    from sqlalchemy import tuple_

    upper_message = session.get(Message, before_id) if before_id else None
    if before_id and (not upper_message or upper_message.conversation_id != conversation_id):
        return []

    conditions = [Message.conversation_id == conversation_id]
    upper_bound = None
    if upper_message:
        upper_bound = tuple_(Message.created_at, Message.id) < (upper_message.created_at, upper_message.id)
        conditions.append(upper_bound)

    if recent_turns:
        bounded_turns = max(1, min(50, recent_turns))
        turn_conditions = [Message.conversation_id == conversation_id, Message.speaker_type.in_({"user", "system"})]
        if upper_bound is not None:
            turn_conditions.append(upper_bound)
        turn_stmt = (
            select(Message)
            .where(*turn_conditions)
            .order_by(Message.created_at.desc(), Message.id.desc())
            .limit(bounded_turns)
        )
        anchors = list(session.exec(turn_stmt).all())
        if not anchors:
            stmt = select(Message).where(*conditions).order_by(text("created_at DESC"), text("id DESC")).limit(max(1, min(200, recent_turns)))
            return list(reversed(session.exec(stmt).all()))
        if len(anchors) >= bounded_turns:
            anchor = anchors[-1]
            conditions.append(tuple_(Message.created_at, Message.id) >= (anchor.created_at, anchor.id))
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


def get_character_memory(session: Session, memory_id: str) -> CharacterMemory | None:
    return session.get(CharacterMemory, memory_id)


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
