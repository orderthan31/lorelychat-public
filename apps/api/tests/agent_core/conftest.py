"""Standalone isolated suite: run with --confcutdir=tests/agent_core."""
import os
import tempfile
from pathlib import Path
import socket

ROOT = Path(tempfile.mkdtemp(prefix='agent-core-', dir=os.environ['TMPDIR']))
for key, value in {'DATABASE_URL': f'sqlite:///{ROOT}/global.db', 'UPLOAD_ROOT': str(ROOT/'uploads'), 'PROVIDER_SECRET_ROOT': str(ROOT/'secrets'), 'LOG_DIR': str(ROOT/'logs'), 'SHARED_UPLOAD_ROOT': '', 'LLM_MOCK': 'true', 'MEM0_ENABLED': 'false', 'MEMORY_PROVIDER': 'local', 'LOCAL_LLM_FALLBACK_ENABLED': 'false'}.items():
    os.environ[key] = value
for method in ('connect', 'connect_ex', 'sendto'):
    original = getattr(socket.socket, method)
    def deny(self, *args, _original=original, **kwargs):
        if self.family in (socket.AF_INET, socket.AF_INET6):
            raise RuntimeError('agent core tests deny TCP')
        return _original(self, *args, **kwargs)
    setattr(socket.socket, method, deny)

import pytest
from sqlmodel import SQLModel, create_engine

@pytest.fixture
def service(tmp_path, monkeypatch):
    from app.agent.schema import initialize_agent_schema
    from app.agent.service import AgentService
    from app.agent.contracts import SettingsUpdate
    engine = create_engine(f'sqlite:///{tmp_path}/test.db', connect_args={'check_same_thread': False})
    SQLModel.metadata.create_all(engine)
    initialize_agent_schema(engine)
    svc = AgentService(engine)
    svc.update_settings(SettingsUpdate(enabled=True))
    yield svc
    engine.dispose()

@pytest.fixture
def owner():
    from app.agent.contracts import Principal
    return Principal('owner', frozenset(), True)
