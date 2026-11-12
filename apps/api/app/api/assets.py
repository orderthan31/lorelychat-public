import json
from pathlib import Path


from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlmodel import Session
from pydantic import ValidationError
from app.db.models import Character
from app.core.upload_files import read_verified_image
from app.ops.image_validation import ImageDecoderBusy, ImageDecoderUnavailable

from app.core.config import get_settings
from app.db.session import get_session
from app.schemas.assets import CharacterAssetCreate, CharacterAssetRead, CharacterAssetUpdate, clean_tags
from app.services import asset_service

UPLOAD_ROOT = Path(get_settings().upload_root).expanduser().resolve()
ASSET_DIR = UPLOAD_ROOT / "character-assets"
MAX_ASSET_BYTES = 8 * 1024 * 1024
ALLOWED_TYPES = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "image/gif": ".gif"}

router = APIRouter(tags=["character-assets"])


def parse_tags(value: str | None) -> list[str]:
    if not value:
        return []
    if len(value) > 262144:
        raise ValueError("Tags exceed form input budget")
    try:
        data = json.loads(value)
        if isinstance(data, list):
            return clean_tags(data)
    except json.JSONDecodeError:
        pass
    return clean_tags(value)


@router.get("/characters/{character_id}/assets", response_model=list[CharacterAssetRead])
def list_character_assets(character_id: str, include_disabled: bool = True, session: Session = Depends(get_session)):
    return [asset_service.to_asset_read(asset) for asset in asset_service.list_assets(session, character_id, include_disabled=include_disabled)]


@router.post("/characters/{character_id}/assets", response_model=CharacterAssetRead)
async def create_character_asset(character_id: str, payload: CharacterAssetCreate, session: Session = Depends(get_session)):
    try:
        return asset_service.to_asset_read(await asset_service.create_asset_ingested(
            session, character_id, payload, upload_root=UPLOAD_ROOT, shared_root=get_settings().shared_upload_root))
    except (ImageDecoderBusy, ImageDecoderUnavailable):
        raise HTTPException(status_code=503, detail="Image decoder unavailable", headers={"Retry-After": "1"}) from None
    except ValueError as error:
        raise HTTPException(status_code=404 if str(error) == "Character not found" else 422, detail=str(error)) from None


@router.post("/characters/{character_id}/assets/upload", response_model=CharacterAssetRead)
async def upload_character_asset(
    character_id: str,
    file: UploadFile = File(...),
    label: str = Form("이미지 에셋"),
    description: str | None = Form(None),
    tags: str | None = Form(None),
    mood_tags: str | None = Form(None),
    scene_tags: str | None = Form(None),
    outfit_tags: str | None = Form(None),
    pose_tags: str | None = Form(None),
    expression_tags: str | None = Form(None),
    priority: int = Form(50),
    enabled: bool = Form(True),
    is_default: bool = Form(False),
    session: Session = Depends(get_session),
):
    if not session.get(Character, character_id):
        raise HTTPException(status_code=404, detail="Character not found")
    # Validate form metadata before reading bytes or publishing a file.
    try:
        payload = CharacterAssetCreate(
            label=label, description=description, image_url="/uploads/pending",
            tags=parse_tags(tags), mood_tags=parse_tags(mood_tags),
            scene_tags=parse_tags(scene_tags), outfit_tags=parse_tags(outfit_tags),
            pose_tags=parse_tags(pose_tags), expression_tags=parse_tags(expression_tags),
            priority=priority, enabled=enabled, is_default=is_default,
        )
    except (ValidationError, ValueError):
        raise HTTPException(status_code=422, detail="Invalid asset metadata") from None
    data, _ = await read_verified_image(file, MAX_ASSET_BYTES)
    try:
        return asset_service.to_asset_read(await asset_service.create_asset_ingested(
            session, character_id, payload, upload_root=UPLOAD_ROOT, shared_root=get_settings().shared_upload_root,
            data=data, source_mime=file.content_type))
    except (ImageDecoderBusy, ImageDecoderUnavailable):
        raise HTTPException(status_code=503, detail="Image decoder unavailable", headers={"Retry-After": "1"}) from None
    except ValueError as error:
        raise HTTPException(status_code=404 if str(error) == "Character not found" else 422, detail=str(error)) from None


@router.get("/character-assets/{asset_id}", response_model=CharacterAssetRead)
def get_character_asset(asset_id: str, session: Session = Depends(get_session)):
    asset = asset_service.get_asset(session, asset_id)
    if not asset:
        raise HTTPException(status_code=404, detail="Asset not found")
    return asset_service.to_asset_read(asset)


@router.patch("/character-assets/{asset_id}", response_model=CharacterAssetRead)
async def update_character_asset(asset_id: str, payload: CharacterAssetUpdate, session: Session = Depends(get_session)):
    asset = asset_service.get_asset(session, asset_id)
    if not asset:
        raise HTTPException(status_code=404, detail="Asset not found")
    try:
        return asset_service.to_asset_read(await asset_service.update_asset_ingested(
            session, asset, payload, upload_root=UPLOAD_ROOT, shared_root=get_settings().shared_upload_root))
    except (ImageDecoderBusy, ImageDecoderUnavailable):
        raise HTTPException(status_code=503, detail="Image decoder unavailable", headers={"Retry-After": "1"}) from None
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from None


@router.post("/character-assets/{asset_id}/set-default-avatar", response_model=CharacterAssetRead)
def set_default_avatar(asset_id: str, session: Session = Depends(get_session)):
    try:
        return asset_service.to_asset_read(asset_service.set_default_avatar(session, asset_id))
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error))


@router.delete("/character-assets/{asset_id}", status_code=204)
def delete_character_asset(asset_id: str, session: Session = Depends(get_session)):
    asset = asset_service.get_asset(session, asset_id)
    if not asset:
        raise HTTPException(status_code=404, detail="Asset not found")
    asset_service.delete_asset(session, asset)
