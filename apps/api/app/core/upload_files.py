"""Read-only upload serving with an optional, lower-priority shared root."""

import mimetypes
import os
import struct
import zlib
from pathlib import Path

from starlette.exceptions import HTTPException
from starlette.responses import Response
from starlette.staticfiles import StaticFiles
from starlette.types import Scope


async def read_verified_image(file, max_bytes: int) -> tuple[bytes, str]:
    """Never request an unbounded read; reject at limit+1 before publication."""
    from fastapi import HTTPException as APIError
    from app.ops.image_validation import verify_image_async, ImageDecoderBusy, ImageDecoderUnavailable
    allowed = {'image/png', 'image/jpeg', 'image/gif', 'image/webp'}
    if file.content_type not in allowed:
        raise APIError(status_code=415, detail='Unsupported image type')
    data = bytearray()
    while True:
        chunk = await file.read(min(65536, max_bytes + 1 - len(data)))
        if not chunk:
            break
        data.extend(chunk)
        if len(data) > max_bytes:
            raise APIError(status_code=413, detail='Image exceeds upload byte limit')
    if not data:
        raise APIError(status_code=400, detail='Image file is empty')
    payload = bytes(data)
    try:
        extension = await verify_image_async(payload, file.content_type)
    except ImageDecoderBusy:
        raise APIError(status_code=503, detail="Image decoder busy", headers={"Retry-After": "1"}) from None
    except ImageDecoderUnavailable:
        raise APIError(status_code=503, detail="Image decoder unavailable", headers={"Retry-After": "1"}) from None
    except (ValueError, IndexError, OverflowError, zlib.error, struct.error):
        raise APIError(status_code=422, detail='Invalid image or image exceeds safety budget') from None
    return payload, extension


def ensure_private_upload_directory(target_dir, upload_root, shared_upload_root=None):
    """Refuse accidental publication through a shared/outside-root symlink."""
    from fastapi import HTTPException as APIError
    private = Path(upload_root).expanduser().resolve()
    target = Path(target_dir).expanduser().resolve()
    shared = Path(shared_upload_root).expanduser().resolve() if shared_upload_root else None
    if not target.is_relative_to(private) or (shared and (target.is_relative_to(shared) or private == shared)):
        raise APIError(status_code=500, detail='Private upload storage is unavailable')
    target.mkdir(parents=True, exist_ok=True)
    return target


class UploadStaticFiles(StaticFiles):
    """Use Starlette's ordered lookup and containment checks, never write files.

    The shared root is only a lookup location, not an upload destination. No
    directories or links are created here; a missing shared root is harmless.
    """

    def __init__(
        self,
        upload_root: str | os.PathLike[str],
        shared_upload_root: str | os.PathLike[str] | None = None,
    ) -> None:
        super().__init__(
            directory=Path(upload_root).expanduser().resolve(),
            html=False,
            follow_symlink=False,
        )
        if shared_upload_root:
            self.all_directories.append(Path(shared_upload_root).expanduser().resolve())

    def file_response(
        self,
        full_path: str | os.PathLike[str],
        stat_result: os.stat_result,
        scope: Scope,
        status_code: int = 200,
    ) -> Response:
        # html=False disables index/error pages, but not direct HTML requests.
        # Uploads must not become an arbitrary same-origin HTML hosting surface.
        media_type, _ = mimetypes.guess_type(str(full_path))
        if media_type in {"text/html", "application/xhtml+xml"}:
            raise HTTPException(status_code=404)
        response = super().file_response(full_path, stat_result, scope, status_code)
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response
