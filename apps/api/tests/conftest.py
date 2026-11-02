# Synthetic roots must be established BEFORE importing any application module.
import os
import tempfile
from pathlib import Path

_test_root = Path(tempfile.mkdtemp(prefix="lorechat-tests-", dir=os.environ.get("TMPDIR") or tempfile.gettempdir()))
for _key, _value in {
    "DATABASE_URL": "sqlite:///" + str(_test_root / "global.db"),
    "UPLOAD_ROOT": str(_test_root / "uploads"),
    "PROVIDER_SECRET_ROOT": str(_test_root / "secrets"),
    "LOG_DIR": str(_test_root / f"lorechat-test-logs-{os.getpid()}"),
    "MEM0_ENABLED": "false", "MEMORY_PROVIDER": "local",
    "SHARED_UPLOAD_ROOT": "", "LLM_MOCK": "true", "LOCAL_LLM_FALLBACK_ENABLED": "false",
}.items():
    os.environ[_key] = _value
# Unit suites are provider-free even when a test temporarily disables LLM_MOCK.
# ASGI and MockTransport do not need AF_INET; Unix async wakeups remain allowed.
import socket
if os.environ.get("HARDENING_GUARD_ACTIVE") != "1":
    for _method in ("connect", "connect_ex", "sendto"):
        _original = getattr(socket.socket, _method)
        def _deny_test_network(self, *args, _call=_original, **kwargs):
            if self.family in (socket.AF_INET, socket.AF_INET6):
                raise RuntimeError("unit-test provider/TCP network denied")
            return _call(self, *args, **kwargs)
        setattr(socket.socket, _method, _deny_test_network)
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, Session, create_engine
from app.db.session import get_session
from app.main import app


@pytest.fixture(autouse=True)
def clear_settings_cache(monkeypatch):
    monkeypatch.setenv("LLM_MOCK", "true")
    monkeypatch.setenv("CHAT_LLM_PROVIDER", "openai_compatible")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    from app.core.config import get_settings
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def engine():
    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(test_engine)
    try:
        yield test_engine
    finally:
        test_engine.dispose()


@pytest.fixture
def session(engine):
    with Session(engine) as test_session:
        yield test_session


@pytest.fixture
def client(engine, monkeypatch):
    monkeypatch.setattr(app.state, "engine", engine, raising=False)
    # Existing suites explicitly drive workers. Onboarding tests opt into the
    # real mock-only execution path without enabling live dispatchers.
    monkeypatch.setattr(app.state, "mock_execution", False, raising=False)
    # Request fixtures historically start empty; tests seed authored commands
    # explicitly. Engine isolation must not silently add default-name conflicts.
    monkeypatch.setattr("app.main.seed_default_chat_commands", lambda _session: None)
    def override_get_session():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
