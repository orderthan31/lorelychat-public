from uuid import uuid4
from datetime import datetime, timezone
from sqlmodel import Session, select
from app.db.models import Preset
from app.schemas.presets import PresetCreate, PresetUpdate


def list_presets(session: Session, preset_type: str | None = None, include_disabled: bool = False) -> list[Preset]:
    stmt = select(Preset)
    if preset_type:
        stmt = stmt.where(Preset.preset_type == preset_type)
    if not include_disabled:
        stmt = stmt.where(Preset.enabled == True)  # noqa: E712
    stmt = stmt.order_by(Preset.preset_type, Preset.title)
    return list(session.exec(stmt).all())


def create_preset(session: Session, payload: PresetCreate) -> Preset:
    preset = Preset(id=f"pre_{uuid4().hex[:12]}", **payload.model_dump())
    session.add(preset)
    session.commit()
    session.refresh(preset)
    return preset


def get_preset(session: Session, preset_id: str) -> Preset | None:
    return session.get(Preset, preset_id)


def update_preset(session: Session, preset: Preset, payload: PresetUpdate) -> Preset:
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(preset, key, value)
    preset.updated_at = datetime.now(timezone.utc)
    session.add(preset)
    session.commit()
    session.refresh(preset)
    return preset


def delete_preset(session: Session, preset: Preset) -> None:
    session.delete(preset)
    session.commit()
