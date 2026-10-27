import asyncio
import json
from copy import deepcopy
from datetime import datetime, timezone
import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlmodel import select
from app.core.config import get_settings
from app.db.models import Character, ChatCommand, Conversation, Message, MessageGenerationJob, PostCommitTask
from app.engine.llm_client import LLMClient, LLMResponse
from app.engine import free_body_protocol as fb
from app.api.conversations import _run_message_generation_job_in_session, persist_generated_reply_messages, build_generated_turn_post_commit_tasks
from app.schemas.conversations import MessageCreate
from app.services import conversation_service, chat_command_service, tts_service


def room_fixture(client):
    cast = [client.post('/characters', json={'name': name, 'persona': 'Adult synthetic actor.'}).json() for name in ['Aria', 'Bora']]
    room = client.post('/conversations', json={'mode': 'character_character', 'genre_mode': 'custom',
        'participants': [{'type': 'character', 'id': c['id'], 'order_index': i} for i, c in enumerate(cast)]}).json()
    return room['id'], cast


@pytest.mark.parametrize('protocol', ['tagged_text', 'free_body_json'])
@pytest.mark.parametrize('invalid', [False, True])
def test_actual_job_api_persistence_edits_preview_and_atomic_parse_failure(client, session, monkeypatch, protocol, invalid):
    monkeypatch.setenv('CHAT_RESPONSE_PROTOCOL', protocol)
    get_settings.cache_clear()
    monkeypatch.setattr('app.api.conversations._start_message_generation_worker', lambda *a: None)
    room, cast = room_fixture(client)
    body = '<a>Slowly turns.</a>First.<t>' + 'A private possibility remains unresolved. ' * 6 + '</t>Last.'
    calls = []
    async def fake_chat(self, messages, response_format=None, **kw):
        calls.append({'messages': messages, 'format': response_format})
        who = 'C999' if invalid else 'C1'
        if protocol == 'tagged_text':
            content = f'<m c="{who}">{body}</m><m c="C2"><a>Waits.</a></m>'
        else:
            content = json.dumps({'replies': [{'who': who, 'text': body}, {'who': 'C2', 'text': '<a>Waits.</a>'}]})
        return LLMResponse(content=content, finish_reason='STOP')
    monkeypatch.setattr(LLMClient, 'chat', fake_chat)
    response = client.post(f'/conversations/{room}/messages/jobs', json={'speaker_type': 'user', 'speaker_id': 'synthetic', 'content': 'Continue.'})
    assert response.status_code == 200, response.text
    job_id = response.json()['job']['id']
    asyncio.run(_run_message_generation_job_in_session(session, job_id))
    status = client.get(f'/conversations/{room}/generation-jobs/{job_id}').json()
    all_messages = client.get(f'/conversations/{room}/messages').json()
    if invalid:
        assert status['status'] == 'failed', status
        assert len(all_messages) == 1 and len(calls) == 2
        assert not session.exec(select(Message).where(Message.generation_job_id == job_id)).all()
        return
    assert status['status'] == 'completed', status
    generated = [m for m in all_messages if m['id'] in status['generated_message_ids']]
    assert [m['speaker_id'] for m in generated] == [c['id'] for c in cast]
    assert generated[0]['metadata']['raw_body'] == body
    assert len(generated[0]['thought']) > 120
    assert [p['type'] for p in generated[0]['metadata']['render_parts']] == ['action', 'dialogue', 'thought', 'dialogue']
    assert generated[1]['content'] == ''
    assert len(calls) == 1
    compiled = calls[0]['messages'][0]['content']
    assert 'C1 = Aria' in compiled and 'Keep total replies between' not in compiled
    assert (calls[0]['format'] is None) == (protocol == 'tagged_text')
    preview = client.get(f'/conversations/{room}/context-preview')
    assert preview.status_code == 200
    assert 'C1 = Aria' in preview.text and 'Keep total replies between' not in preview.text
    assert 'separate JSON fields' not in preview.text
    message = session.get(Message, generated[0]['id'])
    message.metadata_ = {**message.metadata_, 'tts_audio_url': '/synthetic.wav', 'tts_text': 'stale'}
    session.add(message)
    session.commit()
    endpoint = f"/conversations/{room}/messages/{message.id}"
    edited_body = '<t>Private.</t>Before.<a>Moves.</a>After.'
    updated = client.patch(endpoint, json={'raw_body': edited_body, 'emotion': 'calm'})
    assert updated.status_code == 200, updated.text
    data = updated.json()
    assert data['content'] == 'Before.\nAfter.' and data['thought'] == 'Private.'
    assert not any(key.startswith('tts_') for key in data['metadata'])
    emotion_only = client.patch(endpoint, json={'emotion': 'happy'}).json()
    assert emotion_only['metadata']['render_parts'] == data['metadata']['render_parts']
    assert emotion_only['metadata']['raw_body'] == edited_body
    assert client.patch(endpoint, json={'raw_body': '<a>broken'}).status_code == 422
    assert client.patch(endpoint, json={'content': 'must use body'}).status_code == 422
    session.expire_all()
    assert session.get(Message, message.id).metadata_['raw_body'] == edited_body
    # No spoken content means no synthesis and no auto-TTS outbox work.
    silent = session.get(Message, generated[1]['id'])
    def forbidden(*a, **kw):
        pytest.fail('action-only TTS reached synthesis')
    monkeypatch.setattr(tts_service, 'synthesize_to_upload', forbidden)
    assert tts_service.ensure_message_tts(session, silent) is silent
    assert tts_service.build_tts_text(silent) == ''
    assert 'Private' not in tts_service.build_tts_text(session.get(Message, message.id))
    conv = session.get(Conversation, room)
    conv.tts_enabled = True
    tasks = build_generated_turn_post_commit_tasks(session=session, conversation=conv, scene_state=None,
        incoming_message=session.get(Message, response.json()['incoming_message']['id']), generated_messages=[silent],
        enqueue_compression=False, character_ids=[c['id'] for c in cast], generation_job_id='synthetic')
    assert all(t['task_type'] != 'tts' for t in tasks)


@pytest.mark.asyncio
async def test_atomic_finalizer_retains_freebody_metadata_and_rolls_back_all_on_second_insert(session):
    cast = [Character(id='a', name='Aria')]
    replies = fb.parse_response('<m c="C1"><a>First.</a></m><m c="C1">Second.</m>', protocol='tagged_text', aliases=fb.speaker_aliases(cast))
    source = Message(id='src', conversation_id='room', speaker_type='user', speaker_id='u', content='next')
    transient = await persist_generated_reply_messages(session=session, conversation_id='room', conversation=Conversation(id='room'),
        payload=MessageCreate(speaker_type='user', speaker_id='u', content='next'), incoming_message=source,
        generated_replies=replies, room_characters=cast, scene_state=None, is_multi_room=False, min_bubbles=8)
    assert len(transient) == 2 and transient[0].content == ''
    assert transient[0].metadata_['raw_body'] == '<a>First.</a>'
    job = MessageGenerationJob(id='freebody_atomic', conversation_id='room', incoming_message_id='src', status='running',
        lease_owner='worker', lease_expires_at=datetime.now(timezone.utc))
    session.add(job)
    session.commit()
    session.exec(text("CREATE TRIGGER fail_freebody BEFORE INSERT ON messages WHEN NEW.generation_job_id = 'freebody_atomic' AND NEW.reply_index = 1 BEGIN SELECT RAISE(ABORT, 'forced failure'); END"))
    session.commit()
    with pytest.raises(IntegrityError):
        conversation_service.finalize_message_generation_turn(session, job, transient, post_commit_tasks=[])
    session.expire_all()
    assert session.exec(select(Message).where(Message.generation_job_id == job.id)).all() == []
    assert session.get(MessageGenerationJob, job.id).status == 'running'
    assert session.exec(select(PostCommitTask)).all() == []


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['all_bubbles', 'last_bubble'])
@pytest.mark.parametrize('failure', [False, True])
@pytest.mark.parametrize('action_only', [False, True])
async def test_command_rewrite_overlay_and_failure_preserve_semantics(session, mode, failure, action_only):
    command = ChatCommand(id='cmd', name='synthetic', display_name='Synthetic', prompt='Add the requested effect.', postprocess_target=mode,
        postprocess_prompt='Postprocess only.', generation_prompt='Generation only.')
    source = Message(id='src', conversation_id='room', speaker_type='user', speaker_id='u', content='next')
    body = '<a>Moves.</a>Original.<t>Hidden.</t>Final.'
    if action_only:
        body = '<a>Moves without speaking.</a>'
    reply = fb.parse_response(f'<m c="C1">{body}</m>', protocol='tagged_text', aliases={'C1': 'a', 'N': None})[0]
    metadata = fb.metadata_for_reply(reply)
    message = Message(id='generated', conversation_id='room', speaker_type='character', speaker_id='a', content=reply.text,
        action=reply.action, thought=reply.thought, metadata_=deepcopy(metadata), created_at=datetime.now(timezone.utc))
    session.add_all([command, source, message])
    session.commit()
    captured = []
    class Client:
        async def chat(self, messages, **kw):
            captured.append(messages)
            if failure:
                return LLMResponse(content='not json')
            item = {'content': '', 'blocks': [{'type': 'note', 'text': 'Overlay note.'}]}
            if mode == 'all_bubbles':
                item['content'] = 'Rewritten speech.'
            return LLMResponse(content=json.dumps({'messages': [item]}), finish_reason='STOP')
    result = await chat_command_service.apply_chat_command_postprocess(session=session, command=command, conversation_id='room',
        source_message=source, generated_messages=[message], llm_client=Client())
    session.expire_all()
    stored = session.get(Message, 'generated')
    assert 'Generation only.' not in json.dumps(captured)
    assert 'Hidden.' not in json.dumps(captured)
    if failure or mode == 'last_bubble':
        assert stored.content == reply.text
        assert all(stored.metadata_[key] == value for key, value in metadata.items())
        assert stored.action == reply.action and stored.thought == reply.thought
    else:
        assert stored.content == 'Rewritten speech.'
        assert not {'raw_body', 'render_parts', 'response_protocol'}.intersection(stored.metadata_)
    if failure:
        assert stored.metadata_['command_postprocess_status'] == 'failed' and len(captured) == 2
    elif mode == 'last_bubble':
        assert len(result) == 2 and result[-1].metadata_['command_overlay'] is True
