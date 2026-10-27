from datetime import datetime, timedelta, timezone
import json

import pytest

from app.db.models import ChatCommand, Conversation, Message, SceneState
from app.engine.llm_client import LLMResponse
from app.services import chat_command_service


def seed_history(session, *, count=20, boundary_index=18, same_timestamp=False):
    conversation_id = 'conv_command_boundary'
    session.add(Conversation(id=conversation_id, mode='user_character', title='Synthetic boundary fixture'))
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    messages = [Message(
        id=f'msg_{index:03}', conversation_id=conversation_id,
        speaker_type='user', speaker_id='fixture_user',
        content=f'VISIBLE_{index:03}_unique_fixture',
        created_at=start if same_timestamp else start + timedelta(seconds=index),
    ) for index in range(count)]
    session.add_all(messages)
    if boundary_index is not None:
        session.add(SceneState(conversation_id=conversation_id, summary='Synthetic compressed story',
                               last_compression_source_message_id=messages[boundary_index].id))
    session.commit()
    return conversation_id, messages


def recent_context(session, conversation_id):
    return '\n'.join(chat_command_service._build_recent_messages_context(session, conversation_id, {}))


def test_recent_command_context_excludes_covered_prefix_inside_latest_ten(session):
    cid, messages = seed_history(session)
    context = recent_context(session, cid)
    assert messages[19].content in context
    assert all(message.content not in context for message in messages[:19])


def test_recent_command_context_limits_after_filtering_boundary(session):
    cid, messages = seed_history(session, count=30, boundary_index=2)
    context = recent_context(session, cid)
    assert all(message.content in context for message in messages[-10:])
    assert all(message.content not in context for message in messages[:-10])


def test_recent_command_context_empty_when_every_message_is_compressed(session):
    cid, _ = seed_history(session, boundary_index=19)
    assert recent_context(session, cid) == ''


@pytest.mark.parametrize('with_scene', [False, True])
def test_recent_command_context_without_boundary_keeps_latest_ten(session, with_scene):
    cid, messages = seed_history(session, boundary_index=None)
    if with_scene:
        session.add(SceneState(conversation_id=cid))
        session.commit()
    context = recent_context(session, cid)
    assert all(message.content in context for message in messages[-10:])
    assert all(message.content not in context for message in messages[:-10])


@pytest.mark.parametrize('foreign_boundary', [False, True])
def test_recent_command_context_invalid_boundary_fails_closed(session, caplog, foreign_boundary):
    cid, messages = seed_history(session)
    scene = session.get(SceneState, cid)
    if foreign_boundary:
        session.add(Conversation(id='conv_foreign_boundary', mode='user_character'))
        session.add(Message(id='msg_foreign_boundary', conversation_id='conv_foreign_boundary',
                            speaker_type='user', speaker_id='fixture_user', content='FOREIGN_FIXTURE'))
        scene.last_compression_source_message_id = 'msg_foreign_boundary'
    else:
        scene.last_compression_source_message_id = 'missing_fixture_boundary'
    session.add(scene)
    session.commit()
    assert recent_context(session, cid) == ''
    assert 'missing_compression_boundary' in caplog.text
    assert all(message.content not in caplog.text for message in messages)


def test_recent_command_context_uses_created_at_and_id_tie_breaker(session):
    cid, messages = seed_history(session, boundary_index=14, same_timestamp=True)
    context = recent_context(session, cid)
    assert all(message.content not in context for message in messages[:15])
    assert all(message.content in context for message in messages[15:])
    assert [context.index(message.content) for message in messages[15:]] == sorted(
        context.index(message.content) for message in messages[15:])


@pytest.mark.asyncio
@pytest.mark.parametrize('repair_first', [False, True])
async def test_actual_command_provider_payload_never_reintroduces_covered_raw(session, repair_first):
    cid, messages = seed_history(session)
    source = Message(id='virtual_source', conversation_id=cid, speaker_type='user',
                     speaker_id='fixture_user', content='CURRENT_TURN_SOURCE')
    generated = Message(id='virtual_reply', conversation_id=cid, speaker_type='character',
                        speaker_id='fixture_character', content='ORIGINAL_CURRENT_REPLY')
    command = ChatCommand(id='cmd_boundary_fixture', name='fixture-command',
                          prompt='Preserve the current reply', postprocess_prompt='Preserve the current reply',
                          postprocess_target='all_bubbles', postprocess_probability=100,
                          postprocess_context_options=['scene', 'recent_messages'], enabled=True)
    captured = []

    class Client:
        async def chat(self, request_messages, **kwargs):
            captured.append(request_messages)
            content = 'not json' if repair_first and len(captured) == 1 else json.dumps({
                'messages': [{'content': 'VALID_CURRENT_REPLY', 'blocks': []}],
            })
            return LLMResponse(content=content, finish_reason='STOP')

    result = await chat_command_service.apply_chat_command_postprocess(
        session=session, command=command, conversation_id=cid, source_message=source,
        generated_messages=[generated], llm_client=Client(), commit=False)
    assert result[0].content == 'VALID_CURRENT_REPLY'
    assert len(captured) == (2 if repair_first else 1)
    for request in captured:
        payload = '\n'.join(message['content'] for message in request)
        assert 'Synthetic compressed story' in payload
        assert messages[19].content in payload
        assert all(message.content not in payload for message in messages[:19])
    assert all(session.get(Message, message.id).content == message.content for message in messages)
