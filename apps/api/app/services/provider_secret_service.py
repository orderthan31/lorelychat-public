from __future__ import annotations

import json
import os
import secrets
from pathlib import Path

from app.core.config import get_settings

# Local file-backed secret store for self-hosted deployments. The directory is
# mounted separately in Docker and must never be committed or exposed publicly.
SECRET_ROOT = Path(get_settings().provider_secret_root).expanduser().resolve()
SECRET_FILE = SECRET_ROOT / "provider_secrets.json"


def _load() -> dict[str, str]:
    if not SECRET_FILE.exists():
        return {}
    try:
        return json.loads(SECRET_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _save(data: dict[str, str]) -> None:
    SECRET_ROOT.mkdir(parents=True, exist_ok=True)
    SECRET_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        os.chmod(SECRET_FILE, 0o600)
    except OSError:
        pass


def mask_secret(value: str | None) -> str | None:
    if not value:
        return None
    compact = value.strip()
    if len(compact) <= 8:
        return "••••"
    return f"{compact[:4]}…{compact[-4:]}"


def save_secret(value: str) -> tuple[str, str | None]:
    secret_ref = f"secret_{secrets.token_urlsafe(18)}"
    data = _load()
    data[secret_ref] = value
    _save(data)
    return secret_ref, mask_secret(value)


def update_secret(secret_ref: str | None, value: str) -> tuple[str, str | None]:
    data = _load()
    next_ref = secret_ref or f"secret_{secrets.token_urlsafe(18)}"
    data[next_ref] = value
    _save(data)
    return next_ref, mask_secret(value)


def get_secret(secret_ref: str | None) -> str | None:
    if not secret_ref:
        return None
    return _load().get(secret_ref)


def delete_secret(secret_ref: str | None) -> None:
    if not secret_ref:
        return
    data = _load()
    if secret_ref in data:
        del data[secret_ref]
        _save(data)
