"""Compatibility integration: terminal metadata never leaks or fabricates sends."""
import json
from types import SimpleNamespace

import httpx
import pytest

from app.engine import llm_client as llm
from app.engine import litellm_transport as sdk
from app.engine import llm_transport_deadline as deadline
from test_litellm_transport import make, native, wire


@pytest.mark.parametrize('status,code,kind', [
    (400, None, llm.LLMRequestError), (401, None, llm.LLMAuthenticationError),
    (403, None, llm.LLMAuthenticationError), (404, None, llm.LLMConfigurationError),
    (422, None, llm.LLMRequestError), (429, 'content_policy_violation', llm.LLMPolicyBlockedError),
    (429, 'insufficient_quota', llm.LLMConfigurationError),
])
@pytest.mark.parametrize('transport', ['litellm', 'legacy_http'])
async def test_terminal_http_warning_count_keeps_no_raw_diagnostics(wire, monkeypatch, caplog, capsys,
                                                                  transport, status, code, kind):
    private = 'PRIVATE_WARNING_PROMPT_CREDENTIAL_SENTINEL'
    sends = []
    async def send(request):
        sends.append(request)
        return httpx.Response(status, json={'error': {'code': code, 'message': private}},
                              headers=[('warning', private), ('warning', private)])
    if transport == 'litellm':
        monkeypatch.setattr(sdk, 'new_http_transport', lambda: httpx.MockTransport(send))
    else:
        real = httpx.AsyncClient
        monkeypatch.setattr(httpx, 'AsyncClient', lambda **kw: real(transport=httpx.MockTransport(send), **kw))
    obj = make(circuit_breaker_enabled=True)
    obj.settings.llm_transport = transport
    obj.reset_circuit_breakers()
    @llm.bounded_llm_task
    async def run():
        with pytest.raises(kind) as caught:
            await obj.chat([{'role': 'user', 'content': private}])
        error = caught.value
        assert not isinstance(error, (ValueError, llm.LLMUnavailableError))
        assert error.http_status == status
        assert error.transport_attempts == llm.task_attempt_metadata()['network_attempts'] == len(sends) == 1
        assert error.provider_warning_count == 2
        assert private not in str(error) + json.dumps(vars(error))
    await run()
    assert llm._CIRCUIT_STATES[obj.circuit_key()]['failures'] == 0
    output = capsys.readouterr()
    assert private not in output.out + output.err + caplog.text


async def test_synchronous_sdk_bootstrap_expiry_denies_send_deterministically(wire, monkeypatch):
    # Cooperative deadlines cannot preempt synchronous bootstrap. Advancing a
    # fake monotonic clock proves expiry is enforced before authorizing I/O,
    # without turning the wallclock limitation into a flaky sleep assertion.
    module = sdk.load_sdk()
    clock = [100.0]
    fake = SimpleNamespace(monotonic=lambda: clock[0])
    monkeypatch.setattr(llm, 'time', fake)
    monkeypatch.setattr(deadline, 'time', fake)
    def synchronous_bootstrap():
        clock[0] += 2.0
        return module
    monkeypatch.setattr(sdk, 'load_sdk', synchronous_bootstrap)
    obj = make()
    obj.settings.llm_task_deadline_seconds = 1.0
    with pytest.raises(llm.LLMTaskDeadlineError) as caught:
        await obj.chat([])
    assert clock[0] == 102.0  # Synchronous work overran the one-second budget.
    assert caught.value.transport_attempts == 0
    assert wire[0] == []
