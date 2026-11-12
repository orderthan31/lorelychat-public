"""Private, content-addressed image derivatives; no native codec runs here.

All writers take source flock BEFORE reading input, and retain it through publish.
DELETE must use source_lock, delete the original, then purge_source_locked in that
same context. Source directories and lock anchors are NEVER removed (even empty):
unlinking an anchor splits the lock domain of existing waiters. Capacity is bounded
by refusing new sources/publications, not by unsafe global eviction.

Remote snapshots expire after 30 seconds, are opt-in on EVERY request, and contain
only image bytes and content metadata, never URLs/queries. Remote changes become
visible after TTL. Local input is re-read/hashed on every call. Cache reads verify
content hash, MIME, dimensions, full spec and content-addressed filename without
re-encoding. Corruption is regenerated once by the single locked producer.
"""
from __future__ import annotations

import asyncio
import base64
import binascii
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass, field
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import threading
import time
from urllib.parse import unquote, urlsplit
from uuid import uuid4

from app.services.asset_image_service import (
    _directory, detect_mime, download_image, read_local_image, transform_image,
)
from app.ops.image_validation import ImageDecoderBusy, MAX_ENCODED_BYTES

REMOTE_TTL_SECONDS = 30.0
REQUEST_TIMEOUT_SECONDS = 30.0
LOCK_TIMEOUT_SECONDS = 15.0
DECODER_WAIT_SECONDS = 15.0
# Anchors cannot be evicted while another process may hold an open waiter.
# Leave room for large existing libraries and previews within a finite inode cap.
MAX_SOURCES = 4096
MAX_VARIANTS = 16
MAX_SOURCE_BYTES = 24 * 1024 * 1024
MAX_CACHE_BYTES = 256 * 1024 * 1024
MAX_CACHE_ENTRIES = 8192
MAX_SOURCE_FILES = 128
MAX_METADATA_BYTES = 4096
MAX_REMOTE_URL_CHARS = 8192
MAX_DATA_URL_CHARS = ((MAX_ENCODED_BYTES + 2) // 3) * 4 + 64
_PENDING = threading.BoundedSemaphore(64)
_HELD: ContextVar[tuple] = ContextVar('image_derivative_source_locks', default=())
_KEY = re.compile(r'[0-9a-f]{64}\Z')
_IMAGE = re.compile(r'[0-9a-f]{64}\.(webp|png|jpg)\Z')
_TEMP = re.compile(r'\.tmp-[0-9a-f]{32}\Z')
_EXT = {'image/webp': 'webp', 'image/png': 'png', 'image/jpeg': 'jpg'}
_now = time.time


@dataclass(frozen=True)
class DerivativeSpec:
    mime_type: str = 'image/webp'
    max_edge: int = 512
    min_edge: int = 0
    quality: int = 80
    preserve_animation: bool = False
    version: int = 1


@dataclass(frozen=True)
class CachedDerivative:
    data: bytes = field(repr=False)
    mime_type: str
    width: int
    height: int
    sha256: str
    source_key: str
    filename: str


def _error():
    return ValueError('Image derivative unavailable or unsafe')


def _validate_spec(spec):
    if not isinstance(spec, DerivativeSpec):
        raise _error()
    if (spec.mime_type not in _EXT or type(spec.preserve_animation) is not bool
            or any(type(v) is not int for v in (spec.max_edge, spec.min_edge, spec.quality, spec.version))
            or not 1 <= spec.max_edge <= 2048 or not 0 <= spec.min_edge <= spec.max_edge
            or not 1 <= spec.quality <= 100 or not 1 <= spec.version <= 2**31 - 1
            or (spec.min_edge and (spec.mime_type == 'image/webp' or spec.preserve_animation))
            or (spec.preserve_animation and spec.mime_type != 'image/webp')):
        raise _error()


def _source(source):
    if not isinstance(source, str) or not source or len(source) > MAX_DATA_URL_CHARS:
        raise _error()
    if source.startswith('data:'):
        if not re.match(r'data:image/(png|jpeg|webp|gif);base64,', source):
            raise _error()
        return 'data', source
    if len(source) > MAX_REMOTE_URL_CHARS or any(ord(c) < 32 or ord(c) == 127 for c in source):
        raise _error()
    parsed = urlsplit(source)
    if parsed.scheme in ('http', 'https'):
        if not parsed.hostname or parsed.username or parsed.password or parsed.fragment or '\\' in source:
            raise _error()
        # Validate ports now; preserve query identity (signed URLs are not reordered).
        parsed.port
        return 'remote', source
    path = unquote(parsed.path, errors='strict')
    if (parsed.scheme or parsed.netloc or parsed.query or parsed.fragment
            or not path.startswith('/uploads/') or '\\' in path or '\x00' in path
            or any(ord(c) < 32 or ord(c) == 127 for c in path)):
        raise _error()
    parts = path[len('/uploads/'):].split('/')
    if any(p in ('', '.', '..') for p in parts) or parts[0] == '.image-cache':
        raise _error()
    return 'local', path


def source_key_for(source: str) -> str:
    """Opaque stable identity; equivalent percent-encoded local paths share locks."""
    try:
        kind, normalized = _source(source)
        return hashlib.sha256((kind + '\0' + normalized).encode('utf-8')).hexdigest()
    except (ValueError, TypeError, UnicodeError):
        raise _error() from None


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def _filename(digest, spec):
    return _sha(digest.encode() + b'\0' + _json(asdict(spec))) + '.' + _EXT[spec.mime_type]


def _owner():
    try:
        task = asyncio.current_task()
    except RuntimeError:
        task = None
    return os.getpid(), threading.get_ident(), id(task)


def _identity(fd):
    info = os.fstat(fd)
    return info.st_dev, info.st_ino


def _private(fd):
    info = os.fstat(fd)
    if info.st_uid != os.geteuid() or info.st_mode & 0o022:
        raise _error()


@contextmanager
def _tree(root, *, create=True):
    # Configured root must already exist; never resolve a cache symlink into a write.
    parent = _directory(Path(root), ['.image-cache'], create=create)
    try:
        _private(parent)
        if create:
            try:
                os.mkdir('v1', mode=0o700, dir_fd=parent)
            except FileExistsError:
                pass
        fd = os.open('v1', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
        try:
            _private(fd)
            yield fd
        finally:
            os.close(fd)
    finally:
        os.close(parent)


def _lockfile(directory, name, *, create=True):
    fd = os.open(name, os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK | (os.O_CREAT if create else 0), 0o600, dir_fd=directory)
    try:
        info = os.fstat(fd)
        _private(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size:
            raise _error()
        return fd
    except BaseException:
        os.close(fd)
        raise


def _try_lock(fd):
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except BlockingIOError:
        return False


@contextmanager
def _lock(directory, name, timeout=5.0, *, create=True):
    if not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or not 0 <= timeout <= 30:
        raise _error()
    fd = _lockfile(directory, name, create=create)
    try:
        deadline = time.monotonic() + timeout
        while not _try_lock(fd):
            if time.monotonic() >= deadline:
                raise ValueError('Image derivative busy')
            time.sleep(min(.02, max(0, deadline - time.monotonic())))
        yield fd
    finally:
        os.close(fd)  # close releases flock, including exception/cancellation paths


@asynccontextmanager
async def _alock(directory, name, timeout=LOCK_TIMEOUT_SECONDS):
    fd = _lockfile(directory, name)
    try:
        deadline = time.monotonic() + timeout
        while not _try_lock(fd):
            if time.monotonic() >= deadline:
                raise ValueError('Image derivative busy')
            await asyncio.sleep(.02)
        yield fd
    finally:
        os.close(fd)


def _names(fd, limit):
    names = []
    with os.scandir(fd) as entries:
        for entry in entries:
            if len(names) >= limit:
                raise ValueError('Image derivative capacity exceeded')
            names.append(entry.name)
    return names


def _source_directory(tree, key, *, create):
    if not _KEY.fullmatch(key):
        raise _error()
    if create:
        # Called under the global budget anchor. Empty source dirs count forever.
        names = _names(tree, MAX_SOURCES + 1)
        if key not in names:
            if sum(bool(_KEY.fullmatch(n)) for n in names) >= MAX_SOURCES:
                raise ValueError('Image derivative capacity exceeded')
            os.mkdir(key, mode=0o700, dir_fd=tree)
            os.fsync(tree)
    fd = os.open(key, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=tree)
    try:
        _private(fd)
        return fd
    except BaseException:
        os.close(fd)
        raise


@contextmanager
def source_lock(upload_root: Path, source: str, *, timeout: float = 5.0):
    """Synchronous DELETE fence. Delete input and purge inside this context.

    Do not nest the same source lock. This context is for synchronous DELETE
    services; asynchronous producers use cancellable nonblocking flock polling.
    """
    try:
        key = source_key_for(source)
        with _tree(upload_root) as tree:
            with _lock(tree, '.budget.lock', timeout):
                directory = _source_directory(tree, key, create=True)
            try:
                with _lock(directory, '.lock', timeout) as anchor:
                    token = _HELD.set((*_HELD.get(), (_identity(tree), key, directory, anchor, _owner())))
                    try:
                        yield
                    finally:
                        _HELD.reset(token)
            finally:
                os.close(directory)
    except (OSError, ValueError, TypeError, UnicodeError):
        raise _error() from None


def _owned_file(name):
    return (name in ('snapshot.bin', 'snapshot.json') or bool(_IMAGE.fullmatch(name))
            or (name.endswith('.json') and bool(_IMAGE.fullmatch(name[:-5]))) or bool(_TEMP.fullmatch(name)))


def _unlink(directory, name):
    try:
        os.unlink(name, dir_fd=directory)
    except FileNotFoundError:
        pass


def purge_source_locked(upload_root: Path, source: str):
    """Purge only this source's derived files, with a proven caller-owned lock."""
    try:
        key = source_key_for(source)
        with _tree(upload_root, create=False) as tree:
            held = next((record for record in reversed(_HELD.get())
                         if record[0] == _identity(tree) and record[1] == key and record[4] == _owner()), None)
            if held is None:
                raise _error()
            directory = held[2]
            with _lock(tree, '.budget.lock'):
                for name in _names(directory, MAX_SOURCE_FILES):
                    if _owned_file(name):
                        _unlink(directory, name)
                os.fsync(directory)
    except (OSError, ValueError, TypeError, UnicodeError):
        raise _error() from None


def _read(directory, name, limit):
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    with os.fdopen(fd, 'rb') as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or not 0 < info.st_size <= limit:
            raise _error()
        body = handle.read(limit + 1)
        if len(body) != info.st_size or len(body) > limit:
            raise _error()
        return body


def _dimensions(data, mime):
    """Read encoder dimensions only; never decompress pixels in the API process."""
    if mime == 'image/png' and len(data) >= 33 and data[12:16] == b'IHDR':
        return int.from_bytes(data[16:20], 'big'), int.from_bytes(data[20:24], 'big')
    if mime == 'image/webp' and len(data) >= 30 and int.from_bytes(data[4:8], 'little') + 8 == len(data):
        kind = data[12:16]
        if kind == b'VP8X':
            return 1 + int.from_bytes(data[24:27], 'little'), 1 + int.from_bytes(data[27:30], 'little')
        if kind == b'VP8 ' and data[23:26] == b'\x9d\x01\x2a':
            return int.from_bytes(data[26:28], 'little') & 0x3fff, int.from_bytes(data[28:30], 'little') & 0x3fff
        if kind == b'VP8L' and data[20] == 0x2f:
            bits = int.from_bytes(data[21:25], 'little')
            return (bits & 0x3fff) + 1, ((bits >> 14) & 0x3fff) + 1
    if mime == 'image/jpeg':
        pos = 2
        while pos + 4 <= len(data):
            if data[pos] != 255:
                break
            marker = data[pos + 1]
            if marker == 255:
                pos += 1
                continue
            length = int.from_bytes(data[pos + 2:pos + 4], 'big')
            if length < 2 or pos + 2 + length > len(data):
                break
            if marker in (0xc0, 0xc1, 0xc2) and length >= 8:
                return int.from_bytes(data[pos + 7:pos + 9], 'big'), int.from_bytes(data[pos + 5:pos + 7], 'big')
            if marker == 0xda:
                break
            pos += 2 + length
    raise _error()


def _metadata(directory, name):
    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise _error()
            value[key] = item
        return value
    try:
        return json.loads(_read(directory, name, MAX_METADATA_BYTES), object_pairs_hook=unique)
    except RecursionError:
        raise _error() from None


def _read_entry(directory, key, filename):
    metadata = _metadata(directory, filename + '.json')
    fields = {'source_sha256', 'spec', 'sha256', 'mime_type', 'width', 'height', 'size', 'source_key', 'filename'}
    if not isinstance(metadata, dict) or set(metadata) != fields or not isinstance(metadata['spec'], dict):
        raise _error()
    spec = DerivativeSpec(**metadata['spec'])
    _validate_spec(spec)
    if (set(metadata['spec']) != set(asdict(spec)) or metadata['source_key'] != key or metadata['filename'] != filename
            or not isinstance(metadata['source_sha256'], str) or not _KEY.fullmatch(metadata['source_sha256'])
            or _filename(metadata['source_sha256'], spec) != filename or metadata['mime_type'] != spec.mime_type):
        raise _error()
    data = _read(directory, filename, MAX_ENCODED_BYTES)
    if (type(metadata['size']) is not int or metadata['size'] != len(data) or metadata['sha256'] != _sha(data)
            or detect_mime(data) != spec.mime_type or any(type(metadata[k]) is not int for k in ('width', 'height'))
            or not all(max(1, spec.min_edge) <= metadata[k] <= spec.max_edge for k in ('width', 'height'))
            or _dimensions(data, spec.mime_type) != (metadata['width'], metadata['height'])):
        raise _error()
    return CachedDerivative(data, spec.mime_type, metadata['width'], metadata['height'], metadata['sha256'], key, filename)


def read_cached(upload_root: Path, source_key: str, filename: str) -> CachedDerivative:
    """Read a validated opaque image, never arbitrary files/metadata/lock anchors."""
    try:
        if not isinstance(source_key, str) or not _KEY.fullmatch(source_key) or not isinstance(filename, str) or not _IMAGE.fullmatch(filename):
            raise _error()
        with _tree(upload_root, create=False) as tree:
            directory = _source_directory(tree, source_key, create=False)
            try:
                with _lock(directory, '.lock', create=False):
                    return _read_entry(directory, source_key, filename)
            finally:
                os.close(directory)
    except (OSError, ValueError, TypeError, KeyError, UnicodeError, OverflowError):
        raise _error() from None


def _snapshot(directory):
    try:
        meta = _metadata(directory, 'snapshot.json')
        if (not isinstance(meta, dict) or set(meta) != {'created', 'sha256', 'size', 'mime_type'}
                or type(meta['created']) not in (int, float) or not math.isfinite(meta['created'])
                or not 0 <= _now() - meta['created'] < REMOTE_TTL_SECONDS):
            return None
        data = _read(directory, 'snapshot.bin', MAX_ENCODED_BYTES)
        if type(meta['size']) is not int or meta['size'] != len(data) or meta['sha256'] != _sha(data) or meta['mime_type'] != detect_mime(data):
            return None
        return data
    except (OSError, ValueError, TypeError, KeyError, UnicodeError):
        return None


def _usage(tree):
    total_bytes = total_entries = 0
    for name in _names(tree, MAX_SOURCES + 1):
        if name == '.budget.lock':
            continue
        if not _KEY.fullmatch(name):
            raise _error()
        directory = _source_directory(tree, name, create=False)
        try:
            for item in _names(directory, MAX_SOURCE_FILES):
                if item != '.lock' and not _owned_file(item):
                    raise _error()
                info = os.stat(item, dir_fd=directory, follow_symlinks=False)
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                    raise _error()
                total_bytes += info.st_size
                total_entries += bool(_IMAGE.fullmatch(item))
        finally:
            os.close(directory)
    return total_bytes, total_entries


def _prune(directory, digest):
    for name in _names(directory, MAX_SOURCE_FILES):
        if _TEMP.fullmatch(name):
            _unlink(directory, name)
        elif _IMAGE.fullmatch(name):
            try:
                meta = _metadata(directory, name + '.json')
                keep = isinstance(meta, dict) and meta.get('source_sha256') == digest
            except (OSError, ValueError, TypeError, UnicodeError):
                keep = False
            if not keep:
                _unlink(directory, name)
                _unlink(directory, name + '.json')
        elif name.endswith('.json') and _IMAGE.fullmatch(name[:-5]):
            try:
                os.stat(name[:-5], dir_fd=directory, follow_symlinks=False)
            except FileNotFoundError:
                _unlink(directory, name)


def _atomic(directory, name, data):
    temp = '.tmp-' + uuid4().hex
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
    try:
        with os.fdopen(fd, 'wb') as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, name, src_dir_fd=directory, dst_dir_fd=directory)
        os.fsync(directory)
    finally:
        _unlink(directory, temp)


def _publish(tree, directory, result, digest, spec, snapshot):
    # Caller owns BOTH source and global budget flock; all writes are dirfd relative.
    _prune(directory, digest)
    # Remove corrupt entries before counting; unlink never follows a symlink.
    for name in (result.filename, result.filename + '.json'):
        _unlink(directory, name)
    metadata = dict(source_sha256=digest, spec=asdict(spec), sha256=result.sha256,
                    mime_type=result.mime_type, width=result.width, height=result.height,
                    size=len(result.data), source_key=result.source_key, filename=result.filename)
    files = [(result.filename, result.data), (result.filename + '.json', _json(metadata))]
    if snapshot is not None:
        # A corrupt snapshot can be a symlink/hardlink. Remove our names only;
        # never follow them and never let them poison the budget scan forever.
        _unlink(directory, 'snapshot.bin')
        _unlink(directory, 'snapshot.json')
        files = [('snapshot.bin', snapshot), ('snapshot.json', _json(dict(created=_now(), sha256=_sha(snapshot), size=len(snapshot), mime_type=detect_mime(snapshot))))] + files
    names = _names(directory, MAX_SOURCE_FILES)
    variants = sum(bool(_IMAGE.fullmatch(n)) for n in names)
    source_bytes = sum(os.stat(n, dir_fd=directory, follow_symlinks=False).st_size for n in names)
    total_bytes, total_entries = _usage(tree)
    added = sum(len(body) for _, body in files)
    # Conservative peak reservation includes old replaced snapshots and all temp bytes.
    if (variants >= MAX_VARIANTS or total_entries >= MAX_CACHE_ENTRIES
            or source_bytes + added > MAX_SOURCE_BYTES or total_bytes + added > MAX_CACHE_BYTES
            or len(names) + len(files) + 1 > MAX_SOURCE_FILES):
        raise ValueError('Image derivative capacity exceeded')
    for name, body in files:
        _atomic(directory, name, body)


async def _transform(data, mime, spec):
    deadline = time.monotonic() + DECODER_WAIT_SECONDS
    while True:
        try:
            return await transform_image(data, mime, target_mime=spec.mime_type, max_edge=spec.max_edge,
                                         min_edge=spec.min_edge, quality=spec.quality, preserve_animation=spec.preserve_animation)
        except ImageDecoderBusy:
            if time.monotonic() >= deadline:
                raise ValueError('Image derivative busy') from None
            await asyncio.sleep(.04)


async def get_derivative(source: str, spec: DerivativeSpec, *, upload_root: Path,
                         shared_root: str | None = None, allow_remote: bool = False) -> CachedDerivative:
    """Return a reusable derivative. Originals and shared storage are never written."""
    admitted = False
    try:
        _validate_spec(spec)
        kind, normalized = _source(source)
        if type(allow_remote) is not bool or (kind != 'local' and not allow_remote):
            raise _error()
        if shared_root:
            private = Path(upload_root).resolve()
            shared = Path(shared_root).expanduser().resolve()
            if private.is_relative_to(shared):
                raise _error()
        key = source_key_for(source)
        admitted = _PENDING.acquire(blocking=False)
        if not admitted:
            raise ValueError('Image derivative busy')
        async with asyncio.timeout(REQUEST_TIMEOUT_SECONDS):
            with _tree(upload_root) as tree:
                async with _alock(tree, '.budget.lock'):
                    directory = _source_directory(tree, key, create=True)
                try:
                    async with _alock(directory, '.lock'):
                        fresh_snapshot = None
                        if kind == 'local':
                            # Pass original spelling: the secure reader decodes exactly once.
                            data = read_local_image(source, Path(upload_root), shared_root)
                        elif kind == 'data':
                            header, encoded = normalized.split(',', 1)
                            data = base64.b64decode(encoded, validate=True)
                            if not data or len(data) > MAX_ENCODED_BYTES or detect_mime(data) != header[5:-7]:
                                raise _error()
                        else:
                            data = _snapshot(directory)
                            if data is None:
                                data, declared = await download_image(source)
                                if not data or len(data) > MAX_ENCODED_BYTES:
                                    raise _error()
                                actual = detect_mime(data)
                                if declared not in (None, '', 'application/octet-stream', actual):
                                    raise _error()
                                fresh_snapshot = data
                        mime, digest = detect_mime(data), _sha(data)
                        filename = _filename(digest, spec)
                        try:
                            result = _read_entry(directory, key, filename)
                        except (OSError, ValueError, TypeError, KeyError, UnicodeError, OverflowError):
                            result = None
                        if result is not None and fresh_snapshot is None:
                            return result
                        if result is None:
                            body, width, height = await _transform(data, mime, spec)
                            if (not body or len(body) > MAX_ENCODED_BYTES or detect_mime(body) != spec.mime_type
                                    or type(width) is not int or type(height) is not int
                                    or not all(max(1, spec.min_edge) <= v <= spec.max_edge for v in (width, height))
                                    or _dimensions(body, spec.mime_type) != (width, height)):
                                raise _error()
                            result = CachedDerivative(body, spec.mime_type, width, height, _sha(body), key, filename)
                        async with _alock(tree, '.budget.lock'):
                            _publish(tree, directory, result, digest, spec, fresh_snapshot)
                        return _read_entry(directory, key, filename)
                finally:
                    os.close(directory)
    except (OSError, ValueError, TypeError, KeyError, UnicodeError, OverflowError, TimeoutError, binascii.Error):
        raise _error() from None
    finally:
        if admitted:
            _PENDING.release()
