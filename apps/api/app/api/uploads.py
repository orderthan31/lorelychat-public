from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from app.core.config import get_settings
from app.core.upload_files import read_verified_image, ensure_private_upload_directory

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
    data, extension = await read_verified_image(file, MAX_AVATAR_BYTES)

    target_dir = ensure_private_upload_directory(target_dir, UPLOAD_ROOT, get_settings().shared_upload_root)
    filename = f"{prefix}_{uuid4().hex[:16]}{extension}"
    path = target_dir / filename
    upload_path = f"{target_dir.name}/{filename}"
    url = request.url_for("uploads", path=upload_path)
    created = False
    try:
        with path.open('xb') as handle:
            created = True
            handle.write(data)
    except BaseException:
        if created:
            path.unlink(missing_ok=True)
        raise
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
