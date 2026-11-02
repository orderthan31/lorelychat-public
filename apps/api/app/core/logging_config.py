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
    text = _BEARER_PATTERN.sub(lambda match: f"{match.group(1)}{_REDACTED}", text)
    return re.sub(r"(?<![\w:])/(?:root|home|private|data|secrets|uploads|tmp|var)/[^\s\"\']+", "[PRIVATE_PATH]", text)


def safe_log_message(record):
    import copy
    if record.name == 'py.warnings':
        return 'Python warning [diagnostic value omitted]'
    safe = copy.copy(record)
    def clean(value):
        return type(value).__name__ + ': [diagnostic value omitted]' if isinstance(value, BaseException) else value
    safe.msg = clean(record.msg)
    if isinstance(record.args, dict):
        safe.args = {key: clean(value) for key, value in record.args.items()}
    elif record.args:
        safe.args = tuple(clean(value) for value in record.args)
    message = safe.getMessage()
    exc = record.exc_info[1] if record.exc_info else None
    seen = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        try:
            value = str(exc)
            if value:
                message = message.replace(value, '[diagnostic value omitted]')
        except Exception:
            message = '[unsafe diagnostic omitted]'
        exc = exc.__cause__ or (exc.__context__ if not exc.__suppress_context__ else None)
    return redact_sensitive_text(message)


class SensitiveDataFormatter(logging.Formatter):
    """Sanitize at the final formatter boundary, never serialize exception values.

    Exception strings and source-code lines can contain arbitrary provider prose,
    not only recognizable keys. Keep class and frame identity, omit both values.
    Cached exc_text is deliberately ignored and never shared across handlers.
    """
    def formatException(self, ei):
        import traceback
        exc = ei[1]
        seen = set()
        lines = []
        while exc is not None and id(exc) not in seen:
            seen.add(id(exc))
            lines.append(type(exc).__name__ + ': [diagnostic value omitted]')
            for frame in traceback.extract_tb(exc.__traceback__):
                lines.append(f'  {Path(frame.filename).name}:{frame.lineno} in {frame.name}')
            exc = exc.__cause__ or (exc.__context__ if not exc.__suppress_context__ else None)
        return '\n'.join(lines)

    def format(self, record):
        import copy
        safe = copy.copy(record)
        safe.msg = safe_log_message(record)
        safe.args = ()
        safe.exc_text = None
        # Warning source paths/lines and warning text may contain sensitive data.
        if record.name == 'py.warnings':
            safe.msg = 'Python warning [diagnostic value omitted]'
        if safe.stack_info:
            safe.stack_info = '[stack diagnostic omitted]'
        return redact_sensitive_text(super().format(safe))


class SensitiveDataFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = safe_log_message(record)
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
    formatter = SensitiveDataFormatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s")
    logging.captureWarnings(True)
    # Cover preconfigured root stream/stderr handlers as well as file handlers.
    for existing in root.handlers:
        previous = existing.formatter
        existing.setFormatter(SensitiveDataFormatter(previous._fmt if previous else "%(levelname)s %(message)s", datefmt=previous.datefmt if previous else None))
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
