import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import stat

from app.core.logging_config import SensitiveDataFilter, redact_sensitive_text


def test_sensitive_log_filter_redacts_query_headers_and_json_values():
    raw = (
        "POST https://example.test/generate?key=fake-query-secret&token=fake-token "
        "Authorization: Bearer fake.bearer.secret "
        "payload={'api_key': 'fake-json-secret'}"
    )

    redacted = redact_sensitive_text(raw)

    assert "fake-query-secret" not in redacted
    assert "fake-token" not in redacted
    assert "fake.bearer.secret" not in redacted
    assert "fake-json-secret" not in redacted
    assert redacted.count("[REDACTED]") == 4

    record = logging.LogRecord("httpx", logging.INFO, __file__, 1, "%s", (raw,), None)
    assert SensitiveDataFilter().filter(record)
    assert "fake-query-secret" not in record.getMessage()
    assert record.args == ()


def test_pytest_file_handlers_are_isolated_and_private():
    managed_handlers = [
        handler
        for handler in logging.getLogger().handlers
        if isinstance(handler, RotatingFileHandler)
        and getattr(handler, "_lorechat_file_handler", False)
    ]

    assert len(managed_handlers) == 2
    for handler in managed_handlers:
        path = Path(handler.baseFilename)
        assert path.parent.name.startswith("lorechat-test-logs-")
        assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
