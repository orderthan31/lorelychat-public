from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session

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
    q: str | None = Query(None),
    session: Session = Depends(get_session),
):
    if paginated:
        all_items = world_setting_service.list_world_settings(session, include_disabled=include_disabled)
        search = (q or "").strip()
        if search:
            lowered = search.lower()
            all_items = [
                world for world in all_items
                if lowered in f"{world.title or ''} {world.description or ''} {world.world_seed or ''} {world.opening_scene or ''} {world.compression_focus or ''}".lower()
            ]
        total = len(all_items)
        pages = max(1, (total + page_size - 1) // page_size)
        safe_page = min(page, pages)
        start = (safe_page - 1) * page_size
        return WorldSettingPageRead(items=[WorldSettingRead.model_validate(item.model_dump()) for item in all_items[start:start + page_size]], total=total, page=safe_page, page_size=page_size, pages=pages)
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
