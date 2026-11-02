from __future__ import annotations

from contextlib import contextmanager
import fcntl
import json
import os
import secrets
import tempfile
import threading
from pathlib import Path

from app.core.config import get_settings

# A separate lock inode survives atomic replacement of the data inode. Serialize
# the complete read/modify/replace across threads AND processes on local POSIX FS.
SECRET_ROOT = Path(get_settings().provider_secret_root).expanduser().resolve()
SECRET_FILE = SECRET_ROOT / 'provider_secrets.json'
_LOCK = threading.RLock()

class SecretStoreError(RuntimeError):
    """Fixed safe diagnostic; never includes a credential, ref, hint or path."""

@contextmanager
def _locked():
    with _LOCK:
        try:
            SECRET_ROOT.mkdir(parents=True, exist_ok=True, mode=0o700)
            fd = os.open(str(SECRET_FILE)+'.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
            try:
                os.fchmod(fd, 0o600)
                fcntl.flock(fd, fcntl.LOCK_EX)
                yield
            finally:
                os.close(fd)
        except OSError:
            raise SecretStoreError('Secret store operation failed') from None

def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError()
        result[key] = value
    return result

def _load() -> dict[str, str]:
    try:
        # Refuse symlinks rather than exposing arbitrary files via the secret root.
        fd = os.open(SECRET_FILE, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        return {}
    try:
        with os.fdopen(fd, 'r', encoding='utf-8') as source:
            data = json.load(source, object_pairs_hook=_unique_object)
        if not isinstance(data, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k,v in data.items()):
            raise ValueError()
        return data
    except (ValueError, UnicodeError):
        raise SecretStoreError('Secret store is corrupt') from None

def _save(data: dict[str, str]) -> None:
    path = None
    try:
        fd, path = tempfile.mkstemp(prefix='.provider-secrets-', dir=SECRET_ROOT)
        with os.fdopen(fd, 'w', encoding='utf-8') as target:
            os.fchmod(target.fileno(), 0o600)
            json.dump(data, target, ensure_ascii=False, indent=2)
            target.flush()
            os.fsync(target.fileno())
        os.replace(path, SECRET_FILE)
        directory = os.open(SECRET_ROOT, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except OSError:
        raise SecretStoreError('Secret store persistence failed') from None
    finally:
        if path is not None:
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass

def mask_secret(value: str | None) -> str | None:
    if not value:
        return None
    compact = value.strip()
    if len(compact) <= 8:
        return '••••'
    return f'{compact[:4]}…{compact[-4:]}'

def save_secret(value: str) -> tuple[str, str | None]:
    return update_secret(None, value)

def update_secret(secret_ref: str | None, value: str) -> tuple[str, str | None]:
    with _locked():
        data = _load()
        next_ref = secret_ref or f'secret_{secrets.token_urlsafe(18)}'
        data[next_ref] = value
        _save(data)
    return next_ref, mask_secret(value)

def get_secret(secret_ref: str | None) -> str | None:
    if not secret_ref:
        return None
    with _locked():
        return _load().get(secret_ref)

def delete_secret(secret_ref: str | None) -> None:
    if not secret_ref:
        return
    with _locked():
        data = _load()
        if secret_ref in data:
            del data[secret_ref]
            _save(data)
