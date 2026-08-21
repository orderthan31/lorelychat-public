from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session
from app.db.session import get_session
from app.schemas.presets import PresetCreate, PresetRead, PresetUpdate
from app.services import preset_service

router = APIRouter(prefix="/presets", tags=["presets"])


@router.get("", response_model=list[PresetRead])
def list_presets(preset_type: str | None = None, include_disabled: bool = False, session: Session = Depends(get_session)):
    return preset_service.list_presets(session, preset_type=preset_type, include_disabled=include_disabled)


@router.post("", response_model=PresetRead)
def create_preset(payload: PresetCreate, session: Session = Depends(get_session)):
    return preset_service.create_preset(session, payload)


@router.get("/{preset_id}", response_model=PresetRead)
def get_preset(preset_id: str, session: Session = Depends(get_session)):
    preset = preset_service.get_preset(session, preset_id)
    if not preset:
        raise HTTPException(status_code=404, detail="Preset not found")
    return preset


@router.patch("/{preset_id}", response_model=PresetRead)
def update_preset(preset_id: str, payload: PresetUpdate, session: Session = Depends(get_session)):
    preset = preset_service.get_preset(session, preset_id)
    if not preset:
        raise HTTPException(status_code=404, detail="Preset not found")
    return preset_service.update_preset(session, preset, payload)


@router.delete("/{preset_id}", status_code=204)
def delete_preset(preset_id: str, session: Session = Depends(get_session)):
    preset = preset_service.get_preset(session, preset_id)
    if not preset:
        raise HTTPException(status_code=404, detail="Preset not found")
    preset_service.delete_preset(session, preset)
