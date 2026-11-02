import json
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlmodel import Session, select
from pydantic import ValidationError
from app.db.models import Character, CharacterAsset
from app.core.upload_files import read_verified_image, ensure_private_upload_directory

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
def create_character_asset(character_id: str, payload: CharacterAssetCreate, session: Session = Depends(get_session)):
    try:
        return asset_service.to_asset_read(asset_service.create_asset(session, character_id, payload))
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error))


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
    data, extension = await read_verified_image(file, MAX_ASSET_BYTES)
    directory = ensure_private_upload_directory(ASSET_DIR, UPLOAD_ROOT, get_settings().shared_upload_root)
    filename = f"asset_{uuid4().hex}{extension}"
    path = directory / filename
    payload.image_url = f"/uploads/character-assets/{filename}"
    payload.source = "upload"
    payload.metadata = {"content_type": file.content_type, "size": len(data), "filename": filename}
    created = False
    try:
        # Exclusive UUID path: cleanup owns only this request's newly created file.
        with path.open("xb") as handle:
            created = True
            handle.write(data)
        return asset_service.to_asset_read(asset_service.create_asset(session, character_id, payload))
    except BaseException as error:
        session.rollback()
        # A refresh/serialization exception after commit must not remove durable bytes.
        # If reference verification itself fails, retain the file for a dry-run audit.
        try:
            registered = session.exec(select(CharacterAsset.id).where(CharacterAsset.image_url == payload.image_url).limit(1)).first()
        except Exception:
            registered = True
        if created and not registered:
            path.unlink(missing_ok=True)
        if isinstance(error, ValueError):
            raise HTTPException(status_code=404, detail="Character not found") from None
        raise


@router.get("/character-assets/{asset_id}", response_model=CharacterAssetRead)
def get_character_asset(asset_id: str, session: Session = Depends(get_session)):
    asset = asset_service.get_asset(session, asset_id)
    if not asset:
        raise HTTPException(status_code=404, detail="Asset not found")
    return asset_service.to_asset_read(asset)


@router.patch("/character-assets/{asset_id}", response_model=CharacterAssetRead)
def update_character_asset(asset_id: str, payload: CharacterAssetUpdate, session: Session = Depends(get_session)):
    asset = asset_service.get_asset(session, asset_id)
    if not asset:
        raise HTTPException(status_code=404, detail="Asset not found")
    return asset_service.to_asset_read(asset_service.update_asset(session, asset, payload))


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
