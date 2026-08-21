from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from app.core.config import get_settings

UPLOAD_ROOT = Path(get_settings().upload_root).expanduser().resolve()
AVATAR_DIR = UPLOAD_ROOT / "avatars"
CONVERSATION_THUMBNAIL_DIR = UPLOAD_ROOT / "conversation-thumbnails"
WORLD_THUMBNAIL_DIR = UPLOAD_ROOT / "world-thumbnails"
MAX_AVATAR_BYTES = 5 * 1024 * 1024
ALLOWED_AVATAR_TYPES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
}

router = APIRouter(prefix="/uploads", tags=["uploads"])


async def save_image_upload(request: Request, file: UploadFile, target_dir: Path, prefix: str, response_key: str) -> dict:
    extension = ALLOWED_AVATAR_TYPES.get(file.content_type or "")
    if not extension:
        raise HTTPException(status_code=415, detail="Only jpeg, png, webp, and gif images are allowed")

    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Image file is empty")
    if len(data) > MAX_AVATAR_BYTES:
        raise HTTPException(status_code=413, detail="Image file must be 5MB or smaller")

    target_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{prefix}_{uuid4().hex[:16]}{extension}"
    path = target_dir / filename
    path.write_bytes(data)

    upload_path = f"{target_dir.name}/{filename}"
    url = request.url_for("uploads", path=upload_path)
    return {response_key: str(url), "filename": filename, "content_type": file.content_type, "size": len(data)}


@router.post("/avatars")
async def upload_avatar(request: Request, file: UploadFile = File(...)) -> dict:
    return await save_image_upload(request, file, AVATAR_DIR, "avatar", "avatar_url")


@router.post("/conversation-thumbnails")
async def upload_conversation_thumbnail(request: Request, file: UploadFile = File(...)) -> dict:
    return await save_image_upload(request, file, CONVERSATION_THUMBNAIL_DIR, "conversation_thumbnail", "thumbnail_url")


@router.post("/world-thumbnails")
async def upload_world_thumbnail(request: Request, file: UploadFile = File(...)) -> dict:
    return await save_image_upload(request, file, WORLD_THUMBNAIL_DIR, "world_thumbnail", "thumbnail_url")
