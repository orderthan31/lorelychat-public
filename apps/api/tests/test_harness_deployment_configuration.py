"""Check deployment control forwarding without containers or provider calls."""
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from app.core.config import Settings


REPOSITORY = Path(__file__).resolve().parents[3]


@pytest.fixture(scope="module")
def compose_environment():
    if shutil.which("docker") is None:
        pytest.skip("Docker Compose CLI is optional for local API-only tests")
    environment = {key: os.environ[key] for key in ("PATH", "HOME", "TMPDIR") if key in os.environ}
    available = subprocess.run(
        ["docker", "compose", "version", "--short"], env=environment,
        capture_output=True, text=True, timeout=15,
    )
    if available.returncode:
        pytest.skip("Docker Compose plugin is not installed")
    return environment


@pytest.mark.parametrize("text,serialization,policy,budget", [
    ("", "transcript", "compact", None),
    ("CHAT_CONTEXT_POLICY=expanded\n", "transcript", "expanded", None),
    ("CHAT_HISTORY_SERIALIZATION=native\nCHAT_CONTEXT_POLICY=full_history\n"
     "CHAT_CONTEXT_INPUT_BUDGET_TOKENS=24000\n", "native", "full_history", 24000),
])
def test_compose_passes_comparison_controls_without_empty_optional_integer(
    tmp_path, compose_environment, text, serialization, policy, budget,
):
    dotenv = tmp_path / "comparison.env"
    dotenv.write_text(text)
    result = subprocess.run(
        ["docker", "compose", "--env-file", str(dotenv), "-f",
         str(REPOSITORY / "docker-compose.yml"), "config", "--format", "json"],
        env=compose_environment, capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stderr
    environment = json.loads(result.stdout)["services"]["api"]["environment"]
    assert environment["CHAT_HISTORY_SERIALIZATION"] == serialization
    assert environment["CHAT_CONTEXT_POLICY"] == policy
    assert environment["CHAT_CONTEXT_INPUT_BUDGET_TOKENS"] == (str(budget) if budget is not None else None)
    parsed = Settings(
        _env_file=None,
        chat_history_serialization=environment["CHAT_HISTORY_SERIALIZATION"],
        chat_context_policy=environment["CHAT_CONTEXT_POLICY"],
        chat_context_input_budget_tokens=environment["CHAT_CONTEXT_INPUT_BUDGET_TOKENS"],
    )
    assert parsed.chat_history_serialization == serialization
    assert parsed.chat_context_policy == policy
    assert parsed.chat_context_input_budget_tokens == budget


def test_example_keeps_opt_in_comparisons_disabled():
    values = dict(
        line.split("=", 1)
        for line in (REPOSITORY / ".env.example").read_text().splitlines()
        if line and not line.startswith("#") and "=" in line
    )
    assert values["CHAT_HISTORY_SERIALIZATION"] == "transcript"
    assert values["CHAT_CONTEXT_POLICY"] == "compact"
    assert "CHAT_CONTEXT_INPUT_BUDGET_TOKENS" not in values
