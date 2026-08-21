from datetime import datetime, timezone
from uuid import uuid4
from sqlalchemy import text
from sqlmodel import Session, select

from app.db.models import WorldSetting
from app.schemas.world_settings import WorldSettingCreate, WorldSettingUpdate
from app.services.conversation_service import normalize_genre_mode


def _clean_text(value: str | None) -> str | None:
    text = (value or '').strip()
    return text or None


def _apply_payload(world: WorldSetting, data: dict) -> WorldSetting:
    for key, value in data.items():
        if key == 'genre_mode' and value is not None:
            setattr(world, key, normalize_genre_mode(value))
        elif key == 'tags' and value is not None:
            setattr(world, key, [str(item).strip() for item in value if str(item).strip()])
        elif isinstance(value, str) or value is None:
            setattr(world, key, _clean_text(value))
        else:
            setattr(world, key, value)
    world.updated_at = datetime.now(timezone.utc)
    return world


def list_world_settings(session: Session, *, include_disabled: bool = False) -> list[WorldSetting]:
    stmt = select(WorldSetting)
    if not include_disabled:
        stmt = stmt.where(WorldSetting.enabled == True)  # noqa: E712
    stmt = stmt.order_by(text("updated_at DESC"), text("created_at DESC"))
    return list(session.exec(stmt).all())


def get_world_setting(session: Session, world_id: str) -> WorldSetting | None:
    return session.get(WorldSetting, world_id)


def create_world_setting(session: Session, payload: WorldSettingCreate) -> WorldSetting:
    world = WorldSetting(id=f"world_{uuid4().hex[:12]}", title=payload.title.strip(), genre_mode=normalize_genre_mode(payload.genre_mode))
    _apply_payload(world, payload.model_dump())
    session.add(world)
    session.commit()
    session.refresh(world)
    return world


def update_world_setting(session: Session, world: WorldSetting, payload: WorldSettingUpdate) -> WorldSetting:
    _apply_payload(world, payload.model_dump(exclude_unset=True))
    session.add(world)
    session.commit()
    session.refresh(world)
    return world


def delete_world_setting(session: Session, world: WorldSetting) -> None:
    session.delete(world)
    session.commit()
