"""Standalone MCP tests: run with --confcutdir=tests/mcp_gateway."""
import os
from pathlib import Path
import socket
import tempfile

_root = Path(tempfile.mkdtemp(prefix="lore-mcp-tests-", dir=os.environ.get("TMPDIR")))
for key, value in {
    "DATABASE_URL": "sqlite:///" + str(_root / "unused.db"),
    "UPLOAD_ROOT": str(_root / "uploads"),
    "PROVIDER_SECRET_ROOT": str(_root / "secrets"),
    "LOG_DIR": str(_root / "logs"),
    "MEM0_ENABLED": "false", "MEMORY_PROVIDER": "local", "LLM_MOCK": "true",
    "SHARED_UPLOAD_ROOT": "", "LOCAL_LLM_FALLBACK_ENABLED": "false",
}.items():
    os.environ[key] = value
for method in ("connect", "connect_ex", "sendto"):
    original = getattr(socket.socket, method)
    def deny_network(self, *args, _original=original, **kwargs):
        if self.family in (socket.AF_INET, socket.AF_INET6):
            raise RuntimeError("MCP tests forbid TCP/provider calls")
        return _original(self, *args, **kwargs)
    setattr(socket.socket, method, deny_network)
