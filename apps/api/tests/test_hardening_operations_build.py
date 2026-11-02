"""Static build contracts only; no Docker/image/network execution is claimed."""
from pathlib import Path
import pytest

ROOT=Path(__file__).resolve().parents[3]

def test_browser_provisioned_before_test_gate():
    text=(ROOT/'apps/web/Dockerfile').read_text()
    assert 'node:22-bookworm-slim' in text
    assert './node_modules/.bin/playwright install --with-deps chromium' in text
    assert text.index('playwright install')<text.index('npm run test')
    assert 'npm ci' in text and 'npm run typecheck' in text and 'npm run build' in text
    assert 'npm install ' not in text

@pytest.mark.parametrize('pattern',['apps/web/**/node_modules','apps/web/**/dist','apps/web/**/.env*','apps/web/**/.cache','apps/web/**/test-results','apps/web/**/playwright-report'])
def test_actual_root_context_excludes_local_artifacts(pattern):
    lines=(ROOT/'.dockerignore').read_text().splitlines()
    assert pattern in lines
    assert lines.index(pattern)>lines.index('!apps/web/**')

def test_web_context_does_not_reinclude_sibling_applications():
    lines=(ROOT/'.dockerignore').read_text().splitlines()
    # Re-including the apps parent must not recursively admit API runtime files.
    assert 'apps/*' in lines
    assert lines.index('!apps/') < lines.index('apps/*') < lines.index('!apps/web/')


def test_api_context_allowlists_inputs_and_rejects_nested_runtime():
    lines=(ROOT/'apps/api/.dockerignore').read_text().splitlines()
    assert '*' in lines
    for required in ('!requirements.txt', '!pyproject.toml', '!docker-entrypoint.sh', '!app/**', '!scripts/**', '!seeds/**'):
        assert required in lines
    for forbidden in ('**/.env', '**/.runtime', '**/*.db-*', '**/*.sqlite-*', '**/provider_secrets.json', '**/__pycache__/**'):
        assert forbidden in lines
        assert lines.index(forbidden) > lines.index('!seeds/**')


def test_shutdown_settings_are_explicit():
    compose=(ROOT/'docker-compose.yml').read_text()
    for setting in ['LLM_TASK_DEADLINE_SECONDS','DISPATCHER_SHUTDOWN_TIMEOUT_SECONDS','TTS_TIMEOUT_SECONDS']:
        assert setting in compose
    assert 'stop_grace_period: 45s' in compose
    assert '"--timeout-graceful-shutdown", "35"' in (ROOT/'apps/api/Dockerfile').read_text()
