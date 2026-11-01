"""Real pinned SDK, synthetic wire only; never replace acompletion."""
import asyncio
import copy
import json

import httpx
import pytest

from app.core.config import Settings
from app.engine.llm_client import LLMClient, LLMUnavailableError, bounded_llm_task, task_attempt_metadata
from app.engine import litellm_transport as sdk


ROUTES = [
    ('openai', 'gpt-4o', 'https://api.openai.com/v1/chat/completions'),
    ('google', 'gemini-2.5-flash', 'https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent'),
    ('xai', 'grok-4.3', 'https://api.x.ai/v1/chat/completions'),
    ('openrouter', 'nvidia/nemotron-3-ultra-550b-a55b:free', 'https://openrouter.ai/api/v1/chat/completions'),
    ('anthropic', 'claude-sonnet-4-5', 'https://api.anthropic.com/v1/messages'),
    ('openai_compatible', 'vendor/model-with-slash', 'https://compatible.invalid/v1/chat/completions'),
]


def native(provider, text=' untouched ', usage=True, finish=None):
    if provider in ('google', 'gemini'):
        value = {'candidates': [{'content': {'parts': [{'text': 'hidden', 'thought': True}, {'text': text}]},
                                 'finishReason': finish or 'STOP'}], 'modelVersion': 'gemini-reported'}
        if usage is True:
            value['usageMetadata'] = {'promptTokenCount': 11, 'candidatesTokenCount': 12,
                                      'totalTokenCount': 30, 'thoughtsTokenCount': 7, 'cachedContentTokenCount': 0}
        elif usage is None:
            value['usageMetadata'] = None
        return value
    if provider == 'anthropic':
        value = {'id': 'msg_synthetic', 'type': 'message', 'role': 'assistant', 'model': 'claude-reported',
                 'content': [{'type': 'text', 'text': text}], 'stop_reason': finish or 'end_turn'}
        if usage is True:
            value['usage'] = {'input_tokens': 11, 'output_tokens': 12, 'cache_read_input_tokens': 0}
        elif usage is None:
            value['usage'] = None
        return value
    value = {'id': 'synthetic', 'object': 'chat.completion', 'created': 1, 'model': 'reported',
             'choices': [{'index': 0, 'message': {'role': 'assistant', 'content': text}, 'finish_reason': finish or 'stop'}]}
    if usage is True:
        value['usage'] = {'prompt_tokens': 11, 'completion_tokens': 12, 'total_tokens': 30,
                          'prompt_tokens_details': {'cached_tokens': 0}, 'completion_tokens_details': {'reasoning_tokens': 7}}
    elif usage is None:
        value['usage'] = None
    return value


def make(provider='openai', model='gpt-4o', **overrides):
    values = dict(provider=provider, model=model, api_key='synthetic-account-key', provider_account_id='account',
                  min_output_tokens=300, circuit_breaker_enabled=False)
    if provider == 'openai_compatible':
        values['base_url'] = 'https://compatible.invalid/v1'
    values.update(overrides)
    return LLMClient(Settings(_env_file=None, llm_mock=False, llm_transport='litellm'), overrides=values)


@pytest.fixture
def wire(monkeypatch):
    requests, outcomes = [], []
    async def send(request):
        requests.append(request)
        outcome = outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome
    monkeypatch.setattr(sdk, 'new_http_transport', lambda: httpx.MockTransport(send))
    monkeypatch.setattr(LLMClient, '_record_usage', lambda *a, **k: None)
    monkeypatch.setattr(LLMClient, '_record_transport_failure', lambda *a, **k: None)
    monkeypatch.setattr(LLMClient, 'retry_delay_seconds', lambda *a, **k: 0)
    return requests, outcomes


@pytest.mark.parametrize('provider,model,url', ROUTES)
@pytest.mark.parametrize('usage', [True, False, None])
async def test_real_sdk_routes_and_original_usage(wire, provider, model, url, usage):
    requests, outcomes = wire
    outcomes.append(httpx.Response(200, json=native(provider, usage=usage)))
    messages = [{'role': 'system', 'content': 'Keep this system.'}, {'role': 'user', 'content': 'Keep this user.'}]
    original = copy.deepcopy(messages)
    response = await make(provider, model).chat(messages)
    assert len(requests) == 1 and str(requests[0].url) == url
    assert not requests[0].url.query
    auth = requests[0].headers
    assert 'synthetic-account-key' in (auth.get('authorization') or auth.get('x-api-key') or auth.get('x-goog-api-key'))
    assert messages == original
    payload = json.loads(requests[0].content)
    if provider not in ('google', 'gemini'):
        assert payload['model'] == model
    assert response.content == ('untouched' if provider == 'google' else ' untouched ')
    assert response.prompt_tokens == (11 if usage is True else None)
    assert response.completion_tokens == (12 if usage is True else None)
    assert response.total_tokens == (30 if usage is True and provider != 'anthropic' else None)
    assert response.cached_tokens == (0 if usage is True else None)
    assert response.reported_model_version is not None
    assert response.generation_attempts == 1


@pytest.mark.parametrize('provider,model,url', ROUTES)
@pytest.mark.parametrize('failure', [500, 422, 'timeout', 'connect', 'protocol'])
async def test_retry_owner_no_hidden_sends(wire, provider, model, url, failure):
    requests, outcomes = wire
    errors = {'timeout': httpx.ReadTimeout('private'), 'connect': httpx.ConnectError('private'),
              'protocol': httpx.RemoteProtocolError('private')}
    outcomes.append(httpx.Response(failure, json={'error': {'message': 'private'}}, headers={'retry-after': '0'})
                    if isinstance(failure, int) else errors[failure])
    if failure == 422:
        with pytest.raises(LLMUnavailableError) as exc:
            await make(provider, model).chat([{'role': 'user', 'content': 'hello'}])
        assert exc.value.http_status == 422 and exc.value.transport_attempts == 1
        assert 'private' not in str(exc.value)
        assert len(requests) == 1
    else:
        outcomes.append(httpx.Response(200, json=native(provider)))
        response = await make(provider, model).chat([{'role': 'user', 'content': 'hello'}])
        assert response.generation_attempts == 2 and len(requests) == 2


@pytest.mark.parametrize('overrides', [dict(api_key=None), dict(provider='unknown'),
    dict(provider='google', base_url='https://wrong.invalid/v1'),
    dict(provider='openai_compatible', base_url=None), dict(base_url='https://api.openai.com/v1?key=secret')])
async def test_preflight_zero_budget_and_no_circuit(wire, overrides):
    @bounded_llm_task
    async def run():
        with pytest.raises(ValueError):
            await make(**overrides).chat([{'role': 'user', 'content': 'hello'}])
        assert task_attempt_metadata()['network_attempts'] == 0
    await run()
    assert wire[0] == []


@pytest.mark.parametrize('provider,model,url', ROUTES)
async def test_controls_schema_and_messages_are_not_mutated(wire, provider, model, url):
    requests, outcomes = wire
    outcomes.append(httpx.Response(200, json=native(provider)))
    obj = make(provider, model, temperature=0, top_p=0, thinking_budget=0)
    obj.settings.chat_thinking_policy = 'off_when_supported'
    messages = [{'role': 'system', 'content': 'Original system.'},
                {'role': 'user', 'content': 'First user.'},
                {'role': 'assistant', 'content': 'Original assistant.'},
                {'role': 'user', 'content': 'Second user.'}]
    schema = {'type': 'json_object', 'name': 'test', 'strict': False,
              'schema': {'type': 'object', 'properties': {'text': {'type': 'string'}},
                         'required': ['text'], 'additionalProperties': False}}
    before = copy.deepcopy((messages, schema))
    await obj.chat(messages, response_format=schema, conversation_id='private-room')
    assert (messages, schema) == before
    payload = json.loads(requests[0].content)
    assert not payload.get('tools')
    if provider == 'google':
        conf = payload['generationConfig']
        assert conf['temperature'] == conf['top_p'] == conf['thinkingConfig']['thinkingBudget'] == 0
        assert conf['max_output_tokens'] == 600
        assert [p['parts'][0]['text'] for p in payload['contents']] == [m['content'] for m in messages[1:]]
        assert payload['system_instruction']['parts'][0]['text'] == messages[0]['content']
    elif provider == 'anthropic':
        assert payload['temperature'] == payload['top_p'] == 0
        assert payload['messages'] == [{'role': m['role'], 'content': [{'type': 'text', 'text': m['content']}]}
                                       for m in messages[1:]]
        assert payload['system'] == [{'type': 'text', 'text': messages[0]['content']}]
    else:
        assert payload['temperature'] == payload['top_p'] == 0
        assert payload['messages'] == messages
        assert payload['response_format']['json_schema']['strict'] is False
        assert payload['response_format']['json_schema']['schema'] == schema['schema']
    if provider == 'xai':
        import hashlib
        assert requests[0].headers['x-grok-conv-id'] == hashlib.sha256(b'lorechat:private-room').hexdigest()


@pytest.mark.parametrize('provider,model,url', ROUTES)
async def test_tagged_no_schema_and_off_controls(wire, provider, model, url):
    requests, outcomes = wire
    outcomes.append(httpx.Response(200, json=native(provider, '<m c="C1">hi</m>')))
    obj = make(provider, model)
    obj.settings.chat_thinking_policy = 'off_when_supported'
    await obj.chat([{'role': 'user', 'content': 'No added prompts.'}])
    payload = json.loads(requests[0].content)
    assert 'response_format' not in payload and 'tools' not in payload
    assert 'responseMimeType' not in payload.get('generationConfig', {})
    if provider == 'openrouter':
        assert payload['reasoning'] == {'enabled': False}
    elif provider == 'xai':
        assert payload['reasoning_effort'] == 'none'
    elif provider == 'google':
        assert 'thinkingConfig' not in payload['generationConfig']


@pytest.mark.parametrize('provider,model,url', ROUTES)
async def test_malformed_success_is_not_transport_success(wire, provider, model, url):
    wire[1].append(httpx.Response(200, content=b'{bad'))
    with pytest.raises(sdk.ResponseEvidenceError):
        await make(provider, model).chat([{'role': 'user', 'content': 'hi'}])
    assert len(wire[0]) == 1


@pytest.mark.parametrize('data', [
    {'candidates': [], 'usageMetadata': {'promptTokenCount': 0}},
    {'promptFeedback': {'blockReason': 'PROHIBITED_CONTENT', 'blockReasonMessage': 'secret'}},
    native('google', '', finish='MAX_TOKENS'),
])
async def test_gemini_empty_blocked_diagnostics(wire, data):
    wire[1].append(httpx.Response(200, json=data))
    obj = make('google', 'gemini-2.5-flash')
    obj.profile = 'compression'
    response = await obj.chat([{'role': 'user', 'content': 'hi'}])
    assert response.content == ''
    assert response.provider_diagnostics is not None
    assert 'secret' not in json.dumps(response.provider_diagnostics)
    assert response.finish_reason == (data.get('candidates') or [{}])[0].get('finishReason')
    assert len(wire[0]) == 1


async def test_guard_preserves_first_limit_and_bounded_capture():
    sends, reservations = [], []
    raw = native('google', 'prefix', finish='MAX_TOKENS')
    async def send(request):
        sends.append(request)
        return httpx.Response(200, json=raw)
    guard = sdk.SingleSendTransport(httpx.MockTransport(send), lambda: reservations.append(1))
    request = httpx.Request('POST', 'https://synthetic.invalid')
    await guard.handle_async_request(request)
    with pytest.raises(sdk.AdditionalSendDenied):
        await guard.handle_async_request(request)
    assert guard.response.json() == raw and len(sends) == len(reservations) == 1
    await guard.aclose()
    guard = sdk.SingleSendTransport(httpx.MockTransport(send), lambda: None)
    guard.max_response_bytes = 4
    with pytest.raises(sdk.ResponseEvidenceError):
        await guard.handle_async_request(request)
    assert guard.response is None
    await guard.aclose()


async def test_shared_budget_and_retry_after(wire, monkeypatch):
    requests, outcomes = wire
    outcomes.extend([httpx.Response(429, json={'error': {'message': 'private'}}, headers={'retry-after': '7'}),
                     httpx.Response(200, json=native('openai', 'partial', finish='length')),
                     httpx.Response(503, json={'error': {'message': 'private'}})])
    delays = []
    original = LLMClient.retry_after_seconds
    def delay(self, exc, **kw):
        delays.append(original(self, exc))
        return 0
    monkeypatch.setattr(LLMClient, 'retry_delay_seconds', delay)
    from app.engine.llm_client import LLMAttemptBudgetError
    @bounded_llm_task
    async def run():
        obj = make()
        assert (await obj.chat([{'role': 'user', 'content': 'hi'}])).finish_reason == 'length'
        with pytest.raises(LLMUnavailableError):
            await obj.chat([{'role': 'user', 'content': 'repair'}])
        with pytest.raises(LLMAttemptBudgetError):
            await obj.chat([{'role': 'user', 'content': 'no fourth'}])
        assert task_attempt_metadata()['network_attempts'] == 3
    await run()
    assert len(requests) == 3 and delays == [7]


async def test_concurrent_accounts_and_circuit_zero(wire):
    requests, outcomes = wire
    outcomes.extend([httpx.Response(200, json=native('openai')) for _ in range(2)])
    await asyncio.gather(*(make('openai_compatible', api_key='key-' + n, provider_account_id=n,
                               base_url='https://' + n + '.invalid/v1').chat([{'role': 'user', 'content': n}])
                          for n in ('one', 'two')))
    assert {(r.url.host, r.headers['authorization']) for r in requests} == {
        ('one.invalid', 'Bearer key-one'), ('two.invalid', 'Bearer key-two')}
    obj = make(circuit_breaker_enabled=True, circuit_failure_threshold=1)
    obj.record_circuit_failure()
    from app.engine.llm_client import CircuitOpenError
    try:
        with pytest.raises(CircuitOpenError) as exc:
            await obj.chat([{'role': 'user', 'content': 'hi'}])
        assert exc.value.transport_attempts == 0 and len(requests) == 2
    finally:
        obj.reset_circuit_breakers()


def test_separate_event_loops(wire):
    for _ in range(2):
        wire[1].append(httpx.Response(200, json=native('openai')))
        asyncio.run(make().chat([{'role': 'user', 'content': 'hi'}]))
    assert len(wire[0]) == 2


@pytest.mark.parametrize('provider,model,url', ROUTES)
async def test_zero_invalid_and_missing_native_counts(wire, provider, model, url):
    data = native(provider)
    if provider == 'google':
        data['usageMetadata'] = {'promptTokenCount': 0, 'candidatesTokenCount': -1,
                                 'totalTokenCount': True, 'thoughtsTokenCount': '7'}
    elif provider == 'anthropic':
        data['usage'] = {'input_tokens': 0, 'output_tokens': -1, 'cache_read_input_tokens': True}
    else:
        data['usage'] = {'prompt_tokens': 0, 'completion_tokens': -1, 'total_tokens': True,
                         'completion_tokens_details': {'reasoning_tokens': '7'}}
    wire[1].append(httpx.Response(200, json=data))
    result = await make(provider, model).chat([{'role': 'user', 'content': 'hi'}])
    assert result.prompt_tokens == 0
    assert result.completion_tokens is result.total_tokens is result.reasoning_tokens is result.cached_tokens is None


async def test_sdk_preflight_unsupported_effort_does_not_spend_budget(wire):
    @bounded_llm_task
    async def run():
        with pytest.raises(sdk.TransportConfigurationError):
            await make('xai', 'grok-2', reasoning_effort='high').chat([{'role': 'user', 'content': 'hi'}])
        assert task_attempt_metadata()['network_attempts'] == 0
    await run()
    assert wire[0] == []


async def test_real_sdk_then_simulated_continuation_preserves_first(wire, monkeypatch):
    wire[1].append(httpx.Response(200, json=native('google', 'original prefix', finish='MAX_TOKENS')))
    module = sdk.load_sdk()
    original = module.acompletion
    async def continue_after_completion(**kwargs):
        await original(**kwargs)
        # Simulate a pinned/future SDK continuation, below the same SDK driver.
        await kwargs['client'].client.post('https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent')
    monkeypatch.setattr(module, 'acompletion', continue_after_completion)
    result = await make('google', 'gemini-2.5-flash').chat([{'role': 'user', 'content': 'hi'}])
    assert result.content == 'original prefix' and result.finish_reason == 'MAX_TOKENS'
    assert len(wire[0]) == result.generation_attempts == 1


async def test_post_capture_sdk_bug_uses_authoritative_native_proof(wire, monkeypatch):
    wire[1].append(httpx.Response(200, json=native('google')))
    module = sdk.load_sdk()
    original = module.acompletion
    async def broken_after_completion(**kwargs):
        await original(**kwargs)
        raise RuntimeError('synthetic SDK bug')
    monkeypatch.setattr(module, 'acompletion', broken_after_completion)
    # Supersedes the frozen negative: native proof, not SDK exception class,
    # now determines admission. No message/class text is persisted.
    result = await make('google', 'gemini-2.5-flash').chat([{'role': 'user', 'content': 'hi'}])
    assert result.content == 'untouched'
    assert result.transport_diagnostics['sdk_response_status'] == 'native_validated_after_sdk_error'
    assert 'synthetic SDK bug' not in json.dumps(result.transport_diagnostics)
    assert len(wire[0]) == 1


async def test_cancellation_closes_owned_transport(monkeypatch):
    started, closed = asyncio.Event(), asyncio.Event()
    class Pending(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            started.set()
            await asyncio.Event().wait()
        async def aclose(self):
            await asyncio.sleep(0)
            closed.set()
    monkeypatch.setattr(sdk, 'new_http_transport', Pending)
    task = asyncio.create_task(make().chat([{'role': 'user', 'content': 'hi'}]))
    await asyncio.wait_for(started.wait(), timeout=10)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert closed.is_set()


async def test_xai_default_strict_and_modern_gemini_omissions(wire):
    wire[1].append(httpx.Response(200, json=native('xai')))
    await make('xai', 'grok-4.3').chat([{'role': 'user', 'content': 'hi'}],
        response_format={'type': 'json_object', 'schema': {'type': 'object'}})
    assert json.loads(wire[0][0].content)['response_format']['json_schema']['strict'] is True
    wire[1].append(httpx.Response(200, json=native('google')))
    result = await make('google', 'gemini-3.8-flash', temperature=0, top_p=0, top_k=0,
                        thinking_level='low').chat([{'role': 'user', 'content': 'hi'}])
    config = json.loads(wire[0][1].content)['generationConfig']
    # User-approved contract supersedes strict SDK omission; existing app
    # capability omission remains distinct from the observed SDK default.
    assert config['temperature'] == 1.0
    assert all(k not in config for k in ('top_p', 'top_k'))
    assert config['thinkingConfig']['thinkingLevel'] == 'low'
    diag = result.transport_diagnostics
    assert set(diag['application_omitted_parameters']) == {'temperature', 'topP', 'topK'}
    assert set(diag['omitted_parameters']) == {'topP', 'topK'}
    assert diag['serialized_wire_controls']['temperature'] == 1.0
    assert diag['sdk_added_defaults'] == {'temperature': 1.0}
    assert diag['requested_controls']['temperature'] == 0
    assert 'temperature' not in diag['application_controls']


@pytest.mark.parametrize('provider,model,url', ROUTES)
@pytest.mark.parametrize('usage', ['wrong-container', [1], True, 1])
async def test_usage_container_is_independent_of_prose(wire, provider, model, url, usage):
    data = native(provider)
    data['usageMetadata' if provider == 'google' else 'usage'] = usage
    wire[1].append(httpx.Response(200, json=data))
    result = await make(provider, model).chat([{'role': 'user', 'content': 'hi'}])
    assert result.content.strip() == 'untouched'
    assert result.prompt_tokens is result.completion_tokens is result.total_tokens is None
    assert len(wire[0]) == 1


@pytest.mark.parametrize('provider,model,url', ROUTES)
@pytest.mark.parametrize('capture', ['before', 'valid', 'malformed', 'envelope', 'status', 'unsafe', 'finish'])
async def test_controlled_sdk_error_native_admission_matrix(wire, monkeypatch, provider, model, url, capture):
    data = native(provider)
    if capture == 'malformed':
        data = {'candidates': 'invalid', 'promptFeedback': {}} if provider == 'google' else {}
    elif capture == 'envelope':
        data['error'] = {'message': 'PRIVATE'}
    elif capture == 'unsafe':
        if provider == 'google':
            data['candidates'][0]['content']['parts'].append({'inlineData': {'data': 'PRIVATE'}})
        elif provider == 'anthropic':
            data['content'].append({'type': 'tool_use'})
        else:
            data['choices'][0]['message']['tool_calls'] = [{'id': 'PRIVATE'}]
    elif capture == 'finish':
        if provider == 'google':
            data['candidates'][0]['finishReason'] = 5
        elif provider == 'anthropic':
            data['stop_reason'] = 5
        else:
            data['choices'][0]['finish_reason'] = 5
    wire[1].append(httpx.Response(422 if capture == 'status' else 200, json=data))
    module = sdk.load_sdk()
    original = module.acompletion
    async def controlled(**kwargs):
        if capture != 'before':
            try:
                await original(**kwargs)
            except Exception:
                pass  # Controlled error replacement, never production behavior.
        raise RuntimeError('PRIVATE SDK exception')
    monkeypatch.setattr(module, 'acompletion', controlled)
    @bounded_llm_task
    async def run():
        if capture == 'valid':
            result = await make(provider, model).chat([{'role': 'user', 'content': 'PRIVATE PROMPT'}])
            assert result.content.strip() == 'untouched'
            assert result.transport_diagnostics['sdk_response_status'] == 'native_validated_after_sdk_error'
            assert 'PRIVATE' not in json.dumps(result.transport_diagnostics)
        else:
            kind = sdk.TransportConfigurationError if capture == 'before' else LLMUnavailableError if capture == 'status' else sdk.ResponseEvidenceError
            with pytest.raises(kind) as exc:
                await make(provider, model).chat([{'role': 'user', 'content': 'PRIVATE PROMPT'}])
            assert 'PRIVATE' not in str(exc.value)
        assert task_attempt_metadata()['network_attempts'] == (0 if capture == 'before' else 1)
    await run()
    assert len(wire[0]) == (0 if capture == 'before' else 1)


@pytest.mark.parametrize('data', [{}, {'candidates': None}, {'candidates': [None]},
    {'candidates': [{}]}, {'candidates': [{'content': 'bad'}]},
    {'candidates': [{'content': {'parts': 'bad'}}]}, {'promptFeedback': {}},
    {'candidates': [], 'error': {}}, {'candidates': 'bad', 'promptFeedback': {'blockReason': 'SAFETY'}}])
async def test_malformed_gemini_not_empty_diagnostic(wire, data):
    wire[1].append(httpx.Response(200, json=data))
    obj = make('google', 'gemini-2.5-flash')
    obj.profile = 'compression'
    with pytest.raises(sdk.ResponseEvidenceError):
        await obj.chat([{'role': 'user', 'content': 'hi'}])
    assert len(wire[0]) == 1


@pytest.mark.parametrize('profile', ['chat', 'compression'])
@pytest.mark.parametrize('provider,model,url', ROUTES)
async def test_controls_observed_at_real_guard_and_resolved_endpoint(wire, provider, model, url, profile):
    wire[1].append(httpx.Response(200, json=native(provider)))
    obj = make(provider, model, temperature=0, top_p=0,
               **({'thinking_budget': 0} if provider == 'google' else {}))
    obj.profile = profile
    obj.purpose = 'chat_generation' if profile == 'chat' else 'scene_compression'
    obj.settings.chat_thinking_policy = 'off_when_supported'
    # Native registry must win over an irrelevant legacy local base.
    obj.settings.llm_base_url = 'http://stale-legacy.invalid/v1'
    result = await obj.chat([{'role': 'user', 'content': 'PRIVATE PROMPT'}])
    payload = json.loads(wire[0][0].content)
    diag = result.transport_diagnostics
    assert diag is not None
    controls = sdk.control_fields(payload)
    assert controls == diag['serialized_wire_controls']
    assert controls['temperature'] == controls['top_p'] == 0
    assert diag['sdk_added_defaults'].get('temperature') is None
    assert diag['requested_controls']['temperature'] == diag['application_controls']['temperature'] == 0
    assert obj.base_url() == sdk.resolve_route(obj).base_url
    if provider == 'google':
        assert controls['thinkingConfig.thinkingBudget'] == 0
        assert diag['thinking_policy']['explicit_parameters_on_wire'] == ['thinkingConfig.thinkingBudget']
    if provider in {'xai', 'openrouter'}:
        name = 'reasoning_effort' if provider == 'xai' else 'reasoning.enabled'
        assert (name in controls) == (profile == 'chat')
        assert diag['thinking_policy']['applied_on_wire'] == (profile == 'chat')
        if profile == 'chat':
            assert controls[name] == ('none' if provider == 'xai' else False)
    assert 'PRIVATE' not in json.dumps(diag)


async def test_official_default_capability_gate_not_stale_legacy_base(wire):
    for provider, model, expected in [('xai', 'grok-4.3', 'xai_grok'), ('openai', 'gpt-5', 'openai_reasoning')]:
        obj = make(provider, model)
        obj.settings.llm_base_url = 'http://stale.invalid/v1'
        wire[1].append(httpx.Response(200, json=native(provider)))
        result = await obj.chat([{'role': 'user', 'content': 'hi'}])
        assert result.transport_diagnostics['policy'] == expected
        controls = result.transport_diagnostics['serialized_wire_controls']
        if provider == 'xai':
            assert controls['temperature'] == 1 and controls['top_p'] == .95
        else:
            assert 'temperature' not in controls
            assert json.loads(wire[0][-1].content)['max_completion_tokens'] == 600
    obj = make('openai_compatible', 'grok-4.3')
    obj.settings.chat_thinking_policy = 'off_when_supported'
    wire[1].append(httpx.Response(200, json=native('openai_compatible')))
    result = await obj.chat([{'role': 'user', 'content': 'hi'}])
    assert result.transport_diagnostics['policy'] == 'generic_compatible'
    assert 'reasoning_effort' not in result.transport_diagnostics['serialized_wire_controls']


@pytest.mark.parametrize('provider,model,url', ROUTES)
async def test_original_response_bytes_and_missing_total_are_not_rewritten(wire, provider, model, url):
    data = native(provider)
    usage = data['usageMetadata' if provider == 'google' else 'usage']
    usage.pop('totalTokenCount' if provider == 'google' else 'total_tokens', None)
    original = json.dumps(data, indent=2, ensure_ascii=False).encode()
    wire[1].append(httpx.Response(200, content=original))
    obj = make(provider, model)
    route = sdk.resolve_route(obj)
    captured = await sdk.completion(route, messages=[{'role': 'user', 'content': 'hi'}], parameters={}, timeout=10, reserve=lambda: None)
    assert captured.content == original and captured.text == original.decode()
    observation, observed_data = sdk.native_observation(captured, route)
    assert observed_data == data and observation['total_tokens'] is None
    assert len(wire[0]) == 1


@pytest.mark.parametrize('provider,model,url', ROUTES)
@pytest.mark.parametrize('extra', [b'"duplicate":null,"duplicate":null,', b'"unexpected":NaN,'])
async def test_native_ambiguous_or_non_json_grammar_rejected(wire, provider, model, url, extra):
    # Insert the invalid member into an otherwise valid native reply so ordinary
    # shape checks cannot hide permissive JSON decoding.
    valid = json.dumps(native(provider)).encode()

    wire[1].append(httpx.Response(200, content=b'{' + extra + valid[1:]))
    with pytest.raises(sdk.ResponseEvidenceError):
        await make(provider, model).chat([{'role': 'user', 'content': 'hi'}])
    assert len(wire[0]) == 1


def test_transport_selector_validated():
    assert Settings(_env_file=None).llm_transport == 'legacy_http'
    with pytest.raises(ValueError):
        Settings(_env_file=None, llm_transport='typo')
