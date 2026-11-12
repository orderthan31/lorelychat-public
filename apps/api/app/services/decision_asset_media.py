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
    from app.services.image_derivative_cache import DerivativeSpec, get_derivative
    from app.services.decision_transport import DecisionImage
    images = []
    total = 0
    root = Path(get_settings().upload_root).expanduser().resolve()
    minimum = 280 if route.model.startswith(('typesafe/jev', '~typesafe/jev')) else 0
    spec = DerivativeSpec(mime_type=target, max_edge=512, min_edge=minimum,
                          quality=80, preserve_animation=False)
    try:
        async with asyncio.timeout(PREPARE_TIMEOUT):
            for candidate in candidates:
                # The shared cache owns the secure read and native processing.
                # Decision must never import a remote URL or a shared root.
                image = await get_derivative(candidate.get('thumbnail_url') or candidate['image_url'],
                                             spec, upload_root=root, allow_remote=False)
                converted, width, height = image.data, image.width, image.height
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
