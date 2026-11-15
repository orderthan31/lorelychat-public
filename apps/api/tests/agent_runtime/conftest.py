"""Credential-free SDK wires; run with --confcutdir=tests/agent_runtime."""
import os
import socket
import tempfile
from pathlib import Path

import pytest

ROOT = Path(tempfile.mkdtemp(prefix='lore-agent-runtime-', dir=os.environ['TMPDIR']))
for key, value in {
    'DATABASE_URL': f'sqlite:///{ROOT}/db.sqlite', 'UPLOAD_ROOT': str(ROOT / 'uploads'),
    'PROVIDER_SECRET_ROOT': str(ROOT / 'secrets'), 'LOG_DIR': str(ROOT / 'logs'),
    'LITELLM_LOCAL_MODEL_COST_MAP': 'True', 'LANGSMITH_TRACING': 'false',
    'LANGCHAIN_TRACING_V2': 'false',
}.items():
    os.environ[key] = value


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    original = socket.socket.connect
    def deny(self, *args, **kwargs):
        if self.family in (socket.AF_INET, socket.AF_INET6):
            raise AssertionError('Synthetic test attempted network access')
        return original(self, *args, **kwargs)
    monkeypatch.setattr(socket.socket, 'connect', deny)
