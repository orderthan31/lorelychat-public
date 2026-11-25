from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from sqlmodel import Session
    from app.db.models import Conversation

DEFAULT_GENRE_MODE = "battle"
VALID_GENRE_MODES = {"battle", "romance", "fantasy", "slice_of_life", "mystery", "custom"}


def normalize_genre_mode(value: str | None) -> str:
    """Normalize authored genre values without importing persistence or engines."""
    cleaned = (value or DEFAULT_GENRE_MODE).strip().lower().replace("-", "_")
    return cleaned if cleaned in VALID_GENRE_MODES else DEFAULT_GENRE_MODE


@dataclass(frozen=True)
class GenreContextSection:
    key: str
    title: str
    content: str
    included: bool


@dataclass(frozen=True)
class GenreDomainHandler:
    genre_mode: str
    context_key: str
    context_title: str
    format_official_state: Callable[[Session, Conversation | None], str]
    apply_events: Callable[[Session, Conversation | None, dict], list[object]]


def normalize_conversation_genre(value: str | None) -> str | None:
    return None if value is None else normalize_genre_mode(value)


def _apply_battle_payload(session: Session, conversation: Conversation | None, update: dict) -> list[object]:
    from app.services import battle_ledger_service

    events = update.get("battle_events") if isinstance(update.get("battle_events"), list) else []
    return list(battle_ledger_service.apply_battle_events(session, conversation, events))


def _format_battle_state(session: Session, conversation: Conversation | None) -> str:
    from app.services import battle_ledger_service

    return battle_ledger_service.format_official_battle_state(session, conversation)


HANDLERS: dict[str, GenreDomainHandler] = {
    "battle": GenreDomainHandler(
        genre_mode="battle",
        context_key="official_battle_state",
        context_title="Official battle state",
        format_official_state=_format_battle_state,
        apply_events=_apply_battle_payload,
    ),
}


def get_handler(conversation: Conversation | None) -> GenreDomainHandler | None:
    return HANDLERS.get(normalize_conversation_genre(getattr(conversation, "genre_mode", None)))


def apply_genre_domain_update(session: Session, conversation: Conversation | None, update: dict) -> list[object]:
    handler = get_handler(conversation)
    if not handler:
        return []
    return handler.apply_events(session, conversation, update)


def get_official_context(session: Session, conversation: Conversation | None) -> str:
    handler = get_handler(conversation)
    if not handler:
        return ""
    return handler.format_official_state(session, conversation)


def get_context_sections(session: Session, conversation: Conversation | None) -> list[GenreContextSection]:
    sections: list[GenreContextSection] = []
    active_handler = get_handler(conversation)
    for genre_mode, handler in HANDLERS.items():
        content = handler.format_official_state(session, conversation) if active_handler and active_handler.genre_mode == genre_mode else ""
        sections.append(GenreContextSection(
            key=handler.context_key,
            title=handler.context_title,
            content=content,
            included=bool(content),
        ))
    return sections
