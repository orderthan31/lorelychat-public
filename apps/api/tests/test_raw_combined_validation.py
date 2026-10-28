"""Raw syntax admission through the runtime and isolated durable job boundary."""
import asyncio
import json

import pytest
from sqlmodel import select

from app.db.models import Character, Conversation, LLMPromptSnapshot, PostCommitTask
from app.engine import command_overlay as co
from app.engine.character_runtime import CharacterRuntime, CharacterRuntimeError
from app.engine.llm_client import LLMClient, LLMResponse
from app.services import chat_command_service as cs
from test_combined_command_runtime import Client, command, mode
from test_combined_truncation import truncated


JSON_WS = ' \t\r\n'
SYNTAX_CHARS = tuple(dict.fromkeys(
    [chr(i) for i in range(32)] + [' '] +
    [chr(i) for i in range(128, 0x110000) if chr(i).isspace()]
))
JSON_REGIONS = (
    ('', '{"replies":[{"who":"C1","text":"First."}],"panels":['),
    ('{', '"replies":[{"who":"C1","text":"First."}],"panels":['),
    ('{"replies"', ':[{"who":"C1","text":"First."}],"panels":['),
    ('{"replies":', '[{"who":"C1","text":"First."}],"panels":['),
    ('{"replies":[', '{"who":"C1","text":"First."}],"panels":['),
    ('{"replies":[{', '"who":"C1","text":"First."}],"panels":['),
    ('{"replies":[{"who"', ':"C1","text":"First."}],"panels":['),
    ('{"replies":[{"who":', '"C1","text":"First."}],"panels":['),
    ('{"replies":[{"who":"C1"', ',"text":"First."}],"panels":['),
    ('{"replies":[{"who":"C1",', '"text":"First."}],"panels":['),
    ('{"replies":[{"who":"C1","text":"First."', '}],"panels":['),
    ('{"replies":[{"who":"C1","text":"First."}', '],"panels":['),
    ('{"replies":[{"who":"C1","text":"First."}]', ',"panels":['),
    ('{"replies":[{"who":"C1","text":"First."}],', '"panels":['),
    ('{"replies":[{"who":"C1","text":"First."}],"panels"', ':['),
    ('{"replies":[{"who":"C1","text":"First."}],"panels":', '['),
    ('{"replies":[{"who":"C1","text":"First."}],"panels":[', ''),
    ('{"replies":[{"who":"C1","text":"First."}],"panels":[{', '"key":"K1"'),
    ('{"replies":[{"who":"C1","text":"First."}],"panels":[{"key"', ':"K1"'),
    ('{"replies":[{"who":"C1","text":"First."}],"panels":[{"key":', '"K1"'),
    ('{"replies":[{"who":"C1","text":"First."}],"panels":[{"key":"K1"', ',"blocks":['),
    ('{"replies":[{"who":"C1","text":"First."}],"panels":[{"key":"K1",', '"blocks":['),
    ('{"replies":[{"who":"C1","text":"First."}],"panels":[{"key":"K1","blocks"', ':['),
    ('{"replies":[{"who":"C1","text":"First."}],"panels":[{"key":"K1","blocks":', '['),
)


def fenced(raw, enabled, protocol):
    return ('```' + ('json' if protocol == 'free_body_json' else 'xml') + '\n' + raw + '\n```') if enabled else raw


async def runtime_assert(raw, protocol, recover, expected_body=None):
    provider = Client([LLMResponse(content=raw, finish_reason='LENGTH')] * 2)
    seen, snapshots = [], []
    def persist(turn):
        seen.append(turn)
        return turn
    async def run():
        return await CharacterRuntime(provider).generate_replies(
            characters=[Character(id='a', name='Aria')], recent_messages=[], user_message='source',
            command_overlay_plan=co.build_plan([command()]), persist_replies=persist,
            prompt_snapshot_callback=snapshots.append)
    if recover:
        turn = await run()
        assert len(seen) == 1 and len(provider.calls) == 1
        assert not turn.command_panels
        assert turn.command_outcomes['cmd']['reason'] == 'auxiliary_output_truncated'
        if expected_body is not None:
            assert turn[0].raw_body == expected_body
    else:
        with pytest.raises(CharacterRuntimeError):
            await run()
        assert not seen and len(provider.calls) == 2
        assert snapshots[0]['metadata']['parse_status'] == 'failed'
    assert snapshots[0]['response_preview'] == raw


@pytest.mark.asyncio
@pytest.mark.parametrize('char', SYNTAX_CHARS, ids=lambda c: f'U+{ord(c):04X}')
@pytest.mark.parametrize('wrapped', [False, True])
async def test_original_json_syntax_regions(mode, char, wrapped):
    mode('free_body_json')
    for before, after in JSON_REGIONS:
        await runtime_assert(fenced(before + char + after, wrapped, 'free_body_json'),
                             'free_body_json', char in JSON_WS)


@pytest.mark.asyncio
@pytest.mark.parametrize('protocol', ['free_body_json', 'tagged_text'])
@pytest.mark.parametrize('char', SYNTAX_CHARS, ids=lambda c: f'U+{ord(c):04X}')
async def test_fence_syntax_is_not_a_broad_whitespace_sink(mode, protocol, char):
    mode(protocol)
    raw = truncated(protocol)
    label = 'json' if protocol == 'free_body_json' else 'xml'
    for candidate in (char + fenced(raw, True, protocol), fenced(raw, True, protocol) + char,
                      '```' + label + char + '\n' + raw + '\n```'):
        await runtime_assert(candidate, protocol, char in JSON_WS)


@pytest.mark.asyncio
@pytest.mark.parametrize('char', SYNTAX_CHARS, ids=lambda c: f'U+{ord(c):04X}')
@pytest.mark.parametrize('wrapped', [False, True])
async def test_original_tagged_structural_whitespace(mode, char, wrapped):
    mode('tagged_text')
    for raw in (
        char + '<m c="C1">First.</m><panels>',
        '<m' + char + 'c="C1">First.</m><panels>',
        '<m c' + char + '="C1">First.</m><panels>',
        '<m c="C1">First.</m' + char + '><panels>',
        '<m c="C1">First.</m>' + char + '<panels>',
        '<m c="C1">First.</m><panels><panel' + char + 'k="K1">',
    ):
        await runtime_assert(fenced(raw, wrapped, 'tagged_text'), 'tagged_text', char in JSON_WS)


@pytest.mark.asyncio
@pytest.mark.parametrize('char', [chr(i) for i in range(32)], ids=lambda c: f'U+{ord(c):04X}')
@pytest.mark.parametrize('wrapped', [False, True])
async def test_controls_cannot_hide_in_tagged_literal_regions(mode, char, wrapped):
    mode('tagged_text')
    for raw in (
        '<m c="C1">First.`literal' + char + '`</m><panels>',
        '<m c="C1">First.</m><panels><panel k="K1"><note>`literal' + char + '`',
        '<m c="C1">First.</m><panels><panel k="K1"><note>\\<' + char,
    ):
        await runtime_assert(fenced(raw, wrapped, 'tagged_text'), 'tagged_text', char in '\t\r\n')


@pytest.mark.asyncio
@pytest.mark.parametrize('wrapped', [False, True])
@pytest.mark.parametrize('bad', [
    '<m c="C1" `junk`>First.</m><panels>',
    '<m c="C1">First.</m><panels><panel k="K1" `junk`>',
    '<m c="C1">First.</m><panels><panel k="K1`\"`">',
    '<m c="C1">First.</m><panels><panel k="K1" k="K1"',
    '<m c="C1">First.</m><panels><note>&#0;',
    '<m c="C1">First.</m><panels><note>&#x0;',
    '<m c="C1">First.</m><panels><note>bad &bogus;',
    '<m c="C1">First.</m><panels><note><m c="C1">hidden',
    '<m c="UNKNOWN">First.</m><panels>',
])
async def test_tagged_literal_handling_cannot_rewrite_structural_tokens(mode, wrapped, bad):
    mode('tagged_text')
    await runtime_assert(fenced(bad, wrapped, 'tagged_text'), 'tagged_text', False)


@pytest.mark.asyncio
@pytest.mark.parametrize('wrapped', [False, True])
@pytest.mark.parametrize('bad', [
    '{"replies":[{"who":"C1","text":"bad\\q"}],"panels":[',
    '{"replies":[{"who":"C1","text":"bad\\u0X00"}],"panels":[',
    '{"replies":[{"who":"C1","text":"First.","emotion":NaN}],"panels":[',
    '{"replies":[{"who":"C1","text":"First.","emotion":01}],"panels":[',
    '{"replies":[{"who":"C1","text":"First.","emotion":truex}],"panels":[',
    '{"replies":[{"who":"C1","WHO":"C1","text":"First."}],"panels":[',
    '{"replies":[{"who":"C1","text":"First."}],"panels":[{"\\u0077ho":',
    '{"replies":[{"who":"C1","text":"First."}],"panels":[]',
    '{"replies":[{"who":"C1","text":"First."}],"panels":[],"replies":[',
    '{"replies":[{"who":"C1","text":"First."}],"panels":[{"x":1,"X":',
])
async def test_whole_json_lexical_and_envelope_errors(mode, wrapped, bad):
    mode('free_body_json')
    await runtime_assert(fenced(bad, wrapped, 'free_body_json'), 'free_body_json', False)


@pytest.mark.asyncio
@pytest.mark.parametrize('protocol', ['free_body_json', 'tagged_text'])
@pytest.mark.parametrize('wrapped', [False, True])
async def test_original_prose_and_literal_code_are_not_xml_or_normalized(mode, protocol, wrapped):
    mode(protocol)
    prose = '  Bread & butter; x < y; `literal <m c="C9"> & </panels>`\n<t>Private & unspoken.</t>\u00a0end.  '
    await runtime_assert(fenced(truncated(protocol, prose), wrapped, protocol), protocol, True, prose)


@pytest.mark.parametrize('protocol,case', [
    ('free_body_json', 'boundary'), ('free_body_json', 'prefix'),
    ('free_body_json', 'fence'), ('tagged_text', 'literal-control'),
    ('tagged_text', 'attribute-code'),
    ('free_body_json', 'valid'), ('tagged_text', 'valid'),
    ('free_body_json', 'fallback-failed'), ('tagged_text', 'fallback-failed'),
])
def test_raw_admission_job_readback(client, session, monkeypatch, mode, protocol, case):
    from app.api.conversations import _run_message_generation_job_in_session
    mode(protocol)
    monkeypatch.setattr('app.api.conversations._start_message_generation_worker', lambda *a: None)
    actor = client.post('/characters', json={'name': 'Aria', 'persona': 'Synthetic adult.'}).json()
    room = client.post('/conversations', json={'mode': 'user_character', 'participants': [{'type': 'character', 'id': actor['id']}]}).json()['id']
    cmd = client.post('/chat-commands', json={'name': 'raw-proof', 'postprocess_prompt': 'Synthetic', 'generation_prompt': 'Natural.', 'postprocess_target': 'last_bubble', 'postprocess_block_types': ['note']}).json()
    cs.set_active_command_ids(session, session.get(Conversation, room), [cmd['id']])
    raw = truncated(protocol)
    if case == 'boundary':
        raw = raw.replace(',"panels":', ',"panels":\x0b')
    elif case == 'prefix':
        raw = '\u00a0' + raw
    elif case == 'literal-control':
        raw += '`hidden\x0b`'
    elif case == 'attribute-code':
        raw = raw.replace('<panel k="K1">', '<panel k="K1" `hidden`>')
    raw = fenced(raw, True, protocol)
    if case == 'fence':
        raw = '\x0b' + raw
    calls = []
    async def chat(self, messages, response_format=None, **kwargs):
        calls.append(self.purpose)
        if self.purpose == 'chat_command_postprocess':
            return LLMResponse(content='invalid' if case == 'fallback-failed' else
                               '{"messages":[{"content":"","blocks":[{"type":"note","text":"Recovered"}]}]}')
        return LLMResponse(content=raw, finish_reason='LENGTH')
    monkeypatch.setattr(LLMClient, 'chat', chat)
    response = client.post(f'/conversations/{room}/messages/jobs', json={'speaker_type': 'user', 'speaker_id': 'u', 'content': 'source'}).json()
    job_id = response['job']['id']
    asyncio.run(_run_message_generation_job_in_session(session, job_id))
    status = client.get(f'/conversations/{room}/generation-jobs/{job_id}').json()
    messages = client.get(f'/conversations/{room}/messages').json()
    source = next(m for m in messages if m['id'] == response['incoming_message']['id'])
    assert source['content'] == 'source'
    snapshots = session.exec(select(LLMPromptSnapshot).where(
        LLMPromptSnapshot.source_message_id == source['id'])).all()
    assert len(snapshots) == 1 and snapshots[0].response_preview == raw
    assert snapshots[0].metadata_['finish_reason'] == 'LENGTH'
    if case not in {'valid', 'fallback-failed'}:
        assert snapshots[0].metadata_['parse_status'] == 'failed'
        assert status['status'] == 'failed' and not status['generated_message_ids']
        assert [m['id'] for m in messages] == [source['id']]
        assert calls == ['chat_generation', 'chat_generation']
        assert not session.exec(select(PostCommitTask)).all()
    else:
        assert status['status'] == 'completed'
        assert snapshots[0].metadata_['parse_status'] == 'success'
        primary = [m for m in messages if m['speaker_type'] == 'character']
        assert len(primary) == 1
        assert primary[0]['content'] == 'First.\nLast.' and primary[0]['thought'] == 'Secret.'
        assert calls == ['chat_generation'] + ['chat_command_postprocess'] * (2 if case == 'fallback-failed' else 1)
        assert source['metadata']['command_postprocess_status'] == ('failed' if case == 'fallback-failed' else 'applied')
        prior_calls = list(calls)
        asyncio.run(_run_message_generation_job_in_session(session, job_id))
        assert calls == prior_calls
        assert client.get(f'/conversations/{room}/messages').json() == messages


@pytest.mark.asyncio
@pytest.mark.parametrize('protocol', ['free_body_json', 'tagged_text'])
@pytest.mark.parametrize('char', [c for c in SYNTAX_CHARS if ord(c) >= 32], ids=lambda c: f'U+{ord(c):04X}')
async def test_unicode_whitespace_is_data_inside_strings_and_prose(mode, protocol, char):
    mode(protocol)
    prose = 'First.' + char + 'Last.<t>Secret.</t>'
    raw = truncated(protocol, prose) + char
    await runtime_assert(fenced(raw, True, protocol), protocol, True, prose)


@pytest.mark.asyncio
@pytest.mark.parametrize('literal', [
    '`<m c="UNKNOWN">&bogus;</m> </panels>`',
    '```xml\n<panel k="one" k="two"> & x < y\n```',
    r'\<m c="UNKNOWN"> literal', '&lt;m c="UNKNOWN"&gt;',
])
async def test_auxiliary_text_literal_domain_preserves_ordinary_examples(mode, literal):
    mode('tagged_text')
    await runtime_assert(truncated('tagged_text') + literal, 'tagged_text', True)


@pytest.mark.parametrize('protocol', ['free_body_json', 'tagged_text'])
def test_proof_retains_exact_payload_and_never_returns_witness(protocol):
    from app.engine.combined_prefix_validation import validate_auxiliary_cut
    payload = '\n\t' + truncated(protocol) + ('\\u12' if protocol == 'free_body_json' else '&am')
    proof = validate_auxiliary_cut(fenced(payload, True, protocol), protocol)
    assert proof.payload == payload
    assert proof.payload[proof.auxiliary_start:].startswith('[' if protocol == 'free_body_json' else '<panels>')
    assert proof.payload[:proof.primary_end].endswith(']' if protocol == 'free_body_json' else '</m>')


@pytest.mark.asyncio
@pytest.mark.parametrize('protocol', ['free_body_json', 'tagged_text'])
async def test_truncated_admission_does_not_reenter_legacy_splitters(mode, monkeypatch, protocol):
    mode(protocol)
    def forbidden(*args, **kwargs):
        pytest.fail('Truncation must not use a destructive legacy splitter')
    monkeypatch.setattr(co, '_split_json', forbidden)
    monkeypatch.setattr(co, '_split_tagged', forbidden)
    await runtime_assert(truncated(protocol), protocol, True)
    await runtime_assert('\x0b' + truncated(protocol), protocol, False)
