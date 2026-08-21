from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from sqlmodel import Session

from app.db.models import Conversation
from app.services import battle_ledger_service

DEFAULT_GENRE_MODE = "battle"
VALID_GENRE_MODES = {"battle", "romance", "fantasy", "slice_of_life", "mystery", "custom"}


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


def _normalize_genre(conversation: Conversation | None) -> str:
    raw = (getattr(conversation, "genre_mode", None) or DEFAULT_GENRE_MODE) if conversation else DEFAULT_GENRE_MODE
    normalized = str(raw).strip().lower()
    return normalized if normalized in VALID_GENRE_MODES else DEFAULT_GENRE_MODE


def _apply_battle_payload(session: Session, conversation: Conversation | None, update: dict) -> list[object]:
    events = update.get("battle_events") if isinstance(update.get("battle_events"), list) else []
    return list(battle_ledger_service.apply_battle_events(session, conversation, events))


HANDLERS: dict[str, GenreDomainHandler] = {
    "battle": GenreDomainHandler(
        genre_mode="battle",
        context_key="official_battle_state",
        context_title="Official battle state",
        format_official_state=battle_ledger_service.format_official_battle_state,
        apply_events=_apply_battle_payload,
    ),
}


def get_handler(conversation: Conversation | None) -> GenreDomainHandler | None:
    return HANDLERS.get(_normalize_genre(conversation))


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
