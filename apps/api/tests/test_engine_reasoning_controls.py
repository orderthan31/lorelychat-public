"""Real installed SDK + synthetic HTTP only; run from an isolated source snapshot."""
import json

import httpx
import pytest

from app.core.config import Settings
from app.engine.llm_client import LLMClient
from app.engine.llm_error_types import LLMCapabilityError
from app.engine import litellm_transport as transport, vertex_auth


@pytest.fixture
def make_client(monkeypatch):
    def make(provider='google', model='gemini-3.8-flash', effort='low', profile='chat', **extra):
        overrides = dict(provider=provider, model=model, api_key='synthetic-only-key',
                         provider_account_id='synthetic-account', min_output_tokens=1280,
                         circuit_breaker_enabled=False)
        if effort is not None:
            overrides['reasoning_effort'] = effort
        overrides.update(extra)
        client = LLMClient(Settings(_env_file=None, llm_mock=False, llm_transport='litellm',
                                   chat_thinking_policy='off_when_supported'),
                           profile=profile, overrides=overrides)
        monkeypatch.setattr(client, '_record_usage', lambda *a, **kw: None)
        monkeypatch.setattr(client, '_record_transport_failure', lambda *a, **kw: None)
        return client
    return make


@pytest.fixture
def vertex_credentials(monkeypatch):
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives import serialization
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    secret = json.dumps({'type': 'service_account', 'project_id': 'fixture-project',
                         'private_key_id': 'synthetic-id',
                         'private_key': key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                                         serialization.NoEncryption()).decode(),
                         'client_email': 'fixture@fixture-project.iam.gserviceaccount.com',
                         'client_id': '12345', 'token_uri': vertex_auth.TOKEN_URI})
    monkeypatch.setattr(vertex_auth, 'new_auth_transport', lambda: httpx.MockTransport(
        lambda request: httpx.Response(200, json={'access_token': 'synthetic-offline-token',
                                                'expires_in': 3600, 'token_type': 'Bearer'})))
    return dict(api_key=secret, vertex_project='fixture-project', vertex_location='global',
                vertex_auth_mode='service_account')


def native_reply(provider, model):
    if provider in {'google', 'vertex_ai'}:
        return {'candidates': [{'content': {'role': 'model', 'parts': [{'text': 'Synthetic original prose'}]},
                                'finishReason': 'STOP'}],
                'usageMetadata': {'promptTokenCount': 10, 'candidatesTokenCount': 3, 'totalTokenCount': 13},
                'modelVersion': model}
    if provider == 'anthropic':
        return {'id': 'msg_fixture', 'type': 'message', 'role': 'assistant', 'model': model,
                'content': [{'type': 'text', 'text': 'Synthetic original prose'}],
                'stop_reason': 'end_turn', 'stop_sequence': None,
                'usage': {'input_tokens': 10, 'output_tokens': 3}}
    return {'id': 'chatcmpl-fixture', 'object': 'chat.completion', 'created': 1, 'model': model,
            'choices': [{'index': 0, 'message': {'role': 'assistant', 'content': 'Synthetic original prose'},
                         'finish_reason': 'stop', 'logprobs': None}],
            'usage': {'prompt_tokens': 10, 'completion_tokens': 3, 'total_tokens': 13}}


@pytest.mark.asyncio
@pytest.mark.parametrize('provider,model,effort,effective', [
    ('google', 'gemini-3.8-flash', 'low', 'low'),
    ('google', 'gemini-3.8-flash', 'medium', 'medium'),
    ('google', 'gemini-3.8-flash', 'high', 'high'),
    ('google', 'gemini-3.8-flash', 'xhigh', 'high'),
    ('google', 'gemini-4-hydrogen', 'max', 'high'),
    ('vertex_ai', 'gemini-3.8-flash', 'low', 'low'),
    ('vertex_ai', 'gemini-4-hydrogen', 'max', 'high'),
    ('anthropic', 'claude-sonnet-4-6', 'low', 'low'),
    ('anthropic', 'claude-opus-4-8', 'max', 'max'),
    ('openai', 'gpt-5.2', 'max', 'xhigh'),
    ('openai', 'gpt-5.2', 'low', 'low'),
    ('openai', 'gpt-4o', 'low', None),
    ('xai', 'grok-4.3', 'max', 'high'),
    ('xai', 'grok-2', 'high', None),
])
async def test_actual_sdk_wire(make_client, vertex_credentials, monkeypatch, provider, model, effort, effective):
    captures = []
    async def send(request):
        captures.append((str(request.url), json.loads(request.content)))
        return httpx.Response(200, json=native_reply(provider, model))
    monkeypatch.setattr(transport, 'new_http_transport', lambda: httpx.MockTransport(send))
    client = make_client(provider, model, effort, **(vertex_credentials if provider == 'vertex_ai' else {}))
    admitted = client.max_output_tokens()
    response = await client.chat([{'role': 'user', 'content': 'Synthetic offline fixture.'}])
    assert len(captures) == response.generation_attempts == 1
    assert response.content == 'Synthetic original prose'
    assert client.reasoning_resolution().effective == effective
    diagnostic = response.transport_diagnostics
    assert diagnostic['requested_controls']['reasoning_effort'] == effort
    assert diagnostic['output_headroom']['final_max_output_tokens'] == admitted
    wire = diagnostic['serialized_wire_controls']
    assert wire['max_tokens'] == admitted
    body = captures[0][1]
    if effective:
        if provider in {'google', 'vertex_ai'}:
            assert body['generationConfig']['thinkingConfig']['thinkingLevel'].lower() == effective
            assert 'thinkingConfig.thinkingLevel' in wire
            # Native translation is captured, never mislabeled literal effort parity.
            assert not diagnostic['thinking_policy']['applied_on_wire']
        elif provider == 'anthropic':
            assert body['thinking']['type'] == 'adaptive'
            assert body['output_config']['effort'] == effective
            assert wire['output_config.effort'] == effective
            assert 'temperature' not in body and 'top_p' not in body
        else:
            assert body['reasoning_effort'] == effective
            if provider == 'openai':
                assert 'temperature' not in body
    else:
        assert 'reasoning_effort' not in body
        assert admitted == 2560
        assert diagnostic['thinking_policy']['omission_reason']


@pytest.mark.parametrize('level,reserve', [('low', 4096), ('medium', 8192), ('high', 16384),
                                         ('xhigh', 32768), ('max', 65536)])
def test_scaled_cached_headroom(make_client, level, reserve):
    client = make_client('anthropic', 'claude-opus-4-8', level)
    assert client.max_output_tokens() == 2560 + reserve
    assert client.output_budget_diagnostics()['reasoning_reserve_tokens'] == reserve
    client.overrides['model_max_output_tokens'] = 1
    assert client.max_output_tokens() == 2560 + reserve  # admission and dispatch stable


def test_authoritative_limits_and_actual_model(make_client):
    client = make_client('openai', 'gpt-5.2', 'max', model_max_output_tokens=9000,
                         model_context_window_tokens=8000)
    assert client.max_output_tokens() == 8000
    d = client.output_budget_diagnostics()
    assert d['clamped'] and d['effective_effort'] == 'xhigh'
    assert d['limits']['output']['source'] == 'selected_model'
    assert client.max_output_tokens(model=None) == 8000
    assert client.max_output_tokens(model='gpt-4o') == 2560


@pytest.mark.parametrize('effort', [None, 'low', 'max'])
def test_compression_caps_unchanged(make_client, effort):
    client = make_client('openai', 'gpt-5.2', effort, profile='compression')
    assert client.max_output_tokens() == 2560
    if effort is None:
        assert client.thinking_parameters()[0] == {}


def test_legacy_and_unknown_levels(make_client):
    client = make_client(effort=None)
    assert client.max_output_tokens() == 2560
    client = make_client(effort='unrecognized')
    with pytest.raises(LLMCapabilityError):
        client.max_output_tokens()


def test_sdk_declarations_flags_and_unknown(make_client, monkeypatch):
    sdk = transport.load_sdk()
    monkeypatch.setitem(sdk.model_cost, 'openai/gpt-5.2', {
        'supports_reasoning': True, 'reasoning_effort_levels': ['low', 'high'],
        'supports_xhigh_reasoning_effort': True, 'max_output_tokens': 5000,
    })
    client = make_client('openai', 'gpt-5.2', 'max')
    assert client.reasoning_resolution().effective == 'high'
    assert client.max_output_tokens() == 5000
    monkeypatch.setitem(sdk.model_cost, 'openai/gpt-5.2', {'supports_reasoning': True})
    monkeypatch.setitem(sdk.model_cost, 'gpt-5.2', {'supports_reasoning': True})
    client = make_client('openai', 'gpt-5.2', 'max')
    assert client.reasoning_resolution().effective == 'high'
    assert client.reasoning_resolution().reason == 'incomplete_sdk_levels_conservative_fallback'
    monkeypatch.setitem(sdk.model_cost, 'openai/gpt-5.2', {
        'supports_reasoning': False, 'reasoning_effort_levels': ['max']})
    assert make_client('openai', 'gpt-5.2', 'max').reasoning_resolution().effective is None


@pytest.mark.asyncio
async def test_sampling_conflict_zero_send(make_client, monkeypatch):
    captures = []
    monkeypatch.setattr(transport, 'new_http_transport', lambda: httpx.MockTransport(
        lambda request: captures.append(request)))
    with pytest.raises(LLMCapabilityError):
        await make_client('openai', 'gpt-5.2', 'low', temperature=0.4).chat(
            [{'role': 'user', 'content': 'fixture'}])
    assert captures == []


@pytest.mark.asyncio
async def test_sdk_single_send_denies_retry():
    sends = []
    async def send(request):
        sends.append(request)
        return httpx.Response(503, request=request)
    guard = transport.SingleSendTransport(httpx.MockTransport(send), lambda: None)
    async with httpx.AsyncClient(transport=guard) as client:
        assert (await client.post('https://synthetic.invalid', json={})).status_code == 503
        with pytest.raises(transport.AdditionalSendDenied):
            await client.post('https://synthetic.invalid', json={})
    assert len(sends) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('provider,model', [('google', 'gemini-2.5-pro'),
                                           ('vertex_ai', 'gemini-2.5-pro'),
                                           ('anthropic', 'claude-sonnet-4-5')])
async def test_sdk_budget_translation(make_client, vertex_credentials, monkeypatch, provider, model):
    captures = []
    async def send(request):
        captures.append(json.loads(request.content))
        return httpx.Response(200, json=native_reply(provider, model))
    monkeypatch.setattr(transport, 'new_http_transport', lambda: httpx.MockTransport(send))
    client = make_client(provider, model, 'low', **(vertex_credentials if provider == 'vertex_ai' else {}))
    response = await client.chat([{'role': 'user', 'content': 'fixture'}])
    assert len(captures) == 1
    if provider == 'anthropic':
        assert captures[0]['thinking'] == {'type': 'enabled', 'budget_tokens': 1024}
        assert 'temperature' not in captures[0]
    else:
        assert captures[0]['generationConfig']['thinkingConfig']['thinkingBudget'] == 1024
    assert response.transport_diagnostics['sdk_translated_controls']
    assert not response.transport_diagnostics['thinking_policy']['applied_on_wire']


@pytest.mark.asyncio
@pytest.mark.parametrize('profile,effort', [('chat', None), ('compression', None), ('compression', 'low')])
async def test_legacy_compression_wire(make_client, monkeypatch, profile, effort):
    bodies = []
    async def send(request):
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json=native_reply('google', 'gemini-4-hydrogen'))
    monkeypatch.setattr(transport, 'new_http_transport', lambda: httpx.MockTransport(send))
    client = make_client('google', 'gemini-4-hydrogen', effort, profile=profile)
    await client.chat([{'role': 'user', 'content': 'fixture'}])
    assert len(bodies) == 1
    assert transport.control_fields(bodies[0])['max_tokens'] == 2560
    if effort is None:
        assert 'thinkingConfig' not in bodies[0]['generationConfig']


@pytest.mark.asyncio
async def test_actual_sdk_http_error_has_no_hidden_retry(make_client, monkeypatch):
    requests, reserves = [], []
    async def send(request):
        requests.append(request)
        return httpx.Response(503, json={'error': {'message': 'synthetic outage', 'type': 'server_error'}})
    monkeypatch.setattr(transport, 'new_http_transport', lambda: httpx.MockTransport(send))
    route = transport.resolve_route(make_client('openai', 'gpt-5.2', 'low'))
    with pytest.raises(httpx.HTTPStatusError):
        await transport.completion(route, messages=[{'role': 'user', 'content': 'fixture'}],
                                   parameters={'reasoning_effort': 'low', 'max_tokens': 6656},
                                   timeout=5, reserve=lambda: reserves.append(1))
    assert len(requests) == len(reserves) == 1


def test_explicit_declared_max_wins(make_client, monkeypatch):
    sdk = transport.load_sdk()
    monkeypatch.setitem(sdk.model_cost, 'openai/gpt-5.2', {
        'supports_reasoning': True, 'reasoning_effort_levels': ['low', 'high', 'xhigh', 'max'],
        'supports_xhigh_reasoning_effort': True, 'supports_max_reasoning_effort': True})
    resolved = make_client('openai', 'gpt-5.2', 'max').reasoning_resolution()
    assert resolved.effective == 'max'
    assert resolved.reason == 'sdk_declared'
