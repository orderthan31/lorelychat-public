"""Owned asset ingestion. Native codecs never run in the application process."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
from urllib.parse import urlsplit, unquote
from uuid import uuid4

from app.ops.image_validation import _DECODE_SLOTS, _admit, ImageDecoderBusy, ImageDecoderUnavailable, MAX_ENCODED_BYTES
from app.ops.asset_image_download import download_image

TRANSFORM_TIMEOUT_SECONDS = 8.0
_WORKER = Path(__file__).parents[1] / 'ops' / 'asset_image_transform_worker.py'


async def transform_image(data: bytes, source_mime: str, *, target_mime: str = 'image/webp', max_edge: int = 2048, quality: int = 80, preserve_animation: bool = False, min_edge: int = 0) -> tuple[bytes, int, int]:
    _admit(data, source_mime)
    if target_mime not in {'image/webp', 'image/jpeg', 'image/png'} or not 1 <= max_edge <= 2048 or not 1 <= quality <= 100:
        raise ValueError('Invalid image transform options')
    if not 0 <= min_edge <= max_edge or (min_edge and (target_mime == 'image/webp' or preserve_animation)):
        raise ValueError('Invalid image padding options')
    if not _DECODE_SLOTS.acquire(blocking=False):
        raise ImageDecoderBusy('Image decoder busy')
    process = None
    spawn = asyncio.create_task(asyncio.create_subprocess_exec(
        sys.executable, '-I', '-B', str(_WORKER), source_mime, target_mime, str(max_edge), str(quality), '1' if preserve_animation else '0', str(min_edge),
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL, env={}))
    try:
        async with asyncio.timeout(TRANSFORM_TIMEOUT_SECONDS):
            process = await asyncio.shield(spawn)
            output, _ = await process.communicate(data)
            if process.returncode != 0 or len(output) > MAX_ENCODED_BYTES + 128:
                raise ValueError('Invalid image or image cannot be safely normalized')
            header, result = output.split(b'\n', 1)
            dimensions = json.loads(header)
            return result, dimensions['width'], dimensions['height']
    except TimeoutError:
        raise ValueError('Image transform budget exceeded') from None
    except OSError:
        raise ImageDecoderUnavailable('Image decoder unavailable') from None
    finally:
        async def cleanup():
            try:
                child = process if process is not None else await spawn
                if child.returncode is None:
                    try:
                        child.kill()
                    except ProcessLookupError:
                        pass
                await child.wait()
            except OSError:
                pass
            finally:
                _DECODE_SLOTS.release()
        task = asyncio.create_task(cleanup())
        # Keep the slot until cleanup is really complete, including repeated cancellation.
        cancelled = False
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                cancelled = True
        task.result()
        if cancelled:
            raise asyncio.CancelledError()


def _directory(root: Path, parts: list[str], *, create=False) -> int:
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in parts:
            if part in {'', '.', '..'} or '/' in part or '\\' in part:
                raise ValueError('Invalid local image path')
            if create:
                try:
                    os.mkdir(part, mode=0o700, dir_fd=fd)
                except FileExistsError:
                    pass
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        return fd
    except BaseException:
        os.close(fd)
        raise


def read_local_image(url: str, upload_root: Path, shared_root: str | None = None) -> bytes:
    parsed = urlsplit(url)
    path = unquote(parsed.path)
    if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment or not path.startswith('/uploads/') or '\\' in path or '\x00' in path:
        raise ValueError('Invalid local image path')
    parts = path[len('/uploads/'):].split('/')
    if any(part in {'', '.', '..'} for part in parts):
        raise ValueError('Invalid local image path')
    for root in [upload_root] + ([Path(shared_root).expanduser().resolve()] if shared_root else []):
        directory = None
        try:
            directory = _directory(root, parts[:-1])
            fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
            with os.fdopen(fd, 'rb') as handle:
                info = os.fstat(handle.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_ENCODED_BYTES:
                    raise ValueError('Local image exceeds safety budget')
                data = handle.read(MAX_ENCODED_BYTES + 1)
                if not data or len(data) > MAX_ENCODED_BYTES:
                    raise ValueError('Local image exceeds safety budget')
                return data
        except FileNotFoundError:
            continue
        except OSError:
            raise ValueError('Local image is unavailable or unsafe') from None
        finally:
            if directory is not None:
                os.close(directory)
    raise ValueError('Local image does not exist')


def detect_mime(data: bytes) -> str:
    if data.startswith(b'\x89PNG\r\n\x1a\n'): return 'image/png'
    if data.startswith(b'\xff\xd8'): return 'image/jpeg'
    if data.startswith((b'GIF87a', b'GIF89a')): return 'image/gif'
    if data[:4] == b'RIFF' and data[8:12] == b'WEBP': return 'image/webp'
    raise ValueError('Unsupported image type')


@dataclass
class OwnedImage:
    image_url: str
    thumbnail_url: str
    metadata: dict
    source: str
    root: Path
    names: tuple[str, str]

    def cleanup(self):
        fd = _directory(self.root, ['character-assets'])
        try:
            for name in self.names:
                try:
                    os.unlink(name, dir_fd=fd)
                except FileNotFoundError:
                    pass
        finally:
            os.close(fd)


async def ingest_image(*, upload_root: Path, shared_root: str | None = None, data: bytes | None = None, source_mime: str | None = None, image_url: str | None = None) -> OwnedImage:
    source = 'upload' if data is not None else 'url_import'
    if data is None:
        if image_url and image_url.startswith('/uploads/'):
            data = read_local_image(image_url, upload_root, shared_root)
            source_mime = detect_mime(data)
        elif image_url:
            data, source_mime = await download_image(image_url)
            if not source_mime or source_mime == 'application/octet-stream':
                source_mime = detect_mime(data)
        else:
            raise ValueError('Image URL is required')
    canonical, width, height = await transform_image(data, source_mime or detect_mime(data), preserve_animation=True)
    thumbnail, tw, th = await transform_image(canonical, 'image/webp', max_edge=512, preserve_animation=False)
    root = Path(upload_root).expanduser().resolve()
    if shared_root and root.is_relative_to(Path(shared_root).expanduser().resolve()):
        raise ValueError('Private image storage is unavailable')
    root.mkdir(parents=True, exist_ok=True)
    fd = _directory(root, ['character-assets'], create=True)
    token = uuid4().hex
    names = (f'{token}.webp', f'{token}_thumbnail.webp')
    created = []
    try:
        for name, body in zip(names, (canonical, thumbnail)):
            output = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
            created.append(name)
            with os.fdopen(output, 'wb') as handle:
                handle.write(body)
                handle.flush()
                os.fsync(handle.fileno())
    except BaseException:
        for name in created:
            os.unlink(name, dir_fd=fd)
        raise
    finally:
        os.close(fd)
    def metadata(body, w, h):
        return {'mime': 'image/webp', 'width': w, 'height': h, 'size': len(body), 'sha256': hashlib.sha256(body).hexdigest(), 'quality': 80}
    return OwnedImage('/uploads/character-assets/' + names[0], '/uploads/character-assets/' + names[1],
                      {'normalization': {'version': 1, 'owned': True, **metadata(canonical, width, height)},
                       'thumbnail': metadata(thumbnail, tw, th)}, source, root, names)
