"""Read-only upload serving with an optional, lower-priority shared root."""

import mimetypes
import os
from pathlib import Path

from starlette.exceptions import HTTPException
from starlette.responses import Response
from starlette.staticfiles import StaticFiles
from starlette.types import Scope


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
