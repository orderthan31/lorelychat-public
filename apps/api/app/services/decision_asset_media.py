"""Bounded owned-image derivatives for Decision calls; never fetch a URL here."""
from __future__ import annotations

import asyncio
from pathlib import Path

from app.core.config import get_settings

MAX_IMAGES = 8
MAX_SOURCE_BYTES = 8 * 1024 * 1024
MAX_IMAGE_BYTES = 256 * 1024
MAX_TOTAL_BYTES = 1024 * 1024
PREPARE_TIMEOUT = 3.0


def _read_owned(url: str) -> tuple[bytes, str]:
    from app.services.asset_image_service import read_local_image, detect_mime
    root = Path(get_settings().upload_root).expanduser().resolve()
    # Directory-fd traversal and O_NOFOLLOW close the local symlink race as well
    # as refusing remote URLs and arbitrary files. Shared roots are not inputs.
    data = read_local_image(url, root)
    return data, detect_mime(data)


async def prepare_images(route, candidates: list[dict]):
    """All candidates or text-only: no unshown/remote image is implied to exist.

    Input is detached identifiers/owned paths, not ORM objects. Decode/resize is
    isolated by the shared native worker. An unsupported route never reads files.
    """
    if 'image' not in route.input_modalities or not candidates or len(candidates) > MAX_IMAGES:
        return []
    target = next((mime for mime in ('image/webp', 'image/jpeg', 'image/png')
                   if mime in route.image_mime_types), None)
    if target is None:
        return []
    from app.services.asset_image_service import transform_image
    from app.services.decision_transport import DecisionImage
    images = []
    total = 0
    try:
        async with asyncio.timeout(PREPARE_TIMEOUT):
            for candidate in candidates:
                data, mime = await asyncio.to_thread(_read_owned, candidate.get('thumbnail_url') or candidate['image_url'])
                minimum = 280 if route.model.startswith(('typesafe/jev', '~typesafe/jev')) else 0
                converted, width, height = await transform_image(
                    data, mime, target_mime=target, max_edge=512, min_edge=minimum,
                    quality=80, preserve_animation=False,
                )
                if not converted or len(converted) > MAX_IMAGE_BYTES or max(width, height) > 512:
                    return []
                total += len(converted)
                if total > MAX_TOTAL_BYTES:
                    return []
                images.append(DecisionImage(id=candidate['id'], data=converted, mime_type=target))
    except Exception:
        # Invalid legacy/remote/missing bytes degrade to metadata-only selection;
        # raw paths, URLs and decoder/provider errors never become diagnostics.
        return []
    return images
