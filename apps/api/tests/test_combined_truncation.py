"""Provider LENGTH may belong to an auxiliary suffix, never a partial turn."""
import asyncio
import json

import pytest

from app.db.models import Character, Message
from app.engine import command_overlay as co
from app.engine.character_runtime import CharacterRuntime, CharacterRuntimeError
from app.engine.llm_client import LLMResponse
from app.services import chat_command_service as cs
from test_combined_command_runtime import Client, command, encode, mode


class RecordingClient(Client):
    def __init__(self, responses):
        super().__init__(responses)
        self.outcomes = []

    def record_response_outcome(self, response, **kwargs):
        self.outcomes.append(kwargs)


def truncated(protocol, body='First.<a>Moves.</a>Last.<t>Secret.</t>'):
    if protocol == 'tagged_text':
        return f'<m c="C1">{body}</m><panels><panel k="K1"><note>unfinished'
    return json.dumps({'replies': [{'who': 'C1', 'text': body}]})[:-1] + ',"panels":[{"key":"K1","blocks":[{"type":"note","text":"unfinished'


@pytest.mark.asyncio
@pytest.mark.parametrize('protocol', ['tagged_text', 'free_body_json'])
@pytest.mark.parametrize('finish', ['LENGTH', 'MAX_TOKENS'])
@pytest.mark.parametrize('fallback_bad', [False, True])
async def test_panel_only_truncation_preserves_primary_and_bounded_fallback(session, mode, protocol, finish, fallback_bad):
    mode(protocol)
    cmd = command()
    plan = co.build_plan([cmd])
    raw = truncated(protocol)
    provider = RecordingClient([LLMResponse(content=raw, finish_reason=finish)] * 2)
    seen, snapshots = [], []
    def persist(turn):
        seen.append(turn)
        return turn
    turn = await CharacterRuntime(provider).generate_replies(
        characters=[Character(id='a', name='Aria')], recent_messages=[], user_message='source',
        command_overlay_plan=plan, persist_replies=persist, prompt_snapshot_callback=snapshots.append)
    assert seen == [turn] and len(provider.calls) == 1
    assert turn[0].text == 'First.\nLast.' and turn[0].thought == 'Secret.'
    assert not turn.command_panels
    outcome = turn.command_outcomes['cmd']
    assert outcome['status'] == 'fallback' and outcome['reason'] == 'auxiliary_output_truncated'
    assert outcome['primary_integrity'] == 'validated_complete_before_auxiliary'
    assert provider.outcomes[-1]['validation_code'] == 'primary_ok_auxiliary_truncated'
    assert snapshots[0]['response_preview'] == raw
    assert snapshots[0]['metadata']['command_postprocess']['cmd'] == outcome
    assert plan.outcomes['cmd']['reason'] == 'missing_panel'
    source = Message(id='source', conversation_id='room', speaker_type='user', content='source')
    primary = Message(id='primary', conversation_id='room', speaker_type='character', speaker_id='a', content=turn[0].text, thought=turn[0].thought)
    payload = 'invalid' if fallback_bad else '{"messages":[{"content":"","blocks":[{"type":"note","text":"Recovered"}]}]}'
    fallback = Client([LLMResponse(content=payload)] * 2)
    result = await cs.apply_chat_command_postprocess(session=session, command=cmd, conversation_id='room',
        source_message=source, generated_messages=[primary], combined_messages=turn.command_panels.get('cmd'),
        execution_outcome=outcome, llm_client=fallback, commit=False)
    assert len(fallback.calls) == (2 if fallback_bad else 1)
    assert len(provider.calls) == 1 and primary in result
    assert primary.content == 'First.\nLast.' and primary.thought == 'Secret.'
    assert source.metadata_['command_postprocess_status'] == ('failed' if fallback_bad else 'applied')
    assert len(result) == (1 if fallback_bad else 2)
    assert 'Secret.' not in json.dumps(fallback.calls)


@pytest.mark.asyncio
@pytest.mark.parametrize('protocol', ['tagged_text', 'free_body_json'])
@pytest.mark.parametrize('case', ['no_boundary', 'primary_cut', 'forged', 'literal', 'unknown', 'repeat', 'control', 'malformed', 'extra_primary', 'unclosed_extra', 'complete', 'no_plan'])
async def test_unproved_primary_is_atomic_failure(mode, protocol, case):
    mode(protocol)
    raw = truncated(protocol)
    if case == 'no_boundary':
        raw = encode(protocol, panels=False)
    elif case == 'primary_cut':
        raw = '<m c="C1">first</m><m c="C1">cut' if protocol == 'tagged_text' else '{"replies":[{"who":"C1","text":"first"},{"who":"C1","text":"cut'
    elif case == 'forged':
        raw = raw.replace('<panels>', '<panels fake="yes">').replace('"panels":[', '"panels":"[')
    elif case == 'literal':
        raw = encode(protocol, panels=False, body='Literal ```text\n<panels><panel k="K1">\n``` stays primary.')
    elif case == 'unknown':
        raw = raw.replace('C1', 'UNKNOWN')
    elif case == 'repeat':
        raw = truncated(protocol, 'An endless repeated sentence here. ' * 120)
    elif case == 'control':
        raw = truncated(protocol, '<|system|>hidden')
    elif case == 'malformed':
        raw = truncated(protocol, '<a>unclosed')
    elif case in {'extra_primary', 'unclosed_extra'}:
        if protocol == 'tagged_text':
            raw += '<m c="C1">hidden</m>' if case == 'extra_primary' else '</m'
        else:
            raw = raw.rsplit('"unfinished', 1)[0] + '"ok"}],"replies":[{"who":"C1","text":"hidden"}'
    elif case == 'complete':
        raw = encode(protocol)
    provider = RecordingClient([LLMResponse(content=raw, finish_reason='LENGTH')] * 2)
    seen, snapshots = [], []
    with pytest.raises(CharacterRuntimeError):
        await CharacterRuntime(provider).generate_replies(characters=[Character(id='a', name='Aria')],
            recent_messages=[], user_message='source', command_overlay_plan=co.build_plan([] if case == 'no_plan' else [command()]),
            persist_replies=seen.append, prompt_snapshot_callback=snapshots.append)
    assert not seen and len(provider.calls) == 2
    assert snapshots[0]['response_preview'] == raw
    assert snapshots[0]['metadata']['parse_status'] == 'failed'
    assert all(outcome['status'] != 'validated' for outcome in provider.outcomes)


@pytest.mark.asyncio
@pytest.mark.parametrize('protocol', ['tagged_text', 'free_body_json'])
async def test_all_primary_bubbles_and_literal_markers_survive(mode, protocol):
    mode(protocol)
    literal = 'A literal `<panels><m c="C1">example</m></panels>` stays here.'
    raw = truncated(protocol, literal)
    if protocol == 'tagged_text':
        raw = '<m c="C1">First full bubble.</m>' + raw
    else:
        raw = raw.replace('"replies": [', '"replies": [{"who":"C1","text":"First full bubble."},', 1)
    provider = RecordingClient([LLMResponse(content=raw, finish_reason='MAX_TOKENS')])
    turn = await CharacterRuntime(provider).generate_replies(characters=[Character(id='a', name='Aria')],
        recent_messages=[], user_message='source', command_overlay_plan=co.build_plan([command()]))
    assert len(provider.calls) == 1
    assert [reply.text for reply in turn] == ['First full bubble.', literal]


@pytest.mark.asyncio
@pytest.mark.parametrize('finish', ['STOP', 'LENGTH'])
@pytest.mark.parametrize('key', ['replies', 'who', r'\u0072eplies'])
async def test_json_primary_fields_in_unclosed_auxiliary_fail(mode, finish, key):
    mode('free_body_json')
    raw = '{"replies":[{"who":"C1","text":"first"}],"panels":[{"key":"K1","blocks":[{"' + key + '":'
    provider = Client([LLMResponse(content=raw, finish_reason=finish)] * 2)
    seen = []
    with pytest.raises(CharacterRuntimeError):
        await CharacterRuntime(provider).generate_replies(characters=[Character(id='a', name='Aria')],
            recent_messages=[], user_message='source', command_overlay_plan=co.build_plan([command()]), persist_replies=seen.append)
    assert not seen and len(provider.calls) == 2


# Complete-but-invalid and irreparable syntax are not evidence of EOF truncation.
JSON_REJECT_SUFFIXES = [
    '[{"key":"K1","key":"K1","blocks":[]}]}',
    '[{"key":"K1","blocks":[{"type":"note","text":"x","text":"y"}]}]}',
    '[{"key":"K1","blocks":null}]}',
    '[{"key":"K1","blocks":[{"type":"unknown"}]}]}',
    '[{"key":"K1","blocks":[]}],"extra":',
    '[{"key":"K1",}]}',
    '[{"key":"K1","KEY":"K1","blocks":[]}]}',
    r'[{"key":"K1","\u006bey":"K1","blocks":[]}]}',
    '[01', '[1e+]', '[falsex', '[NaN', '[{"text":"bad\\u0X',
    '[{"key":"K1","blocks":invalid',
    '[{"key":"K1","key":"K1","blocks":[',
    '[{"key":"K1","blocks":[{"text":"bad\\q',
    '[{"key":"K1","blocks":[{"text":"bad\n',
    '[' * 2000,
    '[' * 2000 + ']' * 2000 + '}',
]
JSON_INCOMPLETE_SUFFIXES = [
    '[', '[{', '[{"key"', '[{"key":', '[{"key":"K1",',
    '[{"key":"K1","blocks":[',
    '[{"key":"K1","blocks":[{"type":"note","text":"unfinished',
    '[{"key":"K1","blocks":[{"type":"note","text":"escape\\',
    '[{"key":"K1","blocks":[{"type":"note","text":"unicode\\u12',
    '[{"key":"K1","blocks":[{"type":"note","text":"literal ] } and \\"who\\": stay quoted',
    '[{"key":"K1","blocks":[]} ',
    '[tru', '[fals', '[nul', '[-', '[1.', '[1e', '[1e+', '[1.2E-',
]
TAGGED_CLOSES = ['</panels>', '</panels >', '</panels\t>', '</panels\n >', '</PANELS >', '< / panels >']


def json_suffix(suffix):
    return '{"replies":[{"who":"C1","text":"First.<t>Secret.</t>"}],"panels":' + suffix


@pytest.mark.asyncio
@pytest.mark.parametrize('finish', ['LENGTH', 'MAX_TOKENS'])
@pytest.mark.parametrize('suffix,recover', [
    pytest.param(s, False, id=f'reject-{i}') for i, s in enumerate(JSON_REJECT_SUFFIXES)
] + [
    pytest.param(s, True, id=f'incomplete-{i}') for i, s in enumerate(JSON_INCOMPLETE_SUFFIXES)
])
async def test_json_completeness_proof_runtime(mode, finish, suffix, recover):
    mode('free_body_json')
    provider = RecordingClient([LLMResponse(content=json_suffix(suffix), finish_reason=finish)] * 2)
    seen = []
    async def run():
        return await CharacterRuntime(provider).generate_replies(
            characters=[Character(id='a', name='Aria')], recent_messages=[], user_message='source',
            command_overlay_plan=co.build_plan([command()]), persist_replies=seen.append)
    if recover:
        await run()
        assert len(seen) == 1 and len(provider.calls) == 1
        assert seen[0][0].text == 'First.' and seen[0][0].thought == 'Secret.'
        assert seen[0].command_outcomes['cmd']['reason'] == 'auxiliary_output_truncated'
    else:
        with pytest.raises(CharacterRuntimeError):
            await run()
        assert not seen and len(provider.calls) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize('close', TAGGED_CLOSES)
@pytest.mark.parametrize('finish', ['LENGTH', 'MAX_TOKENS'])
async def test_tagged_complete_close_variants_reject(mode, close, finish):
    mode('tagged_text')
    raw = encode('tagged_text').replace('</panels>', close)
    provider = Client([LLMResponse(content=raw, finish_reason=finish)] * 2)
    seen = []
    with pytest.raises(CharacterRuntimeError):
        await CharacterRuntime(provider).generate_replies(
            characters=[Character(id='a', name='Aria')], recent_messages=[], user_message='source',
            command_overlay_plan=co.build_plan([command()]), persist_replies=seen.append)
    assert not seen and len(provider.calls) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize('literal', ['`</panels >`', '```xml\n</panels\t>\n```', r'\</panels >', '&lt;/panels &gt;'])
async def test_tagged_literal_closes_are_not_completion(mode, literal):
    mode('tagged_text')
    raw = truncated('tagged_text') + literal
    provider = Client([LLMResponse(content=raw, finish_reason='LENGTH')])
    turn = await CharacterRuntime(provider).generate_replies(
        characters=[Character(id='a', name='Aria')], recent_messages=[], user_message='source',
        command_overlay_plan=co.build_plan([command()]))
    assert len(provider.calls) == 1 and turn[0].thought == 'Secret.'
    assert turn.command_outcomes['cmd']['reason'] == 'auxiliary_output_truncated'


@pytest.mark.asyncio
@pytest.mark.parametrize('suffix', ['<note>bad</table>', '<panel k="K1" k="K1">', '<!DOCTYPE panels>', '<note>&bogus;</note>', '<note>&bogus', '</wrong', '<panel k="K1" k="K1"'])
async def test_tagged_irreparable_suffix_is_not_truncation(mode, suffix):
    mode('tagged_text')
    provider = Client([LLMResponse(content='<m c="C1">First.</m><panels>' + suffix, finish_reason='LENGTH')] * 2)
    seen = []
    with pytest.raises(CharacterRuntimeError):
        await CharacterRuntime(provider).generate_replies(
            characters=[Character(id='a', name='Aria')], recent_messages=[], user_message='source',
            command_overlay_plan=co.build_plan([command()]), persist_replies=seen.append)
    assert not seen and len(provider.calls) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize('suffix', [
    pytest.param(s, id=f'invalid-{i}') for i, s in enumerate(JSON_REJECT_SUFFIXES) if not s.endswith('"extra":')
])
async def test_nontruncated_invalid_json_keeps_panel_only_fallback(mode, suffix):
    # A complete value with trailing envelope fields remains a whole-turn error.
    mode('free_body_json')
    provider = Client([LLMResponse(content=json_suffix(suffix), finish_reason='STOP')])
    turn = await CharacterRuntime(provider).generate_replies(
        characters=[Character(id='a', name='Aria')], recent_messages=[], user_message='source',
        command_overlay_plan=co.build_plan([command()]))
    assert len(provider.calls) == 1 and turn[0].text == 'First.'
    assert not turn.command_panels and turn.command_outcomes['cmd']['status'] == 'fallback'


@pytest.mark.parametrize('protocol', ['tagged_text', 'free_body_json'])
@pytest.mark.parametrize('fenced_control', [False, True])
def test_complete_invalid_length_job_rejects_without_persistence(client, session, monkeypatch, mode, protocol, fenced_control):
    from app.api.conversations import _run_message_generation_job_in_session
    from app.db.models import Conversation, PostCommitTask
    from app.engine.llm_client import LLMClient
    from sqlmodel import select
    mode(protocol)
    monkeypatch.setattr('app.api.conversations._start_message_generation_worker', lambda *a: None)
    actor = client.post('/characters', json={'name': 'Aria', 'persona': 'Synthetic adult.'}).json()
    room = client.post('/conversations', json={'mode': 'user_character', 'participants': [{'type': 'character', 'id': actor['id']}]}).json()['id']
    cmd = client.post('/chat-commands', json={'name': 'proof', 'postprocess_prompt': 'Synthetic', 'generation_prompt': 'Natural.', 'postprocess_target': 'last_bubble', 'postprocess_block_types': ['note']}).json()
    cs.set_active_command_ids(session, session.get(Conversation, room), [cmd['id']])
    raw = (encode(protocol).replace('</panels>', '</panels >') if protocol == 'tagged_text'
           else json_suffix(JSON_REJECT_SUFFIXES[0]))
    if fenced_control:
        fence = 'xml' if protocol == 'tagged_text' else 'json'
        raw = f'```{fence}\n' + truncated(protocol) + '\x0b\n```'
    calls = []
    async def chat(self, messages, response_format=None, **kwargs):
        calls.append(self.purpose)
        return LLMResponse(content=raw, finish_reason='LENGTH')
    monkeypatch.setattr(LLMClient, 'chat', chat)
    response = client.post(f'/conversations/{room}/messages/jobs', json={'speaker_type': 'user', 'speaker_id': 'u', 'content': 'source'}).json()
    job_id = response['job']['id']
    asyncio.run(_run_message_generation_job_in_session(session, job_id))
    status = client.get(f'/conversations/{room}/generation-jobs/{job_id}').json()
    messages = client.get(f'/conversations/{room}/messages').json()
    assert status['status'] == 'failed' and not status['generated_message_ids']
    assert [m['id'] for m in messages] == [response['incoming_message']['id']]
    assert calls == ['chat_generation', 'chat_generation']
    assert not session.exec(select(PostCommitTask)).all()


@pytest.mark.asyncio
@pytest.mark.parametrize('protocol', ['tagged_text', 'free_body_json'])
async def test_every_auxiliary_token_cut_keeps_valid_primary(mode, protocol):
    mode(protocol)
    if protocol == 'tagged_text':
        prefix = '<m c="C1">First.<t>Secret.</t></m><panels>'
        tail = '<panel k="K1"><note title="Status">Ready &amp; steady &#32; &#x20;.</note></panel></panels>'
        cuts = range(len(tail))
    else:
        prefix = json_suffix('[')
        tail = '{"key":"K1","blocks":[{"type":"note","text":"Ready \\u0020 steady"}]}]}'
        cuts = range(len(tail) - 1)  # The closed panels array is NOT incomplete.
    for cut in cuts:
        raw = prefix + tail[:cut]
        provider = Client([LLMResponse(content=raw, finish_reason='LENGTH')] * 2)
        seen = []
        try:
            await CharacterRuntime(provider).generate_replies(
                characters=[Character(id='a', name='Aria')], recent_messages=[], user_message='source',
                command_overlay_plan=co.build_plan([command()]), persist_replies=seen.append)
        except CharacterRuntimeError:
            pytest.fail(f'Valid auxiliary prefix incorrectly rejected: {tail[:cut]!r}')
        assert len(provider.calls) == 1 and len(seen) == 1
        assert seen[0][0].text == 'First.' and seen[0][0].thought == 'Secret.'
        assert not seen[0].command_panels
        assert seen[0].command_outcomes['cmd']['reason'] == 'auxiliary_output_truncated'


@pytest.mark.asyncio
@pytest.mark.parametrize('fenced', [False, True])
@pytest.mark.parametrize('control', [chr(i) for i in range(32)], ids=lambda value: f'U+{ord(value):04X}')
async def test_fenced_payload_preserves_illegal_json_string_controls(mode, fenced, control):
    mode('free_body_json')
    raw = truncated('free_body_json') + control
    if fenced:
        raw = '```json\n' + raw + '\n```'
    provider = Client([LLMResponse(content=raw, finish_reason='LENGTH')] * 2)
    seen = []
    with pytest.raises(CharacterRuntimeError):
        await CharacterRuntime(provider).generate_replies(
            characters=[Character(id='a', name='Aria')], recent_messages=[], user_message='source',
            command_overlay_plan=co.build_plan([command()]), persist_replies=seen.append)
    assert len(provider.calls) == 2 and not seen


@pytest.mark.asyncio
@pytest.mark.parametrize('protocol', ['tagged_text', 'free_body_json'])
@pytest.mark.parametrize('incomplete', [False, True])
async def test_valid_fenced_payload_keeps_complete_or_proven_incomplete_behavior(mode, protocol, incomplete):
    mode(protocol)
    payload = truncated(protocol) if incomplete else encode(protocol)
    raw = ('```xml\n' if protocol == 'tagged_text' else '```json\n') + payload + '\n```'
    provider = Client([LLMResponse(content=raw, finish_reason='LENGTH' if incomplete else 'STOP')])
    seen, snapshots = [], []
    def persist(replies):
        seen.append(replies)
        return replies
    turn = await CharacterRuntime(provider).generate_replies(
        characters=[Character(id='a', name='Aria')], recent_messages=[], user_message='source',
        command_overlay_plan=co.build_plan([command()]), persist_replies=persist,
        prompt_snapshot_callback=snapshots.append)
    assert len(provider.calls) == 1 and seen == [turn]
    assert turn.command_outcomes['cmd']['status'] == ('fallback' if incomplete else 'combined_success')
    assert snapshots[0]['response_preview'] == raw

