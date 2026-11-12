"""Actual cached WebP display; remote sources only enter explicit POST bodies."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Request
from starlette.responses import Response

from app.core.config import get_settings
from app.ops.image_validation import ImageDecoderBusy, ImageDecoderUnavailable

router = APIRouter(prefix='/images', tags=['images'])
MAX_PREPARE_BODY = 10 * 1024 * 1024


def _roots():
    settings = get_settings()
    return Path(settings.upload_root).expanduser().resolve(), settings.shared_upload_root


def _spec(variant: str):
    from app.services.image_derivative_cache import DerivativeSpec
    if variant not in ('thumbnail', 'full'):
        raise HTTPException(status_code=422, detail='Invalid image variant')
    return DerivativeSpec(mime_type='image/webp', max_edge=512 if variant == 'thumbnail' else 2048,
                          quality=80, preserve_animation=True)


def _response(request: Request, image):
    if image.mime_type != 'image/webp':
        raise HTTPException(status_code=404, detail='Image not found')
    etag = '"' + image.sha256 + '"'
    headers = {'ETag': etag, 'Cache-Control': 'private, max-age=0, must-revalidate',
               'X-Content-Type-Options': 'nosniff'}
    tags = request.headers.get('if-none-match', '').split(',')
    if any(tag.strip().removeprefix('W/') in (etag, '*') for tag in tags):
        return Response(status_code=304, headers=headers)
    return Response(content=image.data, media_type='image/webp', headers=headers)


async def _prepare(source: str, variant: str, *, allow_remote: bool):
    from app.services.image_derivative_cache import get_derivative
    root, shared = _roots()
    spec = _spec(variant)
    try:
        return await get_derivative(source, spec, upload_root=root, shared_root=shared,
                                    allow_remote=allow_remote)
    except (ImageDecoderBusy, ImageDecoderUnavailable, TimeoutError):
        raise HTTPException(status_code=503, detail='Image temporarily unavailable',
                            headers={'Retry-After': '1'}) from None
    except (ValueError, OSError):
        raise HTTPException(status_code=422 if allow_remote else 404,
                            detail='Image unavailable or unsupported') from None


@router.get('/render.webp')
async def render_image(request: Request, source: str = Query(min_length=1, max_length=4096),
                       variant: str = Query(default='thumbnail', max_length=16)):
    # A GET can never become an arbitrary remote fetch or expose query secrets.
    if not source.startswith('/uploads/'):
        raise HTTPException(status_code=422, detail='A local image path is required')
    return _response(request, await _prepare(source, variant, allow_remote=False))


@router.post('/prepare')
async def prepare_image(request: Request):
    # Bound the body before JSON parsing; URLs/data are not placed in access logs.
    body = bytearray()
    async for chunk in request.stream():
        if len(chunk) > MAX_PREPARE_BODY - len(body):
            raise HTTPException(status_code=413, detail='Image request exceeds input budget')
        body.extend(chunk)
    try:
        def unique(pairs):
            value = {}
            for key, item in pairs:
                if key in value:
                    raise ValueError('Duplicate field')
                value[key] = item
            return value
        payload = json.loads(body, object_pairs_hook=unique)
        if not isinstance(payload, dict) or set(payload) - {'source', 'variant'}:
            raise ValueError('Invalid fields')
        source = payload['source']
        variant = payload.get('variant', 'thumbnail')
        if not isinstance(source, str) or not source or not isinstance(variant, str):
            raise ValueError('Invalid input')
        if not source.startswith('data:') and len(source) > 4096:
            raise ValueError('URL too long')
    except (ValueError, KeyError, TypeError, RecursionError):
        raise HTTPException(status_code=422, detail='Invalid image request') from None
    image = await _prepare(source, variant, allow_remote=True)
    return {'image_url': '/images/cache/' + image.source_key + '/' + image.filename}


@router.get('/cache/{source_key}/{filename}')
async def cached_image(request: Request, source_key: str, filename: str):
    from app.services.image_derivative_cache import read_cached
    root, _ = _roots()
    try:
        image = await asyncio.to_thread(read_cached, upload_root=root, source_key=source_key, filename=filename)
    except (ValueError, OSError):
        raise HTTPException(status_code=404, detail='Image not found') from None
    return _response(request, image)
