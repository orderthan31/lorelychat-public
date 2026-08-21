from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import re
import sys
import tempfile

_MAX_BYTES = 10 * 1024 * 1024
_BACKUP_COUNT = 7
_MANAGED_HANDLER_MARKER = "_lorechat_file_handler"
_REDACTED = "[REDACTED]"
_QUERY_SECRET_PATTERN = re.compile(
    r"([?&](?:key|api[_-]?key|access[_-]?token|token|secret)=)[^&\s]+",
    re.IGNORECASE,
)
_JSON_SECRET_PATTERN = re.compile(
    r'(["\'](?:api[_-]?key|access[_-]?token|token|authorization|secret)["\']\s*:\s*["\'])[^"\']+(["\'])',
    re.IGNORECASE,
)
_BEARER_PATTERN = re.compile(r"(\bBearer\s+)[A-Za-z0-9._~+/=-]+", re.IGNORECASE)


def redact_sensitive_text(value: object) -> str:
    text = str(value)
    text = _QUERY_SECRET_PATTERN.sub(lambda match: f"{match.group(1)}{_REDACTED}", text)
    text = _JSON_SECRET_PATTERN.sub(lambda match: f"{match.group(1)}{_REDACTED}{match.group(2)}", text)
    return _BEARER_PATTERN.sub(lambda match: f"{match.group(1)}{_REDACTED}", text)


class SensitiveDataFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact_sensitive_text(record.getMessage())
        record.args = ()
        return True


def _running_under_pytest() -> bool:
    return "pytest" in sys.modules or bool(os.getenv("PYTEST_CURRENT_TEST"))


def resolve_log_directory(log_dir: str | Path | None = None) -> Path:
    if log_dir is not None:
        return Path(log_dir)
    if _running_under_pytest():
        return Path(tempfile.gettempdir()) / f"lorechat-test-logs-{os.getpid()}"
    return Path("logs")


def secure_log_path(path: Path, *, directory: bool = False) -> None:
    try:
        path.chmod(0o700 if directory else 0o600)
    except OSError:
        # Logging must still start on filesystems that do not expose POSIX modes.
        pass


def configure_file_logging(log_dir: str | Path | None = None) -> None:
    """Configure rotating app/error logs without leaking credentials.

    Pytest processes are routed to a process-specific temporary directory so
    deliberate failure tests cannot pollute operational logs.
    """
    root = logging.getLogger()
    if any(getattr(handler, _MANAGED_HANDLER_MARKER, False) for handler in root.handlers):
        return

    resolved_log_dir = resolve_log_directory(log_dir)
    resolved_log_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    secure_log_path(resolved_log_dir, directory=True)
    formatter = logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s")
    redaction_filter = SensitiveDataFilter()

    app_handler = RotatingFileHandler(
        resolved_log_dir / "app.log",
        maxBytes=_MAX_BYTES,
        backupCount=_BACKUP_COUNT,
        encoding="utf-8",
    )
    app_handler.setLevel(logging.INFO)
    app_handler.setFormatter(formatter)
    app_handler.addFilter(redaction_filter)
    setattr(app_handler, _MANAGED_HANDLER_MARKER, True)

    error_handler = RotatingFileHandler(
        resolved_log_dir / "error.log",
        maxBytes=_MAX_BYTES,
        backupCount=_BACKUP_COUNT,
        encoding="utf-8",
    )
    error_handler.setLevel(logging.ERROR)
    error_handler.setFormatter(formatter)
    error_handler.addFilter(redaction_filter)
    setattr(error_handler, _MANAGED_HANDLER_MARKER, True)

    secure_log_path(resolved_log_dir / "app.log")
    secure_log_path(resolved_log_dir / "error.log")

    root.setLevel(logging.INFO)
    root.addHandler(app_handler)
    root.addHandler(error_handler)
    logging.getLogger("uvicorn.access").setLevel(logging.INFO)
    logging.getLogger("uvicorn.error").setLevel(logging.INFO)
