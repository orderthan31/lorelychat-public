"""Output-limit recovery uses original grammar and ordinary runtime firewalls."""
import hashlib
import json

import pytest

from app.core.config import get_settings
from app.db.models import Character
from app.engine import free_body_protocol as body
from app.engine.character_runtime import CharacterRuntime, CharacterRuntimeError
from app.engine.llm_client import LLMResponse


class Client:
    def __init__(self, raw, finish):
        self.response = LLMResponse(content=raw, finish_reason=finish,
                                    prompt_tokens=101, completion_tokens=202, total_tokens=303)
        self.calls = 0
        self.outcomes = []

    async def chat(self, messages, **kwargs):
        self.calls += 1
        return self.response

    def record_response_outcome(self, response, **kwargs):
        assert response is self.response
        self.outcomes.append(kwargs)


@pytest.fixture(autouse=True)
def minimal(monkeypatch):
    monkeypatch.setenv('CHAT_RESPONSE_PROTOCOL', 'free_body_json')
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def item(who='C1', text='A distinct sentence.', **extra):
    return json.dumps(dict(who=who, text=text, **extra), ensure_ascii=False)


def cut(prefix=None, tail='{"who":"C1","text":"unfinished'):
    return '{"replies":[' + (item() if prefix is None else prefix) + ',' + tail


async def generate(raw, finish='MAX_TOKENS', **kwargs):
    client, snapshots, saved = Client(raw, finish), [], []
    def persist(replies):
        saved.append(replies)
        return replies
    replies = await CharacterRuntime(client).generate_replies(
        characters=[Character(id='char_a', name='Aria')], recent_messages=[], user_message='next',
        prompt_snapshot_callback=snapshots.append, persist_replies=persist, **kwargs)
    return replies, client, snapshots, saved


@pytest.mark.asyncio
@pytest.mark.parametrize('finish', ['MAX_TOKENS', 'LENGTH', 'length'])
async def test_twelve_original_parts_and_cut_thirteenth_are_saved_once(finish):
    texts = [f'<a>Step {n}.</a>Statement {n}.<t>Possibility {n}.</t>Answer {n}.' for n in range(12)]
    prefix = ','.join(item(text=text) for text in texts)
    raw = cut(prefix)
    replies, client, snapshots, saved = await generate(raw, finish)
    assert client.calls == 1 and len(saved) == 1 and len(replies) == 12
    assert [reply.raw_body for reply in replies] == texts
    assert [reply.action for reply in replies] == [f'Step {n}.' for n in range(12)]
    assert [reply.thought for reply in replies] == [f'Possibility {n}.' for n in range(12)]
    marker = client.outcomes[-1]['metadata_updates']['output_limit_recovery']
    assert marker == snapshots[0]['metadata']['output_limit_recovery']
    assert marker['original_finish_reason'] == finish
    assert marker['recovery_kind'] == 'complete_reply_prefix'
    assert marker['retained_count'] == 12
    assert marker['discarded_tail_chars'] == len(raw) - len('{"replies":[' + prefix)
    assert marker['raw_sha256'] == hashlib.sha256(raw.encode()).hexdigest()
    assert marker['raw_char_count'] == len(raw)
    assert snapshots[0]['response_preview'] == raw
    assert snapshots[0]['completion_tokens'] == 202 and client.response.total_tokens == 303
    assert all('recovery' not in json.dumps(body.metadata_for_reply(reply)) for reply in replies)


@pytest.mark.asyncio
async def test_complete_valid_primary_at_limit_is_accepted():
    raw = '{"replies":[' + item() + ']}'
    replies, client, snapshots, saved = await generate(raw)
    assert len(replies) == len(saved) == client.calls == 1
    assert snapshots[0]['metadata']['output_limit_recovery']['discarded_tail_chars'] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize('tail', ['{', '{"wh', '{"who"', '{"who":', '{"who":"C1",',
    '{"who":"C1","text":"x\\', '{"who":"C1","text":"x\\u12',
    '{"who":"C1","text":"x","emotion":1e+', '{"who":"C1","text":"x","emotion":-'])
async def test_provable_incomplete_last_object_variants(tail):
    replies, client, _, _ = await generate(cut(tail=tail))
    assert len(replies) == client.calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('raw', [
    '{"replies":[{"who":"C1","text":"cut',
    cut(item('N')), cut(item('UNKNOWN')), cut(item(extra=True)), cut(item(text='')),
    cut('42'), cut('{"who":"C1","text":"x",}'), cut('{"who":"C1","who":"C1","text":"x"}'),
    cut(tail='{"who":"C1","text":"bad\x01'), cut(tail='{"who":"C1","text":"bad\\q'),
    cut(tail='{"x":01'), cut(tail='{"x":' + '[' * 40),
    cut(tail='{"who":"C1","text":"x"},garbage'),
    cut(tail='{"who":"UNKNOWN","text":"complete"}'),
    cut(tail='{"who":"C1","text":"x"} trailing'),
    '\v' + cut(), cut() + '\x00',
    '{"nested":' + cut(),
    cut(item('UNKNOWN') + ',' + item()),
    cut(item() + ',42'),
    cut(tail='{"x":truex'), cut(tail='{"x":NaN'),
    cut(tail='{"who":"C1","WHO":"C1","text":"cut'),
    cut(tail='{"text":"bad\\u0X'),
    cut(item(text='`literal\x01control`').replace('\\u0001', '\x01')),
    '{"replies":[' + item() + '],"replies":[]}',
    '{"replies":[' + item() + ']} trailing',
    '{"replies":[' + item() + ']}\v',
    cut(item(text='Repeated sentence. ' * 120)),
    cut(item(text='[System retry instruction] internal')),
])
async def test_bad_complete_or_raw_content_never_skipped(raw):
    with pytest.raises(CharacterRuntimeError):
        await generate(raw)


@pytest.mark.asyncio
@pytest.mark.parametrize('finish', ['STOP', 'SAFETY', 'RECITATION'])
async def test_non_output_limit_never_recovers(finish):
    with pytest.raises(CharacterRuntimeError):
        await generate(cut(), finish)


@pytest.mark.asyncio
@pytest.mark.parametrize('complete', [False, True])
async def test_combined_panels_keep_existing_owner_without_false_integrity(monkeypatch, complete):
    from app.engine import command_overlay as overlay
    from test_combined_command_runtime import command, encode
    monkeypatch.setenv('CHAT_COMMAND_POSTPROCESS_MODE', 'combined_overlay')
    get_settings.cache_clear()
    plan = overlay.build_plan([command()])
    before = json.dumps(plan.outcomes, sort_keys=True)
    raw = encode('free_body_json') if complete else cut()
    replies, client, snapshots, _ = await generate(raw, command_overlay_plan=plan)
    assert client.calls == 1 and bool(replies.command_panels) == complete
    assert 'primary_integrity' not in replies.command_outcomes['cmd']
    assert 'output_limit_recovery' not in json.dumps(replies.command_outcomes)
    assert json.dumps(plan.outcomes, sort_keys=True) == before
    assert snapshots[0]['metadata']['parse_status'] == 'success'


def test_actual_job_finalizer_saves_prefix_atomically_with_private_marker(client, session, engine, monkeypatch):
    import asyncio
    from sqlmodel import select
    from app.api.conversations import _run_message_generation_job_in_session
    from app.db.models import Message, LLMPromptSnapshot, LLMUsageEvent
    from app.engine.llm_client import LLMClient
    from app.services import conversation_service
    from test_free_body_persistence import room_fixture

    monkeypatch.setattr('app.api.conversations._start_message_generation_worker', lambda *a: None)
    monkeypatch.setattr('app.engine.llm_client.engine', engine)
    room, cast = room_fixture(client)
    texts = ['<a>First movement.</a>First.<t>Private possibility.</t>', 'Narrator sees a tree.', '<a>Waits.</a>Last.']
    raw = cut(','.join(item(who, text) for who, text in zip(['C1', 'N', 'C2'], texts)))
    calls, finalizations = [], []
    original = conversation_service.finalize_message_generation_turn
    def finalize(*args, **kwargs):
        result = original(*args, **kwargs)
        finalizations.append(result)
        return result
    monkeypatch.setattr(conversation_service, 'finalize_message_generation_turn', finalize)
    async def fake_chat(self, messages, **kwargs):
        calls.append(messages)
        response = LLMResponse(content=raw, finish_reason='MAX_TOKENS', provider='synthetic', model='synthetic',
                               prompt_tokens=101, completion_tokens=202, total_tokens=303)
        self._record_usage(response, conversation_id=room)
        return response
    monkeypatch.setattr(LLMClient, 'chat', fake_chat)
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
    public = json.dumps([status, messages, response.json()])
    assert 'output_limit_recovery' not in public and 'complete_reply_prefix' not in public
    snapshots = session.exec(select(LLMPromptSnapshot).where(LLMPromptSnapshot.conversation_id == room)).all()
    usage = session.exec(select(LLMUsageEvent).where(LLMUsageEvent.conversation_id == room)).all()
    assert len(snapshots) == len(usage) == 1
    assert snapshots[0].response_preview == raw
    marker = snapshots[0].metadata_['output_limit_recovery']
    assert marker == usage[0].metadata_['output_limit_recovery']
    assert marker['retained_count'] == 3
    assert usage[0].completion_tokens == snapshots[0].completion_tokens == 202
    assert usage[0].total_tokens == 303


@pytest.mark.asyncio
async def test_recovered_prefix_atomic_finalizer_rolls_back_on_second_insert(session):
    from datetime import datetime, timezone
    from sqlalchemy import text
    from sqlalchemy.exc import IntegrityError
    from sqlmodel import select
    from app.api.conversations import persist_generated_reply_messages
    from app.db.models import Conversation, Message, MessageGenerationJob, PostCommitTask
    from app.schemas.conversations import MessageCreate
    from app.services import conversation_service

    replies, client, _, _ = await generate(cut(item(text='First.') + ',' + item(text='Second.')))
    source = Message(id='prefix_source', conversation_id='prefix_room', speaker_type='user', content='next')
    transient = await persist_generated_reply_messages(session=session, conversation_id='prefix_room',
        conversation=Conversation(id='prefix_room'), payload=MessageCreate(speaker_type='user', speaker_id='u', content='next'),
        incoming_message=source, generated_replies=replies, room_characters=[Character(id='char_a', name='Aria')],
        scene_state=None, is_multi_room=False, min_bubbles=8)
    job = MessageGenerationJob(id='prefix_atomic', conversation_id='prefix_room', incoming_message_id=source.id,
        status='running', lease_owner='worker', lease_expires_at=datetime.now(timezone.utc))
    session.add(job)
    session.commit()
    session.exec(text("CREATE TRIGGER fail_prefix BEFORE INSERT ON messages WHEN NEW.generation_job_id = 'prefix_atomic' "
                      "AND NEW.reply_index = 1 BEGIN SELECT RAISE(ABORT, 'forced prefix failure'); END"))
    session.commit()
    with pytest.raises(IntegrityError):
        conversation_service.finalize_message_generation_turn(session, job, transient, post_commit_tasks=[])
    session.expire_all()
    assert session.exec(select(Message).where(Message.generation_job_id == job.id)).all() == []
    assert session.get(MessageGenerationJob, job.id).status == 'running'
    assert session.exec(select(PostCommitTask)).all() == []
    assert client.calls == 1


def _bounded_worker(connection, raw):
    from app.engine.free_body_prefix_recovery import prove_reply_prefix
    import time
    connection.send('ready')
    started = time.monotonic()
    try:
        prove_reply_prefix(raw)
    except body.FreeBodyError:
        outcome = 'rejected'
    else:
        outcome = 'accepted'
    connection.send((outcome, time.monotonic() - started))
    connection.close()


@pytest.mark.parametrize('case', ['long_string', 'deep', 'whitespace', 'oversized'])
def test_bounded_original_grammar_external_deadline(case):
    import multiprocessing
    raw = {
        'long_string': cut(tail='{"text":"' + 'x' * 99000),
        'deep': cut(tail='{"x":' + '[' * 99000),
        'whitespace': cut(tail='{' + ' ' * 99000 + '!'),
        'oversized': cut(tail='{"text":"' + 'x' * 100001),
    }[case]
    context = multiprocessing.get_context('spawn')
    parent, child = context.Pipe(duplex=False)
    worker = context.Process(target=_bounded_worker, args=(child, raw))
    worker.start()
    child.close()
    try:
        assert parent.poll(15), 'worker startup timeout'
        assert parent.recv() == 'ready'
        assert parent.poll(4), 'parser exceeded external deadline'
        outcome, elapsed = parent.recv()
        assert outcome == ('accepted' if case == 'long_string' else 'rejected')
        assert elapsed < 2
        worker.join(5)
        assert worker.exitcode == 0
    finally:
        parent.close()
        if worker.is_alive():
            worker.terminate()
            worker.join(5)
        if worker.is_alive():
            worker.kill()
            worker.join(5)
        worker.close()
