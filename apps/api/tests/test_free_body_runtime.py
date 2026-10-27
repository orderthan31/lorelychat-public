"""Synthetic protocol/runtime/real HTTP adapter integration; no provider traffic."""
import json
import httpx
import pytest
from app.core.config import Settings, get_settings
from app.db.models import Character, Message, SceneState
from app.engine.character_runtime import CharacterRuntime, CharacterRuntimeError, generation_control_reserve_tokens
from app.engine.llm_client import LLMClient, LLMResponse, LLMUnavailableError
from app.engine import free_body_protocol as fb, prompts
from app.services import system_prompt_service, conversation_service


@pytest.fixture
def enable(monkeypatch):
    def set_protocol(protocol):
        monkeypatch.setenv('CHAT_RESPONSE_PROTOCOL', protocol)
        get_settings.cache_clear()
    return set_protocol


def characters():
    return [Character(id='char_a', name='Aria'), Character(id='char_b', name='Bora')]


def encode(protocol, body='<a>Turns slowly.</a>Hello<t>Unspoken possibility.</t>Again'):
    if protocol == 'tagged_text':
        return f'<m c="C1">{body}</m>'
    return json.dumps({'replies': [{'who': 'C1', 'text': body}]})


class SequenceClient:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []
        self.outcomes = []
    async def chat(self, messages, response_format=None, **kwargs):
        self.calls.append({'messages': messages, 'format': response_format})
        result = next(self.responses)
        if isinstance(result, Exception):
            raise result
        return result
    def record_response_outcome(self, *args, **kwargs):
        self.outcomes.append(kwargs)


@pytest.mark.asyncio
@pytest.mark.parametrize('protocol', ['tagged_text', 'free_body_json'])
async def test_real_runtime_prepared_harness_retry_character_turn_and_snapshots(enable, protocol):
    enable(protocol)
    client = SequenceClient([LLMResponse(content='<m c="UNKNOWN">bad</m>'), LLMResponse(content=encode(protocol))])
    harness = prompts.build_multi_character_prompt_harness(characters=characters(), recent_messages=[], user_message='source',
        room_cast_roles={'char_b': 'silent'}, min_bubbles=5, max_bubbles=8, provider_type='xai',
        prompt_settings=system_prompt_service.prompt_settings_map())
    snapshots = []
    replies = await CharacterRuntime(client).generate_replies(characters=characters(), recent_messages=[], user_message='source',
        source_speaker_type='character', room_cast_roles={'char_b': 'silent'}, min_bubbles=5, max_bubbles=8,
        prepared_harness=harness, prompt_snapshot_callback=snapshots.append)
    assert len(replies) == 1 and replies[0].character_id == 'char_a'
    assert replies[0].raw_body.startswith('<a>')
    assert len(client.calls) == 2
    for call in client.calls:
        serialized = json.dumps(call['messages'])
        assert '"role": "assistant"' in serialized
        assert 'character-authored' in serialized
        assert 'seven' not in serialized and 'Keep total replies between' not in serialized
        assert 'at most 120' not in serialized and 'Keep dialogue, action, and thought in separate JSON fields' not in serialized
        assert 'C1 = Aria' in serialized and 'C2 =' not in serialized
        assert (call['format'] is None) == (protocol == 'tagged_text')
    assert snapshots[0]['metadata']['parse_status'] == 'retry_success'
    assert snapshots[0]['metadata']['response_protocol'] == protocol
    assert snapshots[0]['metadata']['attempt_diagnostics'][0]['code'] == 'free_body_invalid'
    assert generation_control_reserve_tokens(speaker_type='character', min_bubbles=5, max_bubbles=8, characters=characters()) > 0


@pytest.mark.asyncio
@pytest.mark.parametrize('protocol', ['tagged_text', 'free_body_json'])
async def test_fallback_sticky_and_protocol_stable(enable, protocol):
    enable(protocol)
    primary = SequenceClient([LLMUnavailableError('offline')])
    fallback = SequenceClient([LLMResponse(content='invalid envelope'), LLMResponse(content=encode(protocol))])
    snapshots = []
    replies = await CharacterRuntime(primary, fallback).generate_replies(characters=characters(), recent_messages=[], user_message='next', prompt_snapshot_callback=snapshots.append)
    assert replies and len(primary.calls) == 1 and len(fallback.calls) == 2
    assert snapshots[0]['metadata']['fallback_used'] is True
    assert all(call['format'] == primary.calls[0]['format'] for call in fallback.calls)


@pytest.mark.asyncio
@pytest.mark.parametrize('protocol', ['tagged_text', 'free_body_json'])
@pytest.mark.parametrize('finish', ['LENGTH', 'MAX_TOKENS'])
async def test_complete_looking_truncation_rejected_without_persistence(enable, protocol, finish):
    enable(protocol)
    client = SequenceClient([LLMResponse(content=encode(protocol), finish_reason=finish) for _ in range(2)])
    persisted, snapshots = [], []
    with pytest.raises(CharacterRuntimeError):
        await CharacterRuntime(client).generate_replies(characters=characters(), recent_messages=[], user_message='next',
            persist_replies=persisted.append, prompt_snapshot_callback=snapshots.append)
    assert not persisted
    assert snapshots[0]['metadata']['attempt_diagnostics'][0]['code'] == 'structured_output_truncated'
    assert len(client.calls) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize('provider', ['xai', 'gemini'])
@pytest.mark.parametrize('protocol', ['tagged_text', 'free_body_json'])
async def test_real_provider_http_payloads_with_mock_transport(monkeypatch, enable, protocol, provider):
    enable(protocol)
    captured = []
    def handler(request):
        payload = json.loads(request.content)
        captured.append(payload)
        content = encode(protocol)
        if provider == 'gemini':
            return httpx.Response(200, json={'candidates': [{'content': {'parts': [{'text': content}]}, 'finishReason': 'STOP'}]})
        return httpx.Response(200, json={'choices': [{'message': {'content': content}, 'finish_reason': 'stop'}]})
    real_async_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, 'AsyncClient', lambda *a, **kw: real_async_client(*a, **kw, transport=httpx.MockTransport(handler)))
    client = LLMClient(Settings(_env_file=None, llm_mock=False), overrides={
        'provider': provider, 'provider_type': provider, 'model': 'synthetic-model',
        'base_url': 'https://provider.invalid/v1', 'api_key': 'synthetic-not-a-secret', 'min_output_tokens': 1400,
    })
    # Usage sink is unrelated to payload acceptance and must not touch a runtime DB.
    monkeypatch.setattr(client, '_record_usage', lambda *a, **kw: None)
    monkeypatch.setattr(client, 'record_response_outcome', lambda *a, **kw: None)
    replies = await CharacterRuntime(client).generate_replies(characters=characters(), recent_messages=[], user_message='next')
    assert replies[0].text == 'Hello\nAgain' and len(captured) == 1
    payload = captured[0]
    if protocol == 'tagged_text':
        assert 'response_format' not in payload
        assert 'responseMimeType' not in payload.get('generationConfig', {})
        assert 'responseSchema' not in payload.get('generationConfig', {})
    else:
        schema = payload['generationConfig']['responseSchema'] if provider == 'gemini' else payload['response_format']['json_schema']['schema']
        item = schema['properties']['replies']['items']
        assert item['required'] == ['who', 'text']
        assert set(item['properties']) == {'who', 'text', 'emotion'}
        assert item['properties']['who']['enum'] == ['C1', 'C2', 'N']
        if provider == 'xai':
            assert payload['response_format']['json_schema']['strict'] is False
    assert (payload['generationConfig'].get('maxOutputTokens') if provider == 'gemini' else payload.get('max_tokens')) == client.max_output_tokens()


def test_history_and_compression_use_public_order_once():
    body = '<a>Unique movement.</a>First utterance.<t>PRIVATE SECRET.</t><a>Second movement.</a>Last utterance.'
    reply = fb.parse_response(encode('tagged_text', body), protocol='tagged_text', aliases=fb.speaker_aliases(characters()))[0]
    message = Message(id='m', conversation_id='room', speaker_type='character', speaker_id='char_a',
        content=reply.text, action=reply.action, thought=reply.thought, metadata_=fb.metadata_for_reply(reply))
    scene = SceneState(conversation_id='room', summary='Past summary.')
    for history in [prompts.build_prompt_history([message], context_management_mode='automatic'),
                    conversation_service.build_quality_compression_evidence_source(scene, [message])]:
        assert history.count('Unique movement.') == 1
        assert history.count('First utterance.') == 1
        assert 'PRIVATE SECRET' not in history
        assert history.index('Unique movement') < history.index('First utterance') < history.index('Second movement') < history.index('Last utterance')
    private = prompts.format_message_for_context(message, include_thought=True)
    assert 'private_character_thought=PRIVATE SECRET.' in private


@pytest.mark.asyncio
@pytest.mark.parametrize('protocol', ['tagged_text', 'free_body_json'])
async def test_builtin_mock_and_single_reply_wrapper(enable, protocol):
    enable(protocol)
    reply = await CharacterRuntime(LLMClient(Settings(_env_file=None, llm_mock=True))).generate_reply(
        character=characters()[0], recent_messages=[], user_message='hello')
    assert reply.response_protocol == protocol and reply.character_id == 'char_a'


@pytest.mark.asyncio
@pytest.mark.parametrize('protocol', ['tagged_text', 'free_body_json'])
async def test_shared_http_budget_across_parse_retry_and_fallback(monkeypatch, enable, protocol):
    enable(protocol)
    from app.engine.llm_client import LLMAttemptBudgetError
    requests = []
    def handler(request):
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(200, json={'choices': [{'message': {'content': '<m c="UNKNOWN">bad</m>'}, 'finish_reason': 'stop'}]})
        return httpx.Response(503, json={'error': 'synthetic unavailable'})
    real_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, 'AsyncClient', lambda *a, **kw: real_client(*a, **kw, transport=httpx.MockTransport(handler)))
    clients = []
    for name in ['primary-budget', 'fallback-budget']:
        client = LLMClient(Settings(_env_file=None, llm_mock=False), overrides={
            'provider': 'xai', 'provider_type': 'xai', 'model': name, 'base_url': f'https://{name}.invalid/v1', 'api_key': 'synthetic'})
        monkeypatch.setattr(client, '_record_usage', lambda *a, **kw: None)
        monkeypatch.setattr(client, '_record_transport_failure', lambda *a, **kw: None)
        monkeypatch.setattr(client, 'record_response_outcome', lambda *a, **kw: None)
        monkeypatch.setattr(client, 'retry_delay_seconds', lambda *a, **kw: 0)
        clients.append(client)
    snapshots = []
    with pytest.raises(LLMAttemptBudgetError):
        await CharacterRuntime(*clients).generate_replies(characters=characters(), recent_messages=[], user_message='next', prompt_snapshot_callback=snapshots.append)
    assert len(requests) == 3
    assert all(request.url.host == 'primary-budget.invalid' for request in requests)
    assert snapshots[0]['metadata']['network_attempts'] == 3
    assert snapshots[0]['metadata']['attempt_diagnostics'][-1]['code'] == 'network_budget_exhausted'


@pytest.mark.asyncio
@pytest.mark.parametrize('protocol', ['tagged_text', 'free_body_json'])
@pytest.mark.parametrize('part_type', ['dialogue', 'action', 'thought'])
async def test_free_body_repetition_loop_rejected_atomically(enable, protocol, part_type):
    enable(protocol)
    repeated = '같은 문장을 반복합니다. ' * 120
    body = repeated if part_type == 'dialogue' else (
        f'<a>{repeated}</a>' if part_type == 'action' else f'Visible speech.<t>{repeated}</t>'
    )
    client = SequenceClient([LLMResponse(content=encode(protocol, body), finish_reason='STOP') for _ in range(2)])
    persisted, snapshots = [], []
    with pytest.raises(CharacterRuntimeError, match='repetition'):
        await CharacterRuntime(client).generate_replies(characters=characters(), recent_messages=[], user_message='next',
            persist_replies=persisted.append, prompt_snapshot_callback=snapshots.append)
    assert persisted == []
    assert len(client.calls) == 2
    assert snapshots[0]['metadata']['attempt_diagnostics'][0]['code'] == 'free_body_invalid'


@pytest.mark.asyncio
@pytest.mark.parametrize('protocol', ['tagged_text', 'free_body_json'])
async def test_free_body_long_unique_prose_and_literal_code_remain_valid(enable, protocol):
    enable(protocol)
    thought = ' '.join(f'Observation {index}: detail-{index} changes the scene-{index}.' for index in range(30))
    code_sample = '`' + 'sample repeated code. ' * 30 + '`'
    body = f'<a>She turns.</a>Literal example: {code_sample}<t>{thought}</t>Then she speaks.'
    client = SequenceClient([LLMResponse(content=encode(protocol, body), finish_reason='STOP')])
    replies = await CharacterRuntime(client).generate_replies(characters=characters(), recent_messages=[], user_message='next')
    assert len(client.calls) == 1
    assert replies[0].raw_body == body and replies[0].thought == thought
    assert len(replies[0].thought or '') > 120


@pytest.mark.asyncio
@pytest.mark.parametrize('protocol', ['tagged_text', 'free_body_json'])
async def test_deep_invalid_free_body_never_enters_legacy_shadow_parser(monkeypatch, enable, protocol):
    enable(protocol)
    malformed = '[' * 10000 + '0' + ']' * 10000
    client = SequenceClient([LLMResponse(content=malformed, finish_reason='STOP') for _ in range(2)])
    persisted, snapshots = [], []
    def forbid_legacy_shadow(*args, **kwargs):
        raise AssertionError('Free-body output entered the legacy shadow parser')
    monkeypatch.setattr('app.engine.character_runtime.build_parser_shadow_metrics', forbid_legacy_shadow)
    with pytest.raises(CharacterRuntimeError):
        await CharacterRuntime(client).generate_replies(characters=characters(), recent_messages=[], user_message='next',
            persist_replies=persisted.append, prompt_snapshot_callback=snapshots.append)
    assert persisted == [] and len(client.calls) == 2
    assert len(snapshots) == 1 and snapshots[0]['response_preview'] == malformed
    assert snapshots[0]['metadata']['parse_status'] == 'failed'
    assert snapshots[0]['metadata']['attempt_diagnostics'][0]['code'] == 'free_body_invalid'
