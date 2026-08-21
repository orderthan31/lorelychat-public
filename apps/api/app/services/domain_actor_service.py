from __future__ import annotations

import re
from dataclasses import dataclass, field

from sqlmodel import Session

from app.db.models import Character, Conversation
from app.services import character_service, conversation_service

MAX_OFFSTAGE_ACTORS = 4
MAX_LOCAL_MEMORIES_PER_ACTOR = 2
MAX_EXTERNAL_MEMORIES_PER_ACTOR = 2


@dataclass(frozen=True)
class MentionedActor:
    character: Character
    matched_aliases: list[str] = field(default_factory=list)
    on_stage: bool = False


@dataclass(frozen=True)
class OffstageRecallContext:
    actors: list[MentionedActor]
    content: str


def _normalize(value: str | None) -> str:
    return re.sub(r"\s+", "", (value or "").strip().lower())


def _character_aliases(character: Character) -> list[str]:
    aliases: list[str] = []
    raw_name = (character.name or "").strip()
    if raw_name:
        aliases.append(raw_name)
        aliases.append(raw_name.replace(" ", ""))
        parts = [part for part in re.split(r"[\s·/|,()\[\]{}]+", raw_name) if part]
        aliases.extend(parts)
        compact = raw_name.replace(" ", "")
        # Korean full names often get referenced by given name only: 김민지 -> 민지.
        if len(compact) >= 3 and re.fullmatch(r"[가-힣]+", compact):
            aliases.append(compact[1:])
    seen: set[str] = set()
    result: list[str] = []
    for alias in aliases:
        normalized = _normalize(alias)
        if len(normalized) < 2 or normalized in seen:
            continue
        seen.add(normalized)
        result.append(alias)
    return result


def resolve_mentioned_actors(
    session: Session,
    text: str,
    *,
    active_character_ids: set[str] | None = None,
    limit: int = MAX_OFFSTAGE_ACTORS,
) -> list[MentionedActor]:
    """Resolve names/aliases against the global character table, not just room participants."""
    active_character_ids = active_character_ids or set()
    normalized_text = _normalize(text)
    if not normalized_text:
        return []
    candidates: list[tuple[int, MentionedActor]] = []
    for character in character_service.list_characters(session):
        matched: list[str] = []
        score = 0
        for alias in _character_aliases(character):
            normalized_alias = _normalize(alias)
            if normalized_alias and normalized_alias in normalized_text:
                matched.append(alias)
                score = max(score, len(normalized_alias))
        if matched:
            candidates.append((score, MentionedActor(
                character=character,
                matched_aliases=matched,
                on_stage=character.id in active_character_ids,
            )))
    candidates.sort(key=lambda item: (item[1].on_stage, item[0], item[1].character.name), reverse=True)
    selected: list[MentionedActor] = []
    seen: set[str] = set()
    for _, actor in candidates:
        if actor.character.id in seen:
            continue
        seen.add(actor.character.id)
        selected.append(actor)
        if len(selected) >= limit:
            break
    return selected


def _format_local_actor_memory(character: Character, memories) -> list[str]:
    lines: list[str] = []
    for memory in memories[:MAX_LOCAL_MEMORIES_PER_ACTOR]:
        content = conversation_service.compact_text(memory.content, 130)
        if content:
            lines.append(f"- {character.name}({character.id}) local {memory.memory_type}/{memory.importance}: {content}")
    return lines


def build_offstage_actor_recall_context(
    session: Session,
    conversation: Conversation | None,
    *,
    text: str,
    active_character_ids: set[str],
    genre_mode: str,
    query: str,
) -> OffstageRecallContext:
    if not conversation:
        return OffstageRecallContext(actors=[], content="")
    mentioned = resolve_mentioned_actors(session, text, active_character_ids=active_character_ids)
    offstage = [actor for actor in mentioned if not actor.on_stage][:MAX_OFFSTAGE_ACTORS]
    if not offstage:
        return OffstageRecallContext(actors=[], content="")

    lines = [
        "[Relevant off-stage actor recall]",
        "These actors are mentioned or relevant league/domain actors, but are not current speaking/on-stage participants. Use as lightweight recall only; do not make them speak unless invited on-stage.",
    ]
    for actor in offstage:
        character = actor.character
        aliases = ", ".join(actor.matched_aliases[:3])
        lines.append(f"Actor: {character.name}({character.id})" + (f" | matched={aliases}" if aliases else ""))
        local_memories = conversation_service.list_character_memories(
            session,
            conversation.id,
            character.id,
            limit=MAX_LOCAL_MEMORIES_PER_ACTOR,
        )
        memory_lines = _format_local_actor_memory(character, local_memories)
        if memory_lines:
            lines.extend(memory_lines)
        else:
            lines.append(f"- {character.name} is a valid off-stage domain actor; no durable recall snippet was found.")
    return OffstageRecallContext(actors=offstage, content="\n".join(lines))
