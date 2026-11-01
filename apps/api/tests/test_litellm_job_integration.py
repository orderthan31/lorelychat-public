"""API job/finalizer proof through the actual SDK and mocked HTTP boundary."""
import asyncio
import json

import httpx
import pytest
from sqlmodel import select

from app.core.config import get_settings
from app.db.models import Message, LLMPromptSnapshot, LLMUsageEvent
from app.engine.llm_client import LLMClient
from app.engine import litellm_transport as sdk
from app.api.conversations import _run_message_generation_job_in_session
from test_free_body_persistence import room_fixture
from test_free_body_prefix_recovery import cut, item
from test_litellm_transport import native, make, wire
from app.engine.character_runtime import CharacterRuntime
from app.db.models import Character
from app.engine.llm_client import LLMUnavailableError, LLMAttemptBudgetError, CircuitOpenError
from test_bounded_structured_repair import TRUNCATED, VALID
from test_combined_command_runtime import encode, command, mode
from app.engine import command_overlay as co


@pytest.mark.parametrize('metadata_case', ['normal', 'invalid_usage', 'post_sdk_error'])
def test_sdk_job_retains_complete_prefix_atomically_and_privately(client, session, engine, monkeypatch, metadata_case):
    monkeypatch.setenv('CHAT_RESPONSE_PROTOCOL', 'free_body_json')
    get_settings.cache_clear()
    monkeypatch.setattr('app.api.conversations._start_message_generation_worker', lambda *a: None)
    monkeypatch.setattr('app.engine.llm_client.engine', engine)
    room, cast = room_fixture(client)
    texts = ['<a>First movement.</a>First.<t>Private possibility.</t>', 'Narrator sees a tree.', '<a>Waits.</a>Last.']
    raw = cut(','.join(item(who, text) for who, text in zip(['C1', 'N', 'C2'], texts)))
    calls, finalizations = [], []
    from app.services import conversation_service
    original_finalize = conversation_service.finalize_message_generation_turn
    def finalize(*args, **kwargs):
        result = original_finalize(*args, **kwargs)
        finalizations.append(result)
        return result
    monkeypatch.setattr(conversation_service, 'finalize_message_generation_turn', finalize)
    async def send(request):
        calls.append(request)
        response = native('google', raw, finish='MAX_TOKENS')
        response['usageMetadata'] = {'promptTokenCount': 101, 'candidatesTokenCount': 202, 'totalTokenCount': 303}
        if metadata_case == 'invalid_usage':
            response['usageMetadata'] = {'promptTokenCount': True, 'candidatesTokenCount': -1, 'thoughtsTokenCount': 'PRIVATE'}
        return httpx.Response(200, json=response)
    monkeypatch.setattr(sdk, 'new_http_transport', lambda: httpx.MockTransport(send))
    if metadata_case == 'post_sdk_error':
        module = sdk.load_sdk()
        original_completion = module.acompletion
        async def post_error(**kwargs):
            await original_completion(**kwargs)
            raise RuntimeError('PRIVATE normalization error')
        monkeypatch.setattr(module, 'acompletion', post_error)
    # Only configure the synthetic account; do not stub chat/SDK/parser/finalizer.
    original_init = LLMClient.__init__
    def configured(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        self.settings = self.settings.model_copy(update={'llm_mock': False, 'llm_transport': 'litellm'})
        self.overrides = {**self.overrides, 'provider': 'gemini', 'model': 'gemini-2.5-flash',
                          'api_key': 'synthetic-account-key', 'provider_account_id': 'synthetic-account'}
    monkeypatch.setattr(LLMClient, '__init__', configured)
    response = client.post(f'/conversations/{room}/messages/jobs', json={
        'speaker_type': 'user', 'speaker_id': 'synthetic', 'content': 'Continue.'})
    assert response.status_code == 200, response.text
    job_id = response.json()['job']['id']
    asyncio.run(_run_message_generation_job_in_session(session, job_id))
    status = client.get(f'/conversations/{room}/generation-jobs/{job_id}').json()
    messages = client.get(f'/conversations/{room}/messages').json()
    assert status['status'] == 'completed', status
    assert len(calls) == len(finalizations) == 1
    rows = session.exec(select(Message).where(Message.generation_job_id == job_id).order_by(Message.reply_index)).all()
    assert len(rows) == 3
    assert [r.id for r in rows] == status['generated_message_ids']
    assert [r.reply_index for r in rows] == [0, 1, 2]
    assert [r.metadata_['raw_body'] for r in rows] == texts
    assert [r.speaker_id for r in rows] == [cast[0]['id'], 'storyteller', cast[1]['id']]
    assert rows[0].thought == 'Private possibility.'
    public = json.dumps([status, messages, response.json()])
    assert 'output_limit_recovery' not in public and 'complete_reply_prefix' not in public
    assert 'sdk_response_status' not in public and 'PRIVATE normalization' not in public
    snapshots = session.exec(select(LLMPromptSnapshot).where(LLMPromptSnapshot.conversation_id == room)).all()
    usage = session.exec(select(LLMUsageEvent).where(LLMUsageEvent.conversation_id == room)).all()
    assert len(snapshots) == len(usage) == 1
    assert snapshots[0].response_preview == raw
    marker = snapshots[0].metadata_['output_limit_recovery']
    assert marker == usage[0].metadata_['output_limit_recovery']
    assert marker['retained_count'] == 3 and marker['original_finish_reason'] == 'MAX_TOKENS'
    expected_completion = None if metadata_case == 'invalid_usage' else 202
    assert usage[0].completion_tokens == snapshots[0].completion_tokens == expected_completion
    assert usage[0].total_tokens == (None if metadata_case == 'invalid_usage' else 303)
    assert usage[0].metadata_['network_attempts'] == 1
    asyncio.run(_run_message_generation_job_in_session(session, job_id))
    assert len(calls) == len(finalizations) == 1


@pytest.mark.parametrize('scenario', ['repair_then_fallback', 'fallback_then_repair', 'exhausted', 'unconfigured'])
async def test_sdk_runtime_shared_budget_and_explicit_fallback(wire, monkeypatch, scenario):
    monkeypatch.setenv('CHAT_RESPONSE_PROTOCOL', 'legacy_json')
    get_settings.cache_clear()
    primary = make('google', 'gemini-2.5-flash', retry_attempts=2 if scenario == 'exhausted' else 1)
    fallback = make('xai', 'grok-4.3', retry_attempts=1)
    if scenario == 'repair_then_fallback':
        outcomes = [httpx.Response(200, json=native('google', TRUNCATED, finish='MAX_TOKENS')),
                    httpx.Response(503), httpx.Response(200, json=native('xai', VALID))]
        hosts = ['generativelanguage.googleapis.com'] * 2 + ['api.x.ai']
    elif scenario == 'fallback_then_repair':
        outcomes = [httpx.Response(503), httpx.Response(200, json=native('xai', TRUNCATED, finish='length')),
                    httpx.Response(200, json=native('xai', VALID))]
        hosts = ['generativelanguage.googleapis.com'] + ['api.x.ai'] * 2
    elif scenario == 'exhausted':
        outcomes = [httpx.Response(503), httpx.Response(200, json=native('google', TRUNCATED, finish='MAX_TOKENS')), httpx.Response(503)]
        hosts = ['generativelanguage.googleapis.com'] * 3
    else:
        outcomes, hosts, fallback = [httpx.Response(503)], ['generativelanguage.googleapis.com'], None
    wire[1].extend(outcomes)
    writes, snapshots = [], []
    runtime = CharacterRuntime(primary, fallback)
    kwargs = dict(characters=[Character(id='char_1', name='Aria')], recent_messages=[], user_message='next',
                  persist_replies=writes.append, prompt_snapshot_callback=snapshots.append)
    try:
        if scenario in {'exhausted', 'unconfigured'}:
            with pytest.raises(LLMAttemptBudgetError if scenario == 'exhausted' else LLMUnavailableError):
                await runtime.generate_replies(**kwargs)
            assert not writes
        else:
            await runtime.generate_replies(**kwargs)
            assert len(writes) == 1
            assert snapshots[0]['metadata']['fallback_used'] is True
            assert snapshots[0]['metadata']['semantic_attempts'] == 2
        assert [r.url.host for r in wire[0]] == hosts
        assert snapshots[0]['metadata']['network_attempts'] == len(hosts)
    finally:
        get_settings.cache_clear()


@pytest.mark.parametrize('configured_fallback', [False, True])
async def test_sdk_runtime_circuit_zero_primary_sends(wire, configured_fallback):
    primary = make(circuit_breaker_enabled=True, circuit_failure_threshold=1)
    fallback = make('xai', 'grok-4.3') if configured_fallback else None
    primary.record_circuit_failure()
    wire[1].append(httpx.Response(200, json=native('xai', VALID)))
    writes = []
    kwargs = dict(characters=[Character(id='char_1', name='Aria')], recent_messages=[], user_message='next', persist_replies=writes.append)
    try:
        if configured_fallback:
            await CharacterRuntime(primary, fallback).generate_replies(**kwargs)
            assert len(writes) == len(wire[0]) == 1 and wire[0][0].url.host == 'api.x.ai'
        else:
            with pytest.raises(CircuitOpenError):
                await CharacterRuntime(primary).generate_replies(**kwargs)
            assert not writes and not wire[0]
    finally:
        primary.reset_circuit_breakers()


async def test_sdk_runtime_exhausted_budget_zero_new_sends(wire, monkeypatch):
    from app.engine import llm_client
    # Inject an exhausted task allowance at the runtime boundary, not a nested
    # decorated task (each top-level runtime operation deliberately gets its own).
    budget_type = llm_client.LLMAttemptBudget
    monkeypatch.setattr(llm_client, 'LLMAttemptBudget', lambda: budget_type(limit=3, used=3))
    writes = []
    with pytest.raises(LLMAttemptBudgetError):
        await CharacterRuntime(make(), make('xai', 'grok-4.3')).generate_replies(
            characters=[Character(id='char_1', name='Aria')], recent_messages=[], user_message='next', persist_replies=writes.append)
    assert not wire[0] and not writes


@pytest.mark.parametrize('protocol', ['tagged_text', 'free_body_json'])
@pytest.mark.parametrize('provider,model', [('google', 'gemini-2.5-flash'), ('xai', 'grok-4.3')])
@pytest.mark.parametrize('finish', ['STOP', 'LENGTH'])
async def test_sdk_combined_overlay_auxiliary_proof(wire, mode, protocol, provider, model, finish):
    mode(protocol)
    raw = encode(protocol)
    if finish == 'LENGTH':
        raw = raw.split('Ready.')[0] + 'Partial panel'
    wire[1].append(httpx.Response(200, json=native(provider, raw, usage=None, finish=finish)))
    snapshots, writes = [], []
    obj = make(provider, model, min_output_tokens=1280)
    result = await CharacterRuntime(obj).generate_replies(
        characters=[Character(id='a', name='Aria')], recent_messages=[], user_message='source',
        command_overlay_plan=co.build_plan([command()]), prompt_snapshot_callback=snapshots.append,
        persist_replies=lambda replies: writes.append(replies) or replies)
    assert len(wire[0]) == len(writes) == 1
    assert result[0].text == 'First.\nLast.' and result[0].thought == 'Secret.'
    assert bool(result.command_panels) == (finish == 'STOP')
    assert snapshots[0]['response_preview'] == raw
    assert snapshots[0]['metadata']['network_attempts'] == 1
    payload = json.loads(wire[0][0].content)
    controls = sdk.control_fields(payload)
    assert controls['max_tokens'] == 2560
    if protocol == 'tagged_text':
        assert 'response_format' not in payload and 'tools' not in payload
        assert not {'responseMimeType', 'responseSchema', 'response_mime_type', 'response_schema', 'response_json_schema'} & payload.get('generationConfig', {}).keys()
    else:
        schema = payload['generationConfig']['response_json_schema'] if provider == 'google' else payload['response_format']['json_schema']['schema']
        assert set(schema['properties']) == {'replies', 'panels'}

