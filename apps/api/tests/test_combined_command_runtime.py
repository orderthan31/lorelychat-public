"""Synthetic command request, runtime, HTTP, API job and atomic acceptance."""
import asyncio
import json
from datetime import datetime, timezone

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlmodel import select, create_engine

from app.core.config import Settings, get_settings
from app.db.models import ChatCommand, Character, Conversation, Message, MessageGenerationJob, PostCommitTask, SceneState
from app.engine import command_overlay as co, free_body_protocol as fb, prompts
from app.engine.character_runtime import CharacterRuntime, CharacterRuntimeError, generation_control_reserve_tokens
from app.engine.llm_client import LLMClient, LLMResponse
from app.services import chat_command_service as cs, conversation_service


@pytest.fixture
def mode(monkeypatch):
    def enable(protocol):
        monkeypatch.setenv('CHAT_COMMAND_POSTPROCESS_MODE', 'combined_overlay')
        monkeypatch.setenv('CHAT_RESPONSE_PROTOCOL', protocol)
        get_settings.cache_clear()
    yield enable
    get_settings.cache_clear()


def command(id='cmd', target='last_bubble', **kwargs):
    return ChatCommand(id=id, name=id, prompt='Synthetic panel instruction.', postprocess_prompt='UNIQUE_COMMAND_CONSTRAINT',
                       postprocess_target=target, postprocess_block_types=['note'],
                       postprocess_context_options=['scene', 'world', 'turn_messages'], **kwargs)


def encode(protocol, *, panels=True, bad=False, who='C1', body='First.<a>Moves.</a>Last.<t>Secret.</t>'):
    if protocol == 'tagged_text':
        tail = '<panels><panel k="K1"><note title="Status">Ready.</note></panel></panels>' if panels else ''
        if bad:
            tail = '<panels><panel k="K1"><note><nested>invalid</nested></note></panel></panels>'
        return f'<m c="{who}">{body}</m>' + tail
    value = {'replies': [{'who': who, 'text': body}]}
    if panels:
        value['panels'] = [{'key': 'K1', 'blocks': [{'type': 'note', 'title': 'Status', 'text': 'Ready.'}]}]
    if bad:
        return json.dumps({'replies': value['replies']})[:-1] + ',"panels":[' + '[' * 2000
    return json.dumps(value)


class Client:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []
    async def chat(self, messages, response_format=None, **kwargs):
        self.calls.append({'messages': messages, 'format': response_format})
        return next(self.responses)


@pytest.mark.asyncio
@pytest.mark.parametrize('protocol', ['tagged_text', 'free_body_json'])
@pytest.mark.parametrize('case', ['valid', 'missing', 'malformed', 'retry'])
async def test_runtime_prepared_contract_and_private_callback(mode, protocol, case):
    mode(protocol)
    plan = co.build_plan([command()])
    cast = [Character(id='a', name='Aria')]
    response = encode(protocol, panels=case != 'missing', bad=case == 'malformed')
    sequence = ([LLMResponse(content=encode(protocol, who='BAD'))] if case == 'retry' else []) + [LLMResponse(content=response)]
    provider = Client(sequence)
    harness = prompts.build_multi_character_prompt_harness(characters=cast, recent_messages=[], user_message='source', command_overlay_plan=plan)
    seen, snapshots = [], []
    def persist(turn):
        seen.append(turn)
        return turn
    result = await CharacterRuntime(provider).generate_replies(characters=cast, recent_messages=[], user_message='source',
        source_speaker_type='character', prepared_harness=harness, command_overlay_plan=plan,
        persist_replies=persist, prompt_snapshot_callback=snapshots.append)
    assert result is seen[0] and result[0].text == 'First.\nLast.'
    assert len(provider.calls) == (2 if case == 'retry' else 1)
    assert bool(result.command_panels) == (case in {'valid', 'retry'})
    assert not {'command_panels', 'command_outcomes'} & result[0].model_dump().keys()
    assert snapshots[0]['response_preview'] == response
    assert snapshots[0]['metadata']['command_postprocess']['cmd']['status'] == ('combined_success' if case in {'valid', 'retry'} else 'fallback')
    for call in provider.calls:
        wire = json.dumps(call['messages'])
        assert wire.count('UNIQUE_COMMAND_CONSTRAINT') == 1
        assert '"role": "assistant"' in wire
        assert (call['format'] is None) == (protocol == 'tagged_text')
        if protocol == 'free_body_json':
            assert set(call['format']['schema']['properties']) == {'replies', 'panels'}
    before = generation_control_reserve_tokens(speaker_type='user', min_bubbles=1, max_bubbles=8, characters=cast)
    after = generation_control_reserve_tokens(speaker_type='user', min_bubbles=1, max_bubbles=8, characters=cast, command_overlay_plan=plan)
    assert after >= before
    if protocol == 'free_body_json':
        assert after > before


@pytest.mark.asyncio
@pytest.mark.parametrize('protocol', ['tagged_text', 'free_body_json'])
@pytest.mark.parametrize('failure', ['unknown', 'LENGTH', 'MAX_TOKENS', 'deep', 'repetition', 'control'])
async def test_invalid_primary_never_persists(mode, protocol, failure):
    mode(protocol)
    plan = co.build_plan([command()])
    raw = encode(protocol, who='BAD' if failure == 'unknown' else 'C1')
    if failure == 'deep':
        raw = '[' * 3000 if protocol == 'free_body_json' else '<m c="C1">' + '<a>' * 3000
    if failure == 'repetition':
        raw = encode(protocol, body='An endless repeated sentence here. ' * 120)
    if failure == 'control':
        raw = encode(protocol, body='<|system|>hidden')
    provider = Client([LLMResponse(content=raw, finish_reason=failure if failure in {'LENGTH', 'MAX_TOKENS'} else 'STOP')] * 2)
    seen, snapshots = [], []
    with pytest.raises(CharacterRuntimeError):
        await CharacterRuntime(provider).generate_replies(characters=[Character(id='a', name='Aria')], recent_messages=[], user_message='source',
            command_overlay_plan=plan, persist_replies=seen.append, prompt_snapshot_callback=snapshots.append)
    assert not seen and len(provider.calls) == 2
    assert snapshots[0]['response_preview'] == raw


@pytest.mark.asyncio
@pytest.mark.parametrize('protocol', ['tagged_text', 'free_body_json'])
@pytest.mark.parametrize('provider', ['gemini', 'xai'])
@pytest.mark.parametrize('finish', ['STOP', 'LENGTH'])
async def test_combined_real_http_transport_budget_unchanged(mode, monkeypatch, protocol, provider, finish):
    mode(protocol)
    payloads = []
    def handler(request):
        payloads.append(json.loads(request.content))
        raw = encode(protocol)
        if finish == 'LENGTH':
            raw = raw.split('Ready.')[0] + 'Partial panel'
        if provider == 'gemini':
            return httpx.Response(200, json={'candidates': [{'content': {'parts': [{'text': raw}]}, 'finishReason': finish}]})
        return httpx.Response(200, json={'choices': [{'message': {'content': raw}, 'finish_reason': finish.lower()}]})
    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, 'AsyncClient', lambda *a, **kw: real(*a, **kw, transport=httpx.MockTransport(handler)))
    client = LLMClient(Settings(_env_file=None, llm_mock=False), overrides={'provider': provider, 'provider_type': provider,
        'model': 'synthetic-command-model', 'base_url': 'https://provider.invalid/v1', 'api_key': 'synthetic', 'min_output_tokens': 1280})
    monkeypatch.setattr(client, '_record_usage', lambda *a, **kw: None)
    monkeypatch.setattr(client, 'record_response_outcome', lambda *a, **kw: None)
    limit = client.max_output_tokens()
    assert limit == 2560
    result = await CharacterRuntime(client).generate_replies(characters=[Character(id='a', name='Aria')], recent_messages=[], user_message='source', command_overlay_plan=co.build_plan([command()]))
    assert bool(result.command_panels) == (finish == 'STOP') and len(payloads) == 1
    assert result[0].text == 'First.\nLast.' and result[0].thought == 'Secret.'
    payload = payloads[0]
    if protocol == 'tagged_text':
        assert 'response_format' not in payload
        assert not {'responseMimeType', 'responseSchema'} & payload.get('generationConfig', {}).keys()
    else:
        schema = payload['generationConfig']['responseSchema'] if provider == 'gemini' else payload['response_format']['json_schema']['schema']
        assert set(schema['properties']) == {'replies', 'panels'}
        assert set(schema['properties']['replies']['items']['properties']) == {'who', 'text', 'emotion'}
    assert (payload['generationConfig']['maxOutputTokens'] if provider == 'gemini' else payload['max_tokens']) == limit


@pytest.mark.asyncio
async def test_standalone_request_has_each_public_body_once(session):
    source = Message(id='source', conversation_id='room', speaker_type='user', speaker_id='u', content='UNIQUE_SOURCE')
    messages = []
    for index in range(5):
        body = f'<a>ACTION_{index}</a>FIRST_{index}<t>PRIVATE_{index}</t><a>SECOND_ACTION_{index}</a>LAST_{index}'
        reply = fb.parse_response(f'<m c="C1">{body}</m>', protocol='tagged_text', aliases={'C1': 'a'})[0]
        messages.append(Message(id=f'm{index}', conversation_id='room', speaker_type='character', speaker_id='a', content=reply.text,
                                action=reply.action, thought=reply.thought, metadata_=fb.metadata_for_reply(reply)))
    session.add_all([source, *messages]); session.commit()
    cmd = command()
    cmd.postprocess_context_options = ['turn_messages', 'recent_messages']
    provider = Client([LLMResponse(content='{"messages":[{"content":"","blocks":[{"type":"note","text":"ready"}]}]}')])
    result = await cs.apply_chat_command_postprocess(session=session, command=cmd, conversation_id='room', source_message=source,
                                                    generated_messages=messages, llm_client=provider, commit=False)
    wire = '\n'.join(m['content'] for m in provider.calls[0]['messages'])
    assert wire.count('UNIQUE_SOURCE') == 1
    for index in range(5):
        assert wire.count(f'FIRST_{index}') == wire.count(f'LAST_{index}') == 1
        assert f'PRIVATE_{index}' not in wire
        assert wire.index(f'ACTION_{index}') < wire.index(f'FIRST_{index}') < wire.index(f'SECOND_ACTION_{index}') < wire.index(f'LAST_{index}')
    assert len(result) == 6


def test_sqlite_additive_migration_preserves_existing_rows():
    from app.db.session import migrate_sqlite_columns
    engine = create_engine('sqlite://')
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE chat_commands (id VARCHAR PRIMARY KEY, name VARCHAR, prompt VARCHAR, generation_prompt VARCHAR, postprocess_prompt VARCHAR, postprocess_target VARCHAR, postprocess_probability INTEGER, postprocess_context_options JSON)"))
        conn.execute(text("INSERT INTO chat_commands VALUES ('old','custom','authored','gen','post','last_bubble',75,'[]')"))
    migrate_sqlite_columns(engine)
    migrate_sqlite_columns(engine)
    with engine.connect() as conn:
        row = conn.execute(text('SELECT * FROM chat_commands')).mappings().one()
        assert row['prompt'] == 'authored' and row['postprocess_block_types'] == '[]'
        assert row['postprocess_context_options'] == '[]'


@pytest.mark.parametrize('protocol', ['tagged_text', 'free_body_json'])
@pytest.mark.parametrize('bad', [False, True, 'truncated'])
def test_actual_job_combined_or_targeted_fallback_atomic_readback(client, session, monkeypatch, mode, protocol, bad):
    from app.api.conversations import _run_message_generation_job_in_session
    mode(protocol)
    monkeypatch.setattr('app.api.conversations._start_message_generation_worker', lambda *a: None)
    actor = client.post('/characters', json={'name': 'Aria', 'persona': 'Synthetic adult.'}).json()
    room = client.post('/conversations', json={'mode': 'user_character', 'genre_mode': 'custom',
        'participants': [{'type': 'character', 'id': actor['id']}]}).json()['id']
    cmd = client.post('/chat-commands', json={'name': 'optimize', 'postprocess_prompt': 'UNIQUE_COMMAND_CONSTRAINT',
        'generation_prompt': 'Natural scene.', 'postprocess_target': 'last_bubble', 'postprocess_block_types': ['note']}).json()
    conversation = session.get(Conversation, room)
    cs.set_active_command_ids(session, conversation, [cmd['id']])
    calls = []
    async def chat(self, messages, response_format=None, **kwargs):
        calls.append({'messages': messages, 'format': response_format, 'purpose': self.purpose})
        if self.purpose == 'chat_command_postprocess':
            return LLMResponse(content='{"messages":[{"content":"","blocks":[{"type":"note","text":"Fallback ready."}]}]}')
        raw = encode(protocol, bad=bad is True)
        if bad == 'truncated':
            raw = raw.split('Ready.')[0] + 'Partial panel'
        return LLMResponse(content=raw, finish_reason='LENGTH' if bad == 'truncated' else 'STOP')
    monkeypatch.setattr(LLMClient, 'chat', chat)
    response = client.post(f'/conversations/{room}/messages/jobs', json={'speaker_type': 'user', 'speaker_id': 'u', 'content': 'UNIQUE_SOURCE'})
    assert response.status_code == 200, response.text
    job_id = response.json()['job']['id']
    asyncio.run(_run_message_generation_job_in_session(session, job_id))
    status = client.get(f'/conversations/{room}/generation-jobs/{job_id}').json()
    assert status['status'] == 'completed', status
    data = client.get(f'/conversations/{room}/messages').json()
    generated = [m for m in data if m['id'] in status['generated_message_ids']]
    assert len(generated) == 2
    primary, panel = generated
    assert primary['content'] == 'First.\nLast.' and primary['thought'] == 'Secret.'
    assert panel['metadata']['command_overlay']
    assert panel['metadata']['command_postprocess_mode'] == ('separate' if bad else 'combined_overlay')
    assert len(calls) == (2 if bad else 1)
    assert len([c for c in calls if c['purpose'] == 'chat_generation']) == 1
    assert json.dumps(calls[0]['messages']).count('UNIQUE_COMMAND_CONSTRAINT') == 1
    assert 'command_panels' not in primary['metadata']
    source = next(m for m in data if m['id'] == response.json()['incoming_message']['id'])
    assert source['metadata']['command_postprocess_execution'][cmd['id']]['status'] == ('fallback' if bad else 'combined_success')
    preview = client.get(f'/conversations/{room}/context-preview')
    assert preview.status_code == 200 and 'UNIQUE_COMMAND_CONSTRAINT' in preview.text
    assert 'command_panels' in preview.text
    # A completed replay must not generate or persist twice.
    asyncio.run(_run_message_generation_job_in_session(session, job_id))
    assert len(calls) == (2 if bad else 1)
    assert len(session.exec(select(Message).where(Message.generation_job_id == job_id)).all()) == 2


@pytest.mark.parametrize('protocol', ['tagged_text', 'free_body_json'])
def test_alias_isolation_literals_and_order(mode, protocol):
    mode(protocol)
    plan = co.build_plan([command('first', 'first_bubble'), command('last')])
    literal = 'A code sample: `<panels><panel k="K1">literal</panel></panels>` remains dialogue.'
    if protocol == 'tagged_text':
        raw = (f'<m c="C1">{literal}</m><panels>'
               '<panel k="K1"><note>first</note></panel>'
               '<panel k="UNKNOWN"><note>ignored</note></panel>'
               '<panel k="K2"><note>last</note></panel>'
               "<panel k='K2'><note>duplicate</note></panel></panels>")
    else:
        raw = json.dumps({'replies': [{'who': 'C1', 'text': literal}], 'panels': [
            {'key': 'K1', 'blocks': [{'type': 'note', 'text': 'first'}]},
            {'key': 'UNKNOWN', 'blocks': [{'type': 'note', 'text': 'ignored'}]},
            {'key': 'K2', 'blocks': [{'type': 'note', 'text': 'last'}]},
            {'key': 'K2', 'blocks': [{'type': 'note', 'text': 'duplicate'}]}]})
    primary, panels, outcomes = co.split_response(raw, protocol, plan)
    assert fb.parse_response(primary, protocol=protocol, aliases={'C1': 'a'})[0].text == literal
    assert set(panels) == {'first'}
    assert outcomes['last']['reason'] == 'duplicate_alias'
    assert outcomes['first']['unknown_aliases'] == ['UNKNOWN']
    if protocol == 'tagged_text':
        raw = '<m c="C1">safe</m><panels><panel k="K1">' + '<note>' * 1000 + '</panel></panels>'
    else:
        raw = '{"replies":[{"who":"C1","text":"safe"}],"panels":' + '[' * 2000
    primary, panels, _ = co.split_response(raw, protocol, plan)
    assert not panels
    assert fb.parse_response(primary, protocol=protocol, aliases={'C1': 'a'})[0].text == 'safe'


@pytest.mark.asyncio
@pytest.mark.parametrize('protocol', ['tagged_text', 'free_body_json'])
async def test_multi_command_fallback_only_missing_and_atomic_rollback(session, mode, protocol):
    from app.api.conversations import persist_generated_reply_messages
    from app.schemas.conversations import MessageCreate
    mode(protocol)
    commands = [command('first', 'first_bubble'), command('last')]
    plan = co.build_plan(commands)
    cast = [Character(id='a', name='Aria')]
    provider = Client([LLMResponse(content=encode(protocol))])  # K1 only
    turn = await CharacterRuntime(provider).generate_replies(characters=cast, recent_messages=[], user_message='source', command_overlay_plan=plan)
    source = Message(id='src', conversation_id='room', speaker_type='user', speaker_id='u', content='source')
    transient = await persist_generated_reply_messages(session=session, conversation_id='room', conversation=Conversation(id='room'),
        payload=MessageCreate(speaker_type='user', speaker_id='u', content='source'), incoming_message=source, generated_replies=turn,
        room_characters=cast, scene_state=None, is_multi_room=False, min_bubbles=8)
    fallback = Client([LLMResponse(content='{"messages":[{"content":"","blocks":[{"type":"note","text":"Fallback last"}]}]}')])
    for cmd in commands:
        transient = await cs.apply_chat_command_postprocess(session=session, command=cmd, conversation_id='room', source_message=source,
            generated_messages=transient, combined_messages=turn.command_panels.get(cmd.id), execution_outcome=turn.command_outcomes[cmd.id],
            llm_client=fallback, commit=False)
    assert len(provider.calls) == len(fallback.calls) == 1
    assert [m.speaker_type for m in transient] == ['storytelling', 'character', 'storytelling']
    assert transient[0].metadata_['command_id'] == 'first' and transient[-1].metadata_['command_id'] == 'last'
    assert set(source.metadata_['command_postprocess_execution']) == {'first', 'last'}
    assert source.metadata_['command_postprocess_execution']['last']['status'] == 'fallback'
    assert session.exec(select(Message)).all() == []  # no intermediate commits
    job = MessageGenerationJob(id='combined_atomic', conversation_id='room', incoming_message_id='src', status='running',
        lease_owner='worker', lease_expires_at=datetime.now(timezone.utc))
    session.add_all([source, job]); session.commit()
    session.exec(text("CREATE TRIGGER fail_combined BEFORE INSERT ON messages WHEN NEW.generation_job_id = 'combined_atomic' AND NEW.reply_index = 1 BEGIN SELECT RAISE(ABORT, 'forced failure'); END"))
    session.commit()
    with pytest.raises(IntegrityError):
        conversation_service.finalize_message_generation_turn(session, job, transient, post_commit_tasks=[])
    session.expire_all()
    assert session.exec(select(Message).where(Message.generation_job_id == job.id)).all() == []
    assert session.get(MessageGenerationJob, job.id).status == 'running'
    assert session.exec(select(PostCommitTask)).all() == []


@pytest.mark.parametrize('protocol,kinds,target,context,reason', [
    ('legacy_json', ['note'], 'last_bubble', [], 'legacy_protocol'),
    ('tagged_text', [], 'last_bubble', [], 'generic_contract'),
    ('tagged_text', ['note'], 'all_bubbles', [], 'not_overlay'),
    ('tagged_text', ['cards'], 'last_bubble', [], 'unsupported_block_types'),
    ('tagged_text', ['note'], 'last_bubble', ['recent_messages'], 'unsupported_context'),
    ('tagged_text', ['note'], 'last_bubble', ['relationship_memory'], 'unsupported_context'),
])
def test_explicit_eligibility_never_classifies_prose(mode, protocol, kinds, target, context, reason):
    mode(protocol)
    cmd = command(target=target)
    cmd.postprocess_block_types = kinds
    cmd.postprocess_context_options = context
    cmd.name = 'comments note table broadcasting'
    plan = co.build_plan([cmd])
    assert not plan.eligible and plan.outcomes['cmd']['reason'] == reason


def test_safe_deployment_defaults():
    assert Settings.model_fields['chat_command_postprocess_mode'].default == 'separate'
    assert Settings.model_fields['chat_response_protocol'].default == 'legacy_json'
    with pytest.raises(ValueError):
        Settings(_env_file=None, chat_command_postprocess_mode='guess')


def test_combined_context_only_adds_missing_fields_once(session, mode):
    from app.db.models import WorldSetting
    mode('tagged_text')
    world = WorldSetting(id='w', title='WORLD_TITLE', description='WORLD_DESCRIPTION', world_seed='SHARED_WORLD')
    room = Conversation(id='room', world_setting_id='w', mode='user_character')
    scene = SceneState(conversation_id='room', summary='SHARED_ARC', world_seed='SHARED_WORLD', location='SELECTED_LOCATION', mood='SELECTED_MOOD', current_conflict='SELECTED_CONFLICT', last_event='SELECTED_EVENT')
    session.add_all([world, room, scene]); session.commit()
    plan = co.build_plan([command('first', 'first_bubble'), command('last')])
    cs.prepare_combined_context(session, plan, 'room', scene)
    harness = prompts.build_multi_character_prompt_harness(characters=[Character(id='a', name='Aria')], recent_messages=[], user_message='SOURCE', scene_state=scene, command_overlay_plan=plan)
    for token in ['SHARED_WORLD', 'SHARED_ARC', 'SELECTED_LOCATION', 'SELECTED_MOOD', 'SELECTED_CONFLICT', 'SELECTED_EVENT', 'WORLD_TITLE', 'WORLD_DESCRIPTION']:
        assert harness.compiled_text.count(token) == 1
    assert 'trust=' not in harness.compiled_text


def test_tagged_nested_panels_do_not_salvage_nested_alias(mode):
    mode('tagged_text')
    plan = co.build_plan([command('first'), command('last')])
    raw = '<m c="C1">body</m><panels><panel k="K1"><panel k="K2"><note>nested</note></panel></panel></panels>'
    primary, panels, outcomes = co.split_response(raw, 'tagged_text', plan)
    assert primary == '<m c="C1">body</m>' and panels == {}
    assert all(v['status'] == 'fallback' for v in outcomes.values())


@pytest.mark.parametrize('protocol', ['tagged_text', 'free_body_json'])
def test_outer_fences_and_primary_alias_grammar_preserved(mode, protocol):
    mode(protocol)
    plan = co.build_plan([command()])
    raw = encode(protocol)
    if protocol == 'free_body_json':
        raw = raw.replace('"replies"', '"REPLIES"').replace('"who"', '"WHO"')
    primary, panels, _ = co.split_response('```text\n' + raw + '\n```', protocol, plan)
    assert panels
    assert fb.parse_response(primary, protocol=protocol, aliases={'C1': 'a'})[0].character_id == 'a'
