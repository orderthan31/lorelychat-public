"""Comparison policy crosses real job/preview and auxiliary dispatch boundaries."""
import asyncio
import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlmodel import select

from app.api import conversations as api
from app.core.config import get_settings
from app.db.models import Conversation, Message, SceneState, LLMPromptSnapshot
from app.engine.llm_client import LLMClient, LLMResponse
from app.services import chat_command_service
from test_harness_comparison_api import seed
from test_harness_comparisons import options
from test_combined_command_runtime import encode


@pytest.fixture(autouse=True)
def reset_settings():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def setup_job(client, session, monkeypatch, *, recent=False, fresh=False):
    options(monkeypatch, 'native', 'full_history', 10000)
    monkeypatch.setenv('CHAT_RESPONSE_PROTOCOL', 'tagged_text')
    monkeypatch.setenv('CHAT_COMMAND_POSTPROCESS_MODE', 'combined_overlay')
    get_settings.cache_clear()
    room, char, rows = seed(client, session)
    scene = session.get(SceneState, room['id'])
    scene.current_conflict = 'STALE CONFLICT'
    scene.last_event = 'STALE EVENT'
    if fresh:
        for row in rows:
            session.delete(row)
        rows = []
        scene.summary = ''
        scene.last_compression_source_message_id = None
        scene.opening_scene = 'AUTHORED FRESH HOOK'
        scene.opening_line = 'AUTHORED FRESH LINE'
    if recent:
        start = datetime.now(timezone.utc) - timedelta(hours=1)
        for i in range(14):
            row = Message(id=f'command-prefix-{i:02}', conversation_id=room['id'],
                speaker_type='character', speaker_id=char['id'],
                content='\n'.join(f'Memory {i}, detail {j}: a public observation worth preserving.' for j in range(8)) + f' LONG END {i:02}!',
                action=f'PUBLIC ACTION {i}', thought='PRIVATE NEVER SHARED',
                created_at=start + timedelta(seconds=i))
            if i == 5:
                row.metadata_ = {'response_protocol': 'tagged_text', 'render_parts': [
                    {'type': 'dialogue', 'text': 'ORDER FIRST'},
                    {'type': 'thought', 'text': 'PRIVATE NEVER SHARED'},
                    {'type': 'action', 'text': 'ORDER SECOND'},
                    {'type': 'dialogue', 'text': row.content}]}
            session.add(row)
            rows.append(row)
    session.add(scene)
    session.commit()
    session.refresh(scene)
    state = scene.model_dump()
    command = client.post('/chat-commands', json={
        'name': 'explicit_comparison', 'generation_prompt': 'Keep the authored voice.',
        'postprocess_prompt': 'Render a panel.', 'postprocess_target': 'last_bubble',
        'postprocess_context_options': ['scene', 'turn_messages'] + (['recent_messages'] if recent else []),
        'postprocess_block_types': ['note']}).json()
    chat_command_service.set_active_command_ids(session, session.get(Conversation, room['id']), [command['id']])
    monkeypatch.setattr(api, '_start_message_generation_worker', lambda *a: None)
    queued = client.post(f"/conversations/{room['id']}/messages/jobs", json={
        'speaker_type': 'user', 'speaker_id': 'u', 'content': 'EXACT COMMAND SOURCE'}).json()
    return room, char, rows, state, command, queued


def read_completed(client, session, room, queued, calls):
    job_id = queued['job']['id']
    status = client.get(f"/conversations/{room['id']}/generation-jobs/{job_id}").json()
    assert status['status'] == 'completed', status
    data = client.get(f"/conversations/{room['id']}/messages").json()
    generated = [m for m in data if m['id'] in status['generated_message_ids']]
    source = next(m for m in data if m['id'] == queued['incoming_message']['id'])
    before = len(calls)
    asyncio.run(api._run_message_generation_job_in_session(session, job_id))
    assert len(calls) == before
    session.expire_all()
    return source, generated


@pytest.mark.parametrize('case', ['combined', 'missing', 'malformed', 'exhausted', 'recent', 'fresh'])
@pytest.mark.parametrize('serialization', ['native', 'transcript'])
def test_frozen_full_context_real_job_preview_replay(client, session, monkeypatch, case, serialization):
    room, char, rows, state, command, queued = setup_job(
        client, session, monkeypatch, recent=case == 'recent', fresh=case == 'fresh')
    options(monkeypatch, serialization, 'full_history', 10000)
    preview_response = client.get(f"/conversations/{room['id']}/context-preview")
    assert preview_response.status_code == 200
    preview = preview_response.json()
    assert preview['context_policy'] == 'full_history'
    preview_text = json.dumps(preview, ensure_ascii=False)
    for stale in ['DERIVED ARC MUST STAY STORED', 'STALE CONFLICT', 'STALE EVENT']:
        assert stale not in preview_text
    if case != 'fresh':
        assert 'STALE LOCATION' not in preview_text and 'STALE MOOD' not in preview_text
    else:
        assert 'AUTHORED FRESH HOOK' in preview_text
    session.add(Message(id='future-command-row', conversation_id=room['id'], speaker_type='user',
                        speaker_id='u', content='FUTURE MUST NOT LEAK',
                        created_at=datetime.now(timezone.utc) + timedelta(hours=1)))
    session.commit()
    calls = []
    async def chat(self, messages, **kwargs):
        calls.append((self.purpose, messages))
        if self.purpose == 'chat_command_postprocess':
            return LLMResponse(content='invalid' if case == 'exhausted' else
                '{"messages":[{"content":"","blocks":[{"type":"note","text":"Panel"}]}]}')
        # A deployment toggle during the await must not change auxiliary policy.
        options(monkeypatch, 'transcript', 'compact')
        if case in {'missing', 'exhausted', 'fresh'}:
            raw = '<m c="C1">First.<t>Secret.</t>Last.</m>'
        elif case == 'malformed':
            raw = '<m c="C1">First.<t>Secret.</t>Last.</m><panels><panel k="K1"><bad>bad</bad></panel></panels>'
        elif case == 'recent':
            raw = '<m c="C1">First.<t>Secret.</t>Last.</m>'
        else:
            raw = encode('tagged_text')
        return LLMResponse(content=raw, finish_reason='STOP')
    monkeypatch.setattr('app.engine.llm_client.LLMClient.chat', chat)
    asyncio.run(api._run_message_generation_job_in_session(session, queued['job']['id']))
    source, generated = read_completed(client, session, room, queued, calls)
    assert len(calls) == (1 if case == 'combined' else 3 if case == 'exhausted' else 2)
    assert generated[0]['content'] == 'First.\nLast.'
    assert generated[0]['thought'] == 'Secret.'
    assert len(generated) == (1 if case == 'exhausted' else 2)
    if case == 'exhausted':
        assert source['metadata']['command_postprocess_parse_attempts'] == 2
        assert source['metadata']['command_postprocess_status'] == 'failed'
    for purpose, messages in calls:
        wire = '\n'.join(m['content'] for m in messages)
        for absent in ['DERIVED ARC MUST STAY STORED', 'STALE CONFLICT', 'STALE EVENT',
                       'HISTORICAL PRIVATE', 'PRIVATE NEVER SHARED', 'FUTURE MUST NOT LEAK']:
            assert absent not in wire
        assert wire.count('EXACT COMMAND SOURCE') == 1
        if case == 'fresh':
            assert 'AUTHORED FRESH HOOK' in wire and 'AUTHORED FRESH LINE' in wire
        else:
            assert 'STALE LOCATION' not in wire and 'STALE MOOD' not in wire and 'STALE OPENING' not in wire
        if purpose == 'chat_command_postprocess':
            assert wire.count('First.') == 1 and wire.count('Last.') == 1 and 'Secret.' not in wire
            assert 'AUTHORED WORLD' in wire
            if case == 'recent':
                for row in rows:
                    assert wire.count(row.content) == 1
                assert wire.index('ORDER FIRST') < wire.index('ORDER SECOND') < wire.index('LONG END 05!')
                assert f'character:Canon({char["id"]})' in wire
    saved = session.get(SceneState, room['id'])
    assert saved.model_dump() == state
    snapshot = session.exec(select(LLMPromptSnapshot).where(
        LLMPromptSnapshot.source_message_id == queued['incoming_message']['id'])).one()
    assert snapshot.messages_json == calls[0][1]
    assert snapshot.metadata_['projected_input_tokens'] == preview['projected_input_tokens']


@pytest.mark.parametrize('retry', [False, True])
def test_auxiliary_capacity_preserves_completed_main_before_provider(client, session, monkeypatch, retry):
    from dataclasses import replace
    from app.engine.harness_comparison import rendered_input_tokens
    from app.services.context_management_service import resolve_safe_model_context_capacity
    room, char, rows, state, command, queued = setup_job(client, session, monkeypatch, recent=True)
    calls = []
    original = chat_command_service.apply_chat_command_postprocess
    async def bounded(**kwargs):
        # Isolate the auxiliary boundary; the main keeps its independently valid capacity.
        capacity = kwargs.get('context_capacity') or resolve_safe_model_context_capacity(None)
        if retry:
            # Capture the fully assembled initial request without making a provider call.
            # The admission seam sees exactly the same request as actual dispatch.
            original_guard = chat_command_service.enforce_rendered_context_capacity
            seen = []
            def exact_first(messages, cap, **opts):
                if not seen:
                    seen.append(rendered_input_tokens(messages, **opts))
                return original_guard(messages, replace(cap, working_input_tokens=seen[0]), **opts)
            monkeypatch.setattr(chat_command_service, 'enforce_rendered_context_capacity', exact_first)
        else:
            kwargs['context_capacity'] = replace(capacity, working_input_tokens=1)
        return await original(**kwargs)
    monkeypatch.setattr(chat_command_service, 'apply_chat_command_postprocess', bounded)
    async def chat(self, messages, **kwargs):
        calls.append((self.purpose, messages))
        return LLMResponse(content='invalid' if self.purpose == 'chat_command_postprocess' else
                           '<m c="C1">Valid main.<t>Private stays.</t></m>', finish_reason='STOP')
    monkeypatch.setattr('app.engine.llm_client.LLMClient.chat', chat)
    asyncio.run(api._run_message_generation_job_in_session(session, queued['job']['id']))
    source, generated = read_completed(client, session, room, queued, calls)
    assert [p for p, _ in calls] == ['chat_generation'] + (['chat_command_postprocess'] if retry else [])
    assert len(generated) == 1 and generated[0]['content'] == 'Valid main.'
    assert generated[0]['thought'] == 'Private stays.'
    metadata = source['metadata']
    assert metadata['command_postprocess_status'] == 'failed'
    assert metadata['command_postprocess_error_code'] == 'context_maintenance_required'
    assert metadata['command_postprocess_parse_attempts'] == int(retry)
    assert metadata['command_postprocess_network_attempts'] == 0  # Fake chat never enters transport.
    diagnostic = metadata['command_postprocess_diagnostics'][-1]
    assert diagnostic['code'] == 'context_maintenance_required'
    assert diagnostic['projected_tokens'] > diagnostic['working_tokens']
    assert 'EXACT COMMAND SOURCE' not in json.dumps(diagnostic)
    assert session.get(SceneState, room['id']).model_dump() == state


@pytest.mark.parametrize('policy', ['full_history', 'expanded'])
@pytest.mark.parametrize('case', ['valid', 'oversized', 'retry_overflow'])
def test_actual_auxiliary_wire_and_usage_admission(client, session, monkeypatch, policy, case):
    """Real adapter + MockTransport: capacity rejection produces no usage event."""
    import httpx
    from app.db.models import ChatCommand, LLMUsageEvent
    from app.engine import llm_client
    room, char, rows, state, command, queued = setup_job(client, session, monkeypatch, recent=True)
    options(monkeypatch, 'native', policy, 10000)
    oversized = case == 'oversized'
    retry_overflow = case == 'retry_overflow'
    if oversized:
        cmd = session.get(ChatCommand, command['id'])
        # This standalone-only authored instruction cannot invalidate the main.
        cmd.postprocess_prompt = '\n'.join(f'Panel detail {i}: retain this selected authored instruction.' for i in range(1200))
        session.add(cmd)
        session.commit()
    if retry_overflow:
        from dataclasses import replace
        from app.engine.harness_comparison import rendered_input_tokens
        # Exact admission boundary: initial fits with schema, retry control does not.
        guard = chat_command_service.enforce_rendered_context_capacity
        initial = []
        def enforce(messages, capacity, **kwargs):
            if not initial:
                initial.append(rendered_input_tokens(messages, **kwargs))
            return guard(messages, replace(capacity, working_input_tokens=initial[0]), **kwargs)
        monkeypatch.setattr(chat_command_service, 'enforce_rendered_context_capacity', enforce)
    real_client = llm_client.LLMClient
    outputs = []
    def factory(*args, **kwargs):
        overrides = {**kwargs.pop('overrides', {}), 'provider': 'openai', 'provider_type': 'openai',
            'model': 'synthetic-command-model', 'base_url': 'https://provider.invalid/v1', 'api_key': 'synthetic'}
        instance = real_client(get_settings().model_copy(update={'llm_mock': False}), **kwargs, overrides=overrides)
        outputs.append(instance.max_output_tokens())
        return instance
    monkeypatch.setattr(api, 'LLMClient', factory)
    monkeypatch.setattr(llm_client, 'engine', session.get_bind())
    payloads = []
    def handler(request):
        payload = json.loads(request.content)
        payloads.append(payload)
        auxiliary = bool(payload.get('response_format'))
        raw = ('{"messages":[{"content":"","blocks":[{"type":"note","text":"Panel"}]}]}' if auxiliary else
               '<m c="C1">Valid main.<t>Private stays.</t></m>')
        if auxiliary and retry_overflow:
            raw = 'invalid'
        return httpx.Response(200, json={'choices': [{'message': {'content': raw}, 'finish_reason': 'stop'}],
            'usage': {'prompt_tokens': 111, 'completion_tokens': 22, 'total_tokens': 133}})
    real_http = httpx.AsyncClient
    monkeypatch.setattr(httpx, 'AsyncClient', lambda *a, **k: real_http(*a, **k, transport=httpx.MockTransport(handler)))
    asyncio.run(api._run_message_generation_job_in_session(session, queued['job']['id']))
    source, generated = read_completed(client, session, room, queued, payloads)
    assert len(payloads) == (1 if oversized else 2)
    assert generated[0]['content'] == 'Valid main.' and generated[0]['thought'] == 'Private stays.'
    events = session.exec(select(LLMUsageEvent).where(LLMUsageEvent.conversation_id == room['id'])).all()
    assert sorted(event.purpose for event in events) == sorted(['chat_generation'] + ([] if oversized else ['chat_command_postprocess']))
    assert all(event.prompt_tokens == 111 and event.completion_tokens == 22 for event in events)
    assert all(payload.get('max_tokens', payload.get('max_completion_tokens')) == outputs[0] for payload in payloads)
    if oversized or retry_overflow:
        assert len(generated) == 1
        metadata = source['metadata']
        assert metadata['command_postprocess_error_code'] == 'context_maintenance_required'
        assert metadata['command_postprocess_network_attempts'] == int(retry_overflow)
        assert metadata['command_postprocess_parse_attempts'] == int(retry_overflow)
        assert metadata['command_postprocess_diagnostics'][-1]['completion_reserve_tokens'] >= outputs[0]
    else:
        assert len(generated) == 2
        metadata = source['metadata']
        assert metadata['command_postprocess_network_attempts'] == 1
        wire = '\n'.join(m['content'] for m in payloads[1]['messages'])
        assert wire.count('EXACT COMMAND SOURCE') == 1 and wire.count('Valid main.') == 1
        assert 'Private stays.' not in wire and 'PRIVATE NEVER SHARED' not in wire
        if policy == 'full_history':
            assert all(wire.count(row.content) == 1 for row in rows)
            assert 'DERIVED ARC MUST STAY STORED' not in wire and 'STALE LOCATION' not in wire


@pytest.mark.parametrize('snapshot', [False, True])
def test_full_prefix_equal_timestamp_source_and_represented_dedupe(session, snapshot):
    from app.db.models import ChatCommand
    from app.engine.harness_comparison import ComparisonOptions
    from test_command_context_boundary import seed_history
    cid, rows = seed_history(session, count=24, boundary_index=18, same_timestamp=True)
    command = ChatCommand(id='prefix-command', name='prefix-command', postprocess_context_options=['recent_messages'])
    source = rows[20]
    text = chat_command_service.build_command_postprocess_context(session, command, cid, source, [rows[19]],
        represented_ids={source.id, rows[19].id}, comparison_options=ComparisonOptions(context_policy='full_history'),
        source_messages=rows if snapshot else None)
    assert all(text.count(row.content) == 1 for row in rows[:19])
    assert all(row.content not in text for row in rows[19:])
    offsets = [text.index(row.content) for row in rows[:19]]
    assert offsets == sorted(offsets)


@pytest.mark.asyncio
@pytest.mark.parametrize('known_capacity', [False, True])
async def test_auxiliary_actual_output_reserve_cannot_expand_capacity(session, known_capacity):
    from app.db.models import ChatCommand, ModelOption
    from app.engine.harness_comparison import ComparisonOptions
    from app.services.context_management_service import resolve_model_context_capacity
    source = Message(id='capacity-source', conversation_id='capacity-room', speaker_type='user', speaker_id='u', content='Source')
    reply = Message(id='capacity-reply', conversation_id='capacity-room', speaker_type='character', speaker_id='c',
                    content='Keep my main.', thought='Keep my thought.')
    command = ChatCommand(id='capacity-command', name='capacity-command', postprocess_target='last_bubble',
        postprocess_context_options=['turn_messages'], postprocess_block_types=['note'],
        postprocess_prompt='\n'.join(f'Authored panel instruction {i} must remain intact.' for i in range(800)))
    class Client(LLMClient):
        def max_output_tokens(self):
            return 8192
        async def chat(self, *args, **kwargs):
            raise AssertionError('Capacity failure must precede the provider')
    capacity = resolve_model_context_capacity(
        ModelOption(key='narrow', context_window_tokens=12000, max_output_tokens=2048),
        completion_reserve_tokens=2048) if known_capacity else None
    result = await chat_command_service.apply_chat_command_postprocess(
        session=session, command=command, conversation_id='capacity-room', source_message=source,
        generated_messages=[reply], llm_client=Client(), commit=False, context_capacity=capacity,
        comparison_options=ComparisonOptions(context_policy='full_history', input_budget_tokens=100000),
        source_messages=[source])
    assert result == [reply] and reply.content == 'Keep my main.' and reply.thought == 'Keep my thought.'
    metadata = source.metadata_
    assert metadata['command_postprocess_error_code'] == 'context_maintenance_required'
    assert metadata['command_postprocess_network_attempts'] == 0
    assert metadata['command_postprocess_parse_attempts'] == 0
    diagnostic = metadata['command_postprocess_diagnostics'][0]
    assert diagnostic['completion_reserve_tokens'] == 8192
    assert diagnostic['working_tokens'] == (12000 if known_capacity else 16384) - 8192 - 1024
    assert diagnostic['capacity_source'] == ('model_metadata' if known_capacity else 'conservative_fallback')
