import pytest
from datetime import datetime, timedelta, timezone
from sqlmodel import Session, select

from app.db.models import Conversation, Message, SceneState, WorldSetting
from app.engine.llm_client import LLMResponse
from app.schemas.chat_commands import ChatCommandCreate
from app.services import chat_command_service
from app.services.chat_command_service import parse_chat_command


def test_chat_commands_seed_schedule_and_broadcast(client):
    commands = client.get('/chat-commands?include_disabled=true').json()

    names = {item['name'] for item in commands}
    assert {'스케줄', '방송'} <= names
    schedule = next(item for item in commands if item['name'] == '스케줄')
    broadcast = next(item for item in commands if item['name'] == '방송')
    assert schedule['enabled'] is True
    assert broadcast['enabled'] is True
    assert schedule['description']
    assert broadcast['prompt']
    assert '댓글작성자' in broadcast['prompt']
    assert '댓글내용' in broadcast['prompt']
    broadcast_last = next(item for item in commands if item['name'] == '방송마지막')
    assert '삽입 위치만 뜻' in broadcast_last['generation_prompt']
    assert '방송 엔딩' in broadcast_last['postprocess_prompt']
    assert '절대 만들지 마라' in broadcast_last['postprocess_prompt']


def test_chat_command_crud(client):
    created = client.post('/chat-commands', json={
        'name': '리허설',
        'display_name': '리허설',
        'description': '리허설 장면 연출',
        'prompt': '방금 생성된 응답을 리허설 장면으로 자연스럽게 가공한다.',
        'enabled': True,
        'priority': 7,
    })
    assert created.status_code == 200
    command = created.json()
    assert command['name'] == '리허설'

    updated = client.patch(f"/chat-commands/{command['id']}", json={'description': '수정된 설명', 'enabled': False}).json()
    assert updated['description'] == '수정된 설명'
    assert updated['enabled'] is False

    listed = client.get('/chat-commands').json()
    assert all(item['id'] != command['id'] for item in listed)

    assert client.delete(f"/chat-commands/{command['id']}").status_code == 204
    assert client.get(f"/chat-commands/{command['id']}").status_code == 404


def test_deleting_default_chat_command_hides_it_without_reseeding(client):
    commands = client.get('/chat-commands').json()
    schedule = next(item for item in commands if item['name'] == '스케줄')

    assert client.delete(f"/chat-commands/{schedule['id']}").status_code == 204

    visible_names = {item['name'] for item in client.get('/chat-commands').json()}
    assert '스케줄' not in visible_names

    all_commands = client.get('/chat-commands?include_disabled=true').json()
    hidden_schedule = next(item for item in all_commands if item['id'] == schedule['id'])
    assert hidden_schedule['name'] == '스케줄'
    assert hidden_schedule['enabled'] is False

    # Repeated list calls run the default seeder; hidden bundled commands must
    # stay hidden instead of being resurrected into the visible list.
    visible_names_after_seed = {item['name'] for item in client.get('/chat-commands').json()}
    assert '스케줄' not in visible_names_after_seed


def test_parse_chat_command_uses_enabled_command_metadata(session: Session):
    from app.services.chat_command_service import seed_default_chat_commands
    seed_default_chat_commands(session)

    parsed = parse_chat_command(session, '!스케줄 내일 인기가요랑 NHK 일정 체크해줘')

    assert parsed is not None
    assert parsed.command.name == '스케줄'
    assert parsed.args == '내일 인기가요랑 NHK 일정 체크해줘'
    assert parsed.raw_content == '!스케줄 내일 인기가요랑 NHK 일정 체크해줘'


def test_user_bang_command_stores_clean_content_and_metadata(client, session: Session, monkeypatch):
    from app.api import conversations as conversations_api
    from app.services.chat_command_service import seed_default_chat_commands
    seed_default_chat_commands(session)

    async def fake_generate_replies_from_message(**kwargs):
        return [kwargs['incoming_message']]

    monkeypatch.setattr(conversations_api, 'generate_replies_from_message', fake_generate_replies_from_message)

    char = client.post('/characters', json={'name': '미나', 'persona': '다정한 아이돌'}).json()
    room = client.post('/conversations', json={
        'title': '커맨드 테스트방',
        'mode': 'user_character',
        'participants': [{'type': 'character', 'id': char['id']}],
    }).json()

    response = client.post(f"/conversations/{room['id']}/messages", json={
        'speaker_type': 'user',
        'speaker_id': 'user_001',
        'content': '!방송 인기가요 리허설 무대로 넘어가자',
    })

    assert response.status_code == 200
    saved = response.json()[0]
    assert saved['content'] == '인기가요 리허설 무대로 넘어가자'
    assert saved['metadata']['command'] == '방송'
    assert saved['metadata']['raw_content'] == '!방송 인기가요 리허설 무대로 넘어가자'
    assert saved['metadata']['command_id'].startswith('cmd_')

    db_message = session.exec(select(Message).where(Message.id == saved['id'])).one()
    assert db_message.content == '인기가요 리허설 무대로 넘어가자'
    assert db_message.metadata_['command'] == '방송'


def test_bang_command_activates_until_badge_clear_and_is_exposed_on_conversation(client, session: Session, monkeypatch):
    from app.api import conversations as conversations_api
    from app.services.chat_command_service import seed_default_chat_commands
    seed_default_chat_commands(session)

    async def fake_generate_replies_from_message(**kwargs):
        return [kwargs['incoming_message']]

    monkeypatch.setattr(conversations_api, 'generate_replies_from_message', fake_generate_replies_from_message)

    char = client.post('/characters', json={'name': '미나', 'persona': '다정한 아이돌'}).json()
    room = client.post('/conversations', json={
        'title': '커맨드 지속 테스트방',
        'mode': 'user_character',
        'participants': [{'type': 'character', 'id': char['id']}],
    }).json()

    first = client.post(f"/conversations/{room['id']}/messages", json={
        'speaker_type': 'user',
        'speaker_id': 'user_001',
        'content': '!방송 리허설 무대로 넘어가자',
    })
    assert first.status_code == 200
    refreshed_room = client.get(f"/conversations/{room['id']}").json()
    assert refreshed_room['active_command_id'] == first.json()[0]['metadata']['command_id']

    second = client.post(f"/conversations/{room['id']}/messages", json={
        'speaker_type': 'user',
        'speaker_id': 'user_001',
        'content': '다음 장면 계속 가자',
    })
    assert second.status_code == 200
    assert second.json()[0]['metadata']['active_command'] == '방송'
    assert second.json()[0]['metadata']['active_command_id'] == refreshed_room['active_command_id']

    character_turn = client.post(f"/conversations/{room['id']}/messages", json={
        'speaker_type': 'character',
        'speaker_id': char['id'],
        'content': '미나가 다음 장면을 이어간다',
    })
    assert character_turn.status_code == 200
    character_metadata = character_turn.json()[0]['metadata']
    assert character_metadata['active_command'] == '방송'
    assert character_metadata['active_command_id'] == refreshed_room['active_command_id']
    assert character_metadata['command_ids'] == [refreshed_room['active_command_id']]

    cleared = client.post(f"/conversations/{room['id']}/active-command/{refreshed_room['active_command_id']}/clear")
    assert cleared.status_code == 200
    assert cleared.json()['active_command_id'] is None
    assert cleared.json()['active_command_ids'] == []

    off = client.post(f"/conversations/{room['id']}/messages", json={
        'speaker_type': 'user',
        'speaker_id': 'user_001',
        'content': '!off',
    })
    assert off.status_code == 422


def test_multiple_commands_can_stay_active_until_badge_clear(client, session: Session, monkeypatch):
    from app.api import conversations as conversations_api
    from app.services.chat_command_service import seed_default_chat_commands
    seed_default_chat_commands(session)

    async def fake_generate_replies_from_message(**kwargs):
        return [kwargs['incoming_message']]

    monkeypatch.setattr(conversations_api, 'generate_replies_from_message', fake_generate_replies_from_message)

    char = client.post('/characters', json={'name': '미나', 'persona': '다정한 아이돌'}).json()
    room = client.post('/conversations', json={
        'title': '다중 커맨드 테스트방',
        'mode': 'user_character',
        'participants': [{'type': 'character', 'id': char['id']}],
    }).json()

    first = client.post(f"/conversations/{room['id']}/messages", json={'speaker_type': 'user', 'speaker_id': 'user_001', 'content': '!방송 무대 송출'})
    assert first.status_code == 200
    second = client.post(f"/conversations/{room['id']}/messages", json={'speaker_type': 'user', 'speaker_id': 'user_001', 'content': '!스케줄 오늘 동선 체크'})
    assert second.status_code == 200
    room_state = client.get(f"/conversations/{room['id']}").json()
    assert len(room_state['active_command_ids']) == 2
    assert set(second.json()[0]['metadata']['active_commands']) == {'방송', '스케줄'}
    assert len(second.json()[0]['metadata']['command_ids']) == 2

    removed = client.post(f"/conversations/{room['id']}/active-command/{first.json()[0]['metadata']['command_id']}/clear")
    assert removed.status_code == 200
    assert removed.json()['active_command_ids'] == [second.json()[0]['metadata']['command_id']]


def test_clear_active_command_endpoint_turns_badge_state_off(client, session: Session):
    command = chat_command_service.create_chat_command(
        session,
        ChatCommandCreate(name='방송', display_name='방송', prompt='방송 모드로 바꿔라', enabled=True),
    )
    conversation = Conversation(id='conv_clear_command_test', title='커맨드 뱃지 해제 테스트', mode='user_character', active_command_id=command.id, active_command_ids=[command.id])
    session.add(conversation)
    session.commit()
    session.refresh(conversation)

    response = client.post(f'/conversations/{conversation.id}/active-command/clear')

    assert response.status_code == 200
    body = response.json()
    assert body['active_command_id'] is None
    session.refresh(conversation)
    assert conversation.active_command_id is None


def test_unknown_bang_command_returns_422(client):
    char = client.post('/characters', json={'name': '미나', 'persona': '다정한 아이돌'}).json()
    room = client.post('/conversations', json={
        'title': '커맨드 테스트방',
        'mode': 'user_character',
        'participants': [{'type': 'character', 'id': char['id']}],
    }).json()

    response = client.post(f"/conversations/{room['id']}/messages", json={
        'speaker_type': 'user',
        'speaker_id': 'user_001',
        'content': '!없는커맨드 테스트',
    })

    assert response.status_code == 422
    assert '지원하지 않는 커맨드' in response.json()['detail']


def test_command_postprocess_rejects_internal_control_tokens_without_partial_blocks():
    from app.services.chat_command_service import parse_command_postprocess_response

    leaked = (
        '{"messages":[{"content":"정상 본문","blocks":['
        '{"type":"note","title":"상태","text":"정상"},'
        '{"type":"note","title":"내부","text":"function_call(update_scene)"}'
        ']}]}'
    )
    malformed = (
        '{"messages":[{"content":"정상 본문","blocks":['
        '{"type":"note","title":"상태","text":"정상"},"MALFORMED"'
        ']}]}'
    )

    assert parse_command_postprocess_response(leaked, 1) is None
    assert parse_command_postprocess_response(malformed, 1) is None


def test_command_postprocess_allows_narrative_dice_mentions_without_control_syntax():
    from app.services.chat_command_service import parse_command_postprocess_response

    raw = '{"messages":[{"content":"주사위를 굴려 20이 나왔다.","blocks":[]}]}'

    assert parse_command_postprocess_response(raw, 1) == [
        {"content": "주사위를 굴려 20이 나왔다.", "blocks": []}
    ]


class FakePostprocessClient:
    async def chat(self, messages, response_format=None, conversation_id=None):
        return LLMResponse(content='가공된 방송 응답', provider='fake', model='fake')


class FakeBroadcastBlocksClient:
    async def chat(self, messages, response_format=None, conversation_id=None):
        return LLMResponse(
            content=(
                '{"messages":[{"content":"가공된 방송 응답",'
                '"blocks":[{"type":"comments","title":"LIVE 댓글","comments":[{"author":"채팅요정","text":"방금 표정 뭐야 ㅋㅋ"},{"author":"팬계정","text":"이 장면 클립각"}]},'
                '{"type":"note","title":"방송 상태","text":"라이브 송출 중 · 채팅 속도 빠름"},'
                '{"type":"table","title":"화면 정보","headers":["항목","내용"],"rows":[["자막","리허설 직전"],["투표","긴장 72%"]]}]}]}'
            ),
            provider='fake',
            model='fake',
        )


class FakeMalformedBroadcastClient:
    async def chat(self, messages, response_format=None, conversation_id=None):
        return LLMResponse(content='한 덩어리 방송 후처리 응답', provider='fake', model='fake')


@pytest.mark.asyncio
async def test_apply_chat_command_postprocess_marks_unstructured_response_failed(session: Session):
    from app.db.models import ChatCommand
    from app.services.chat_command_service import apply_chat_command_postprocess

    command = ChatCommand(
        id='cmd_test_broadcast',
        name='방송',
        display_name='방송',
        description='방송 연출',
        prompt='방송 장면처럼 가공한다.',
        enabled=True,
    )
    source = Message(
        id='msg_source',
        conversation_id='conv_test',
        speaker_type='user',
        speaker_id='user_001',
        content='인기가요 리허설로 넘어가자',
        metadata_={'command': '방송', 'command_id': command.id},
    )
    generated = Message(
        id='msg_generated',
        conversation_id='conv_test',
        speaker_type='character',
        speaker_id='char_mina',
        content='원본 응답',
    )
    session.add(command)
    session.add(source)
    session.add(generated)
    session.commit()

    await apply_chat_command_postprocess(
        session=session,
        command=command,
        conversation_id='conv_test',
        source_message=source,
        generated_messages=[generated],
        llm_client=FakePostprocessClient(),
    )

    refreshed = session.get(Message, 'msg_generated')
    assert refreshed is not None
    assert refreshed.content == '원본 응답'
    assert refreshed.metadata_['command_postprocess_status'] == 'failed'
    assert refreshed.metadata_['command_applied'] == '방송'
    assert 'command_postprocess_fallback' not in refreshed.metadata_
    assert session.get(Message, 'msg_source').metadata_['command_postprocess_status'] == 'failed'


@pytest.mark.asyncio
async def test_apply_broadcast_command_postprocess_stores_renderable_blocks(session: Session):
    from app.db.models import ChatCommand
    from app.services.chat_command_service import apply_chat_command_postprocess

    command = ChatCommand(
        id='cmd_test_broadcast_blocks',
        name='방송',
        display_name='방송',
        description='방송 댓글 레이어',
        prompt='방송 댓글창을 추가한다.',
        enabled=True,
    )
    source = Message(
        id='msg_source_blocks',
        conversation_id='conv_test_blocks',
        speaker_type='user',
        speaker_id='user_001',
        content='라이브로 보여줘',
        metadata_={'command': '방송', 'command_id': command.id},
    )
    generated = Message(
        id='msg_generated_blocks',
        conversation_id='conv_test_blocks',
        speaker_type='character',
        speaker_id='char_mina',
        content='원본 응답',
    )
    session.add(command)
    session.add(source)
    session.add(generated)
    session.commit()

    await apply_chat_command_postprocess(
        session=session,
        command=command,
        conversation_id='conv_test_blocks',
        source_message=source,
        generated_messages=[generated],
        llm_client=FakeBroadcastBlocksClient(),
    )

    refreshed = session.get(Message, 'msg_generated_blocks')
    assert refreshed is not None
    assert refreshed.content == '가공된 방송 응답'
    assert refreshed.metadata_['command_block_schema'] == 'chat_overlay_v1'
    assert refreshed.metadata_['command_blocks'][0]['type'] == 'comments'
    assert refreshed.metadata_['command_blocks'][0]['comments'][0] == {'author': '채팅요정', 'text': '방금 표정 뭐야 ㅋㅋ'}
    assert refreshed.metadata_['command_blocks'][1]['type'] == 'note'
    assert refreshed.metadata_['command_blocks'][2]['type'] == 'table'


class FakeCapturingBroadcastClient:
    def __init__(self, content: str):
        self.content = content
        self.messages = None

    async def chat(self, messages, response_format=None, conversation_id=None):
        self.messages = messages
        return LLMResponse(content=self.content, provider='fake', model='fake')


@pytest.mark.asyncio
async def test_broadcast_postprocess_prompt_forbids_talking_to_user_directly(session: Session):
    from app.db.models import ChatCommand
    from app.services.chat_command_service import apply_chat_command_postprocess

    command = ChatCommand(
        id='cmd_test_broadcast_prompt',
        name='방송',
        display_name='방송',
        description='방송 댓글 레이어',
        prompt='개쩌는 방송 화면과 댓글창을 만든다.',
        enabled=True,
    )
    source = Message(
        id='msg_source_prompt',
        conversation_id='conv_test_prompt',
        speaker_type='user',
        speaker_id='user_001',
        content='개쩌는 방송을 보여줘',
        metadata_={'command': '방송', 'command_id': command.id},
    )
    generated = Message(
        id='msg_generated_prompt',
        conversation_id='conv_test_prompt',
        speaker_type='character',
        speaker_id='char_harin',
        content='좋아, 내가 바로 보여줄게.',
    )
    session.add(command)
    session.add(source)
    session.add(generated)
    session.commit()
    client = FakeCapturingBroadcastClient('{"messages":[{"content":"[LIVE] 모아의 무대가 화면을 꽉 채운다.","blocks":[{"type":"comments","comments":[{"author":"시청자","text":"와 이거 방송 맞냐"}]}]}]}')

    await apply_chat_command_postprocess(
        session=session,
        command=command,
        conversation_id='conv_test_prompt',
        source_message=source,
        generated_messages=[generated],
        llm_client=client,
    )

    assert client.messages is not None
    prompt_text = client.messages[1]['content']
    assert '사용자에게 요청을 접수했다거나 처리해주겠다고 직접 말하지 마라' in prompt_text
    assert '커맨드 요청/연출 지시' in prompt_text
    assert '방송 요청/연출 지시' not in prompt_text


@pytest.mark.asyncio
async def test_broadcast_malformed_response_keeps_original_scene_and_marks_failure(session: Session):
    from app.db.models import ChatCommand
    from app.services.chat_command_service import apply_chat_command_postprocess

    command = ChatCommand(
        id='cmd_test_broadcast_malformed',
        name='방송',
        display_name='방송',
        description='방송 댓글 레이어',
        prompt='방송 댓글창을 추가한다.',
        enabled=True,
    )
    source = Message(
        id='msg_source_malformed',
        conversation_id='conv_test_malformed',
        speaker_type='user',
        speaker_id='user_001',
        content='개쩌는 방송을 보여줘',
        metadata_={'command': '방송', 'command_id': command.id},
    )
    generated = Message(
        id='msg_generated_malformed',
        conversation_id='conv_test_malformed',
        speaker_type='character',
        speaker_id='char_harin',
        content='모아이 조명 아래에서 첫 동작을 잡자 카메라가 무대를 당겨 잡는다.',
    )
    session.add(command)
    session.add(source)
    session.add(generated)
    session.commit()

    await apply_chat_command_postprocess(
        session=session,
        command=command,
        conversation_id='conv_test_malformed',
        source_message=source,
        generated_messages=[generated],
        llm_client=FakeMalformedBroadcastClient(),
    )

    refreshed = session.get(Message, 'msg_generated_malformed')
    assert refreshed is not None
    assert refreshed.content == '모아이 조명 아래에서 첫 동작을 잡자 카메라가 무대를 당겨 잡는다.'
    assert '보여줘' not in refreshed.content
    assert refreshed.metadata_['command_postprocess_status'] == 'failed'
    assert 'command_postprocess_fallback' not in refreshed.metadata_
    assert session.get(Message, 'msg_source_malformed').metadata_['command_postprocess_status'] == 'failed'

@pytest.mark.asyncio
async def test_overlay_postprocess_prompt_includes_world_and_scene_context(session: Session):
    from app.db.models import ChatCommand
    from app.services.chat_command_service import apply_chat_command_postprocess

    world = WorldSetting(
        id='world_league_context',
        title='리그 세계관',
        description='20인 리그제 섹스 배틀 운영 세계관',
        genre_mode='battle',
        world_seed='승리 시 승점 3점, 패배 시 0점, 상위 4인은 포스트시즌에 진출한다.',
        compression_focus='경기 결과와 승점, 대진표만 공식 기록으로 갱신한다.',
        mood='중계 열기',
        tone_preset='cinematic',
    )
    conversation = Conversation(id='conv_world_context', title='리그', world_setting_id=world.id, mode='multi', genre_mode='battle')
    scene = SceneState(
        conversation_id=conversation.id,
        location='중앙 경기장',
        mood='결승전 직전 긴장감',
        current_conflict='솔 vs 로아 속도전',
        last_event='솔가 속도로 밀어붙임',
        summary='리그는 정규 시즌 중이며 방송 중계가 계속된다.',
    )
    command = ChatCommand(
        id='cmd_world_context',
        name='방송마지막',
        display_name='방송 마지막',
        description='라이브 방송 오버레이',
        prompt='방송 오버레이를 만든다.',
        postprocess_prompt='댓글창과 상태 패널을 만든다.',
        postprocess_target='last_bubble',
        enabled=True,
    )
    source = Message(id='msg_world_source', conversation_id=conversation.id, speaker_type='user', speaker_id='user_001', content='세아가 속도로 밀어붙입니다')
    generated = Message(id='msg_world_generated', conversation_id=conversation.id, speaker_type='character', speaker_id='char_sea', content='지지 않을 거예요!')
    session.add(world)
    session.add(conversation)
    session.add(scene)
    session.add(command)
    session.add(source)
    session.add(generated)
    session.commit()
    client = FakeCapturingBroadcastClient('{"messages":[{"content":"LIVE 속도전","blocks":[{"type":"comments","comments":[{"author":"팬","text":"세아 빠르다"}]}]}]}')

    result = await apply_chat_command_postprocess(
        session=session,
        command=command,
        conversation_id=conversation.id,
        source_message=source,
        generated_messages=[generated],
        llm_client=client,
    )

    prompt_text = client.messages[1]['content']
    assert '후속 생성 참고 컨텍스트' in prompt_text
    assert '세계관 제목: 리그 세계관' in prompt_text
    assert '승리 시 승점 3점' in prompt_text
    assert '현재 갈등/주제: 솔 vs 로아 속도전' in prompt_text
    assert '1차 응답 메시지' in prompt_text
    overlay = result[-1]
    assert overlay.speaker_id == 'command_overlay'
    assert overlay.content == ''
    assert overlay.metadata_['command_blocks'][0]['type'] == 'comments'


@pytest.mark.asyncio
async def test_last_bubble_overlay_is_after_full_generated_result(session: Session):
    from app.db.models import ChatCommand
    from app.services.chat_command_service import apply_chat_command_postprocess

    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    command = ChatCommand(
        id='cmd_overlay_order',
        name='방송마지막',
        display_name='방송 마지막',
        description='라이브 방송 오버레이',
        prompt='방송 오버레이를 만든다.',
        postprocess_prompt='댓글창을 만든다.',
        postprocess_target='last_bubble',
        enabled=True,
    )
    source = Message(id='msg_order_source', conversation_id='conv_overlay_order', speaker_type='user', speaker_id='user_001', content='마지막에 붙여줘')
    character = Message(id='msg_order_character', conversation_id='conv_overlay_order', speaker_type='character', speaker_id='char_a', content='캐릭터 버블', created_at=base)
    storyteller = Message(id='msg_order_storyteller', conversation_id='conv_overlay_order', speaker_type='storytelling', speaker_id='storyteller', content='스토리텔러 버블', created_at=base + timedelta(seconds=1))
    session.add(command)
    session.add(source)
    session.add(character)
    session.add(storyteller)
    session.commit()

    result = await apply_chat_command_postprocess(
        session=session,
        command=command,
        conversation_id='conv_overlay_order',
        source_message=source,
        generated_messages=[character, storyteller],
        llm_client=FakeCapturingBroadcastClient('{"messages":[{"content":"LIVE 마지막","blocks":[]}]}'),
    )

    assert [message.id for message in result] == [character.id, storyteller.id]
    refreshed_source = session.get(Message, source.id)
    assert refreshed_source is not None
    assert refreshed_source.metadata_['command_postprocess_status'] == 'failed'
    overlays = session.exec(select(Message).where(Message.conversation_id == 'conv_overlay_order', Message.speaker_id == 'command_overlay')).all()
    assert overlays == []

@pytest.mark.asyncio
async def test_overlay_command_rejects_whole_generated_response_before_persistence(session: Session):
    from app.api.conversations import persist_generated_reply_messages
    from app.db.models import Character
    from app.engine.character_runtime import CharacterRuntimeError
    from app.schemas.conversations import MessageCreate
    from app.schemas.dialogue import CharacterReply

    conversation = Conversation(id='conv_filter_fake_overlay', title='리그', mode='multi', genre_mode='battle')
    character = Character(id='char_filter_fake_overlay', name='솔', persona='승부욕 강한 선수')
    source = Message(id='msg_filter_fake_overlay_source', conversation_id=conversation.id, speaker_type='user', speaker_id='user_001', content='진행')
    session.add(conversation)
    session.add(character)
    session.add(source)
    session.commit()

    with pytest.raises(CharacterRuntimeError, match='filtered before persistence'):
        await persist_generated_reply_messages(
            session=session,
            conversation_id=conversation.id,
            conversation=conversation,
            payload=MessageCreate(speaker_type='user', speaker_id='user_001', content='진행'),
            incoming_message=source,
            generated_replies=[
                CharacterReply(reply_type='character', character_id=character.id, text='제가 밀어붙일게요.', emotion='', action='', thought=''),
                CharacterReply(reply_type='storytelling', character_id='storyteller', text='관중석이 술렁인다.', emotion='', action='', thought=''),
                CharacterReply(reply_type='storytelling', character_id='storyteller', text='command_overlay | dialogue=LIVE 찌꺼기', emotion='', action='', thought=''),
            ],
            room_characters=[character],
            scene_state=None,
            is_multi_room=True,
        )

    stored = session.exec(select(Message).where(Message.conversation_id == conversation.id)).all()
    assert [message.id for message in stored] == [source.id]

