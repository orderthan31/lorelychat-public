from uuid import uuid4
from sqlmodel import Session, select
from app.db.models import Character, ConversationParticipant, Message, utc_now
from app.schemas.characters import CharacterCreate, CharacterUpdate, DEFAULT_FORBIDDEN_RULES, DEFAULT_TRAIT_SCORES


def ensure_trait_scores(character: Character) -> Character:
    if not character.trait_scores:
        character.trait_scores = DEFAULT_TRAIT_SCORES.copy()
    else:
        character.trait_scores = {**DEFAULT_TRAIT_SCORES, **character.trait_scores}
    return character


def create_character(session: Session, payload: CharacterCreate) -> Character:
    forbidden = payload.forbidden_rules or DEFAULT_FORBIDDEN_RULES.copy()
    data = payload.model_dump(exclude={"forbidden_rules"})
    data["trait_scores"] = {**DEFAULT_TRAIT_SCORES, **data.get("trait_scores", {})}
    character = Character(id=f"char_{uuid4().hex[:12]}", **data, forbidden_rules=forbidden)
    session.add(character)
    session.commit()
    session.refresh(character)
    return character


def list_characters(session: Session) -> list[Character]:
    return [ensure_trait_scores(character) for character in session.exec(select(Character)).all()]


def list_characters_by_usage(session: Session) -> list[dict]:
    characters = list_characters(session)
    message_counts: dict[str, int] = {}
    room_counts: dict[str, int] = {}
    for character_id in session.exec(select(Message.speaker_id).where(Message.speaker_type == "character")).all():
        message_counts[character_id] = message_counts.get(character_id, 0) + 1
    for participant_id in session.exec(select(ConversationParticipant.participant_id).where(ConversationParticipant.participant_type == "character")).all():
        room_counts[participant_id] = room_counts.get(participant_id, 0) + 1

    ranked = []
    for character in characters:
        message_count = message_counts.get(character.id, 0)
        room_count = room_counts.get(character.id, 0)
        usage_count = message_count or room_count
        item = character.model_dump()
        item.update({"message_count": message_count, "room_count": room_count, "usage_count": usage_count})
        ranked.append(item)
    ranked.sort(key=lambda item: (-item["message_count"], -item["room_count"], item["name"]))
    return ranked


def get_character(session: Session, character_id: str) -> Character | None:
    character = session.get(Character, character_id)
    return ensure_trait_scores(character) if character else None


def update_character(session: Session, character: Character, payload: CharacterUpdate) -> Character:
    for key, value in payload.model_dump(exclude_unset=True).items():
        if key == "trait_scores" and value is not None:
            value = {**DEFAULT_TRAIT_SCORES, **value}
        setattr(character, key, value)
    character.updated_at = utc_now()
    session.add(character)
    session.commit()
    session.refresh(character)
    return character


def delete_character(session: Session, character: Character) -> None:
    session.delete(character)
    session.commit()
