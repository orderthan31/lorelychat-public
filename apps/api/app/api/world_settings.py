from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session
from app.db.models import WorldSetting
from app.ops.catalog_queries import catalog_page

from app.db.session import get_session
from app.schemas.world_settings import WorldSettingCreate, WorldSettingRead, WorldSettingUpdate
from app.schemas.pagination import WorldSettingPageRead
from app.services import world_setting_service

router = APIRouter(prefix="/world-settings", tags=["world-settings"])


@router.get("", response_model=list[WorldSettingRead] | WorldSettingPageRead)
def list_world_settings(
    include_disabled: bool = False,
    paginated: bool = Query(False),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    q: str | None = Query(None, max_length=512),
    session: Session = Depends(get_session),
):
    if paginated:
        result = catalog_page(session, WorldSetting, fields=[WorldSetting.title, WorldSetting.description, WorldSetting.world_seed, WorldSetting.opening_scene, WorldSetting.compression_focus], order=[WorldSetting.updated_at.desc(), WorldSetting.created_at.desc(), WorldSetting.id], page=page, page_size=page_size, q=q, predicates=() if include_disabled else (WorldSetting.enabled == True,))
        result["items"] = [WorldSettingRead.model_validate(item.model_dump()) for item in result["items"]]
        return WorldSettingPageRead(**result)
    return world_setting_service.list_world_settings(session, include_disabled=include_disabled)


@router.post("", response_model=WorldSettingRead)
def create_world_setting(payload: WorldSettingCreate, session: Session = Depends(get_session)):
    return world_setting_service.create_world_setting(session, payload)


@router.get("/{world_id}", response_model=WorldSettingRead)
def get_world_setting(world_id: str, session: Session = Depends(get_session)):
    world = world_setting_service.get_world_setting(session, world_id)
    if not world:
        raise HTTPException(status_code=404, detail="World setting not found")
    return world


@router.patch("/{world_id}", response_model=WorldSettingRead)
def update_world_setting(world_id: str, payload: WorldSettingUpdate, session: Session = Depends(get_session)):
    world = world_setting_service.get_world_setting(session, world_id)
    if not world:
        raise HTTPException(status_code=404, detail="World setting not found")
    return world_setting_service.update_world_setting(session, world, payload)


@router.delete("/{world_id}", status_code=204)
def delete_world_setting(world_id: str, session: Session = Depends(get_session)):
    world = world_setting_service.get_world_setting(session, world_id)
    if not world:
        raise HTTPException(status_code=404, detail="World setting not found")
    world_setting_service.delete_world_setting(session, world)
