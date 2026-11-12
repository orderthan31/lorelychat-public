"""Private, bounded delete intents; an open locked ticket spans domain commit."""
from __future__ import annotations

from dataclasses import dataclass, field
import fcntl
import json
import logging
import os
from pathlib import Path
import re
import stat
from uuid import uuid4

from app.services.asset_image_service import _directory
from app.services.image_reference_cleanup import cleanup_sources

MAX_BYTES = 32768
MAX_TICKETS = 1024
_NAME = re.compile(r'[0-9a-f]{32}\.json\Z')
_LOG = logging.getLogger(__name__)


def _root(upload_root, shared_root):
    root = Path(upload_root).expanduser().resolve()
    if shared_root:
        shared = Path(shared_root).expanduser().resolve()
        if root.is_relative_to(shared) or shared.is_relative_to(root):
            raise ValueError('Unsafe cleanup journal root')
    return root


def _names(directory):
    # Bound both memory and work, including malformed entries.
    with os.scandir(directory) as entries:
        names = []
        for entry in entries:
            names.append(entry.name)
            if len(names) > MAX_TICKETS:
                break
    return names


def _payload_valid(payload):
    return (isinstance(payload, dict) and set(payload) == {'asset_id', 'character_id', 'urls'}
            and all(isinstance(payload[key], str) and 0 < len(payload[key]) <= 256
                    for key in ('asset_id', 'character_id'))
            and isinstance(payload['urls'], list) and 1 <= len(payload['urls']) <= 2
            and all(isinstance(url, str) and url for url in payload['urls']))


def _valid(directory, name, fd):
    info = os.fstat(fd)
    current = os.stat(name, dir_fd=directory, follow_symlinks=False)
    return (stat.S_ISREG(info.st_mode) and info.st_nlink == 1
            and info.st_size <= MAX_BYTES and stat.S_IMODE(info.st_mode) == 0o600
            and (info.st_dev, info.st_ino) == (current.st_dev, current.st_ino))


@dataclass
class CleanupTicket:
    directory: int
    fd: int
    name: str
    payload: dict = field(repr=False)

    def release(self):
        # Never mask an original commit failure; closing releases flock on death too.
        for attribute in ('fd', 'directory'):
            fd = getattr(self, attribute)
            setattr(self, attribute, -1)
            if fd >= 0:
                try:
                    os.close(fd)
                except OSError:
                    pass

    def complete(self, bind, *, upload_root, shared_root=None):
        from app.services.image_reference_cleanup import cleanup_deleted_asset_images
        try:
            if not _valid(self.directory, self.name, self.fd):
                return False
            result = cleanup_deleted_asset_images(bind, self.payload['character_id'], self.payload['urls'],
                upload_root=upload_root, shared_root=shared_root, deleted_asset_id=self.payload['asset_id'])
            if result['deferred'] or not _valid(self.directory, self.name, self.fd):
                return False
            os.unlink(self.name, dir_fd=self.directory)
            os.fsync(self.directory)
            return True
        except Exception:
            _LOG.warning('Asset image cleanup intent retained for retry')
            return False


def stage_cleanup(asset_id: str, character_id: str, urls: list[str], *, upload_root: Path,
                  shared_root: str | None = None) -> CleanupTicket | None:
    """Fail before domain deletion if required durable intent cannot be stored."""
    directory = fd = None
    try:
        root = Path(upload_root).expanduser().resolve()
        if not cleanup_sources(root, urls):
            return None
        root = _root(root, shared_root)
        payload = {'asset_id': asset_id, 'character_id': character_id,
                   'urls': [url for url in urls if isinstance(url, str) and url]}
        data = json.dumps(payload, ensure_ascii=True).encode('utf-8')
        if not _payload_valid(payload) or len(data) > MAX_BYTES:
            raise ValueError('Cleanup intent exceeds budget')
        # No mkdir on synthetic/missing sources. Directory flock serializes the
        # capacity check and creation, not the domain commit or source locks.
        directory = _directory(root, ['.image-cleanup'], create=True)
        parent = _directory(root, [])
        try:
            os.fsync(parent)
        finally:
            os.close(parent)
        fcntl.flock(directory, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            if len(_names(directory)) >= MAX_TICKETS:
                raise ValueError('Cleanup intent capacity reached')
            name = uuid4().hex + '.json'
            fd = os.open(name, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            # Immutable after publication; lock is held before any JSON is visible.
            with os.fdopen(os.dup(fd), 'wb') as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.fsync(directory)
        finally:
            fcntl.flock(directory, fcntl.LOCK_UN)
        ticket = CleanupTicket(directory, fd, name, payload)
        directory = fd = None
        return ticket
    except Exception:
        raise RuntimeError('Image cleanup intent could not be persisted') from None
    finally:
        for handle in (fd, directory):
            if handle is not None:
                try:
                    os.close(handle)
                except OSError:
                    pass


def replay_cleanup(bind, *, upload_root: Path, shared_root: str | None = None,
                   limit: int = 16, skip: str | None = None) -> int:
    """Bounded opportunistic replay; locked precommit tickets are never inspected."""
    directory = None
    completed = attempted = 0
    try:
        root = _root(upload_root, shared_root)
        directory = _directory(root, ['.image-cleanup'])
        for name in _names(directory):
            if attempted >= max(0, min(limit, 16)):
                break
            if name == skip or not _NAME.fullmatch(name):
                continue
            fd = None
            try:
                fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                if not _valid(directory, name, fd):
                    continue
                payload = json.loads(os.read(fd, MAX_BYTES + 1))
                if not _payload_valid(payload):
                    continue
                attempted += 1
                ticket = CleanupTicket(directory, fd, name, payload)
                completed += int(ticket.complete(bind, upload_root=root, shared_root=shared_root))
            except (OSError, ValueError, TypeError):
                continue
            finally:
                if fd is not None:
                    os.close(fd)
    except FileNotFoundError:
        pass
    except Exception:
        _LOG.warning('Asset image cleanup replay deferred')
    finally:
        if directory is not None:
            os.close(directory)
    return completed
