"""Synthetic endpoint and provider-boundary coverage for character-only rooms."""
import asyncio
import json

import pytest
from sqlmodel import select

from app.api import conversations as api
from app.core.config import get_settings
from app.db.models import Conversation, SceneState, WorldSetting, Message, ChatCommand
from app.engine import prompts, command_overlay
from app.engine.llm_client import LLMClient, LLMResponse
from app.services import chat_command_service, conversation_service, generation_execution


def create_room(client, **extra):
    char = client.post('/characters', json={'name': 'Synthetic Mira', 'persona': 'PERSONA_SENTINEL'}).json()
    response = client.post('/conversations', json={
        'mode': 'user_character', 'participants': [{'type': 'character', 'id': char['id']}], **extra})
    return response, char


@pytest.mark.parametrize('extra', [{}, {'world_setting_id': None}, {'genre_mode': None}])
def test_character_only_create_persists_no_world(client, session, extra):
    response, _ = create_room(client, **extra)
    assert response.status_code == 200, response.text
    room = response.json()
    assert room['world_setting_id'] is None
    stored = session.get(Conversation, room['id'])
    assert stored.world_setting_id is None
    assert room['genre_mode'] is None and stored.genre_mode is None
    assert client.get(f"/conversations/{room['id']}/battle-state").status_code == 404
    scene = session.get(SceneState, room['id'])
    assert scene is not None and not scene.world_seed and scene.user_description is None
    assert not session.exec(select(WorldSetting)).all()
    assert client.get(f"/conversations/{room['id']}").json()['world_setting_id'] is None


def test_explicit_room_genre_and_linked_world_are_preserved(client):
    explicit, _ = create_room(client, genre_mode='battle')
    assert explicit.status_code == 200 and explicit.json()['genre_mode'] == 'battle'
    world = client.post('/world-settings', json={'title': 'Synthetic world', 'genre_mode': 'romance'}).json()
    linked, _ = create_room(client, world_setting_id=world['id'], genre_mode='battle')
    assert linked.status_code == 200 and linked.json()['genre_mode'] == 'romance'


@pytest.mark.parametrize('world_id', ['missing-world', '', '   '])
def test_invalid_explicit_world_rejected(client, session, world_id):
    response, _ = create_room(client, world_setting_id=world_id)
    assert response.status_code == 422
    assert not session.exec(select(Conversation)).all()
    assert not session.exec(select(WorldSetting)).all()


def test_lifespan_does_not_clone_worldless_room(client, session, engine):
    response, _ = create_room(client, scene={'opening_scene': 'AUTHORED_HOOK', 'user_description': 'PERSONA_USER'})
    room_id = response.json()['id']
    from app.main import app, lifespan
    async def restart_fixture_lifespan():
        async with lifespan(app):
            assert app.state.engine is engine
            assert not app.state.dispatchers_started
            assert app.state.mock_generation_task is None
    asyncio.run(restart_fixture_lifespan())
    session.expire_all()
    assert session.get(Conversation, room_id).world_setting_id is None
    assert not session.exec(select(WorldSetting)).all()
    assert session.get(SceneState, room_id).opening_scene == 'AUTHORED_HOOK'


@pytest.mark.parametrize('linked', [False, True, 'detach', 'clear'])
def test_preview_and_real_job_prompt_world_policy(client, session, monkeypatch, linked):
    monkeypatch.setenv('CHAT_RESPONSE_PROTOCOL', 'tagged_text')
    get_settings.cache_clear()
    extra = {}
    if linked:
        world = client.post('/world-settings', json={'title': 'Synthetic stage', 'world_seed': 'WORLD_SENTINEL'}).json()
        extra['world_setting_id'] = world['id']
    response, char = create_room(client, scene={'user_description': 'USER_SENTINEL', 'opening_scene': 'HOOK_SENTINEL'}, **extra)
    assert response.status_code == 200
    room_id = response.json()['id']
    assert client.post(f'/conversations/{room_id}/memories', json={'content': 'NOTE_SENTINEL'}).status_code == 200
    scene = session.get(SceneState, room_id)
    scene.summary = '[Rolling Story Arc]\n- ARC_SENTINEL'
    session.add(scene)
    session.add(Message(id='prior-synthetic', conversation_id=room_id, speaker_type='character', speaker_id=char['id'], content='HISTORY_SENTINEL'))
    session.commit()
    monkeypatch.setattr(api, '_start_message_generation_worker', lambda *a: None)
    if linked == 'clear':
        cleared = client.patch(f'/conversations/{room_id}', json={'genre_mode': None})
        assert cleared.status_code == 200 and cleared.json()['genre_mode'] is None
    if linked == 'detach':
        detached = client.patch(f'/conversations/{room_id}', json={'world_setting_id': None})
        assert detached.status_code == 200 and detached.json()['world_setting_id'] is None
        session.expire_all()
        stored = session.get(SceneState, room_id)
        assert stored.world_seed == 'WORLD_SENTINEL'  # Detach is not destructive editing.
        assert stored.opening_scene == 'HOOK_SENTINEL'
        assert 'ARC_SENTINEL' in stored.summary
    queued = client.post(f'/conversations/{room_id}/messages/jobs', json={'speaker_type': 'user', 'speaker_id': 'u', 'content': 'SOURCE_SENTINEL'}).json()
    preview = client.get(f'/conversations/{room_id}/context-preview')
    assert preview.status_code == 200, preview.text
    calls = []
    async def chat(self, messages, **kwargs):
        calls.append(messages)
        return LLMResponse(content='<m c="C1">I remember our promise and will meet you there.</m>')
    monkeypatch.setattr(LLMClient, 'chat', chat)
    asyncio.run(generation_execution.run_message_generation_job_in_session(session, queued['job']['id']))
    status = client.get(f"/conversations/{room_id}/generation-jobs/{queued['job']['id']}").json()
    assert status['status'] == 'completed', status
    assert calls
    system_text = calls[0][0]['content']
    for section in preview.json()['sections']:
        if section['included'] and section['key'] != 'current_user_input':
            assert section['content'] in system_text
    for text in [json.dumps(preview.json(), ensure_ascii=False), json.dumps(calls, ensure_ascii=False)]:
        for marker in ['PERSONA_SENTINEL', 'USER_SENTINEL', 'NOTE_SENTINEL', 'ARC_SENTINEL', 'HISTORY_SENTINEL']:
            assert marker in text
        if linked in (False, 'clear'):
            for marker in ['Genre routing', 'genre policy', 'official_domain_state', 'battle_control_hint', 'genre_route:', 'battle ledger']:
                assert marker.lower() not in text.lower()
        if linked is True or linked == 'clear':
            assert 'WORLD_SENTINEL' in text
        else:
            assert 'WORLD_SENTINEL' not in text
            assert 'Fixed room world setting' not in text
            assert 'Room world and past story' not in text
            assert 'World setting defines the stage' not in text
            assert 'world_seed' not in text
            assert ('Genre routing' in text) == (linked == 'detach')
            assert 'Official domain state' not in text


def test_worldless_commands_do_not_restore_orphan_seed(client, session):
    response, char = create_room(client, scene={'opening_scene': 'AUTHORED_HOOK'})
    room_id = response.json()['id']
    scene = session.get(SceneState, room_id)
    scene.world_seed = 'ORPHAN_WORLD_SENTINEL'
    session.add(scene)
    source = Message(id='cmd-source', conversation_id=room_id, speaker_type='user', speaker_id='u', content='COMMAND_SOURCE')
    session.add(source)
    session.commit()
    command = ChatCommand(id='cmd-test', name='synthetic', generation_prompt='AUTHORED_COMMAND', postprocess_prompt='Make a panel', postprocess_target='last_bubble', postprocess_context_options=['world', 'scene', 'turn_messages'], postprocess_block_types=['note'])
    text = chat_command_service.build_command_postprocess_context(session, command, room_id, source, [])
    assert 'ORPHAN_WORLD_SENTINEL' not in text and '[세계관]' not in text
    assert 'COMMAND_SOURCE' in text
    plan = command_overlay.build_plan([command])
    plan = chat_command_service.prepare_combined_context(session, plan, room_id, scene)
    assert 'ORPHAN_WORLD_SENTINEL' not in plan.supplemental_context


def test_authored_hook_and_empty_scene_rendering():
    empty = SceneState(conversation_id='s')
    assert prompts.build_scene_text(empty) == ''
    hook = SceneState(conversation_id='s', opening_scene='AUTHORED_HOOK', user_description='USER_PERSONA')
    text = prompts.build_scene_text(hook)
    assert 'AUTHORED_HOOK' in text
    assert 'world' not in text.lower()
    assert 'Initial location:' not in text


@pytest.mark.parametrize('protocol', ['legacy_json', 'free_body_json', 'tagged_text'])
def test_compiled_worldless_protocols_keep_authored_and_actual_domain(monkeypatch, protocol):
    from app.db.models import Character
    from app.services import system_prompt_service
    monkeypatch.setenv('CHAT_RESPONSE_PROTOCOL', protocol)
    get_settings.cache_clear()
    kwargs = dict(characters=[Character(id='c', name='Mira', persona='PERSONA_SENTINEL')],
                  recent_messages=[], user_message='Source', genre_mode='battle', include_world=False,
                  scene_state=SceneState(conversation_id='s', world_seed='ORPHAN_WORLD_SENTINEL', opening_scene='AUTHORED_HOOK'),
                  prompt_settings=system_prompt_service.prompt_settings_map())
    harness = prompts.build_multi_character_prompt_harness(**kwargs)
    assert 'AUTHORED_HOOK' in harness.compiled_text and 'PERSONA_SENTINEL' in harness.compiled_text
    assert 'world' not in harness.compiled_text.lower()
    assert 'Official domain state' not in harness.compiled_text
    harness = prompts.build_multi_character_prompt_harness(**kwargs, official_domain_context='ACTUAL_RESULT',
        battle_control_context={'action': 'end', 'winner_id': 'c', 'winner_name': 'Mira'})
    assert 'ACTUAL_RESULT' in harness.compiled_text
    assert 'official winner is fixed: Mira(c)' in harness.compiled_text
    kwargs['prompt_settings'] = {'generation_core_contract': 'AUTHORED World policy', 'free_body_generation_core': 'AUTHORED World policy'}
    assert 'AUTHORED World policy' in prompts.build_multi_character_prompt_harness(**kwargs).compiled_text


def test_patch_invalid_world_does_not_detach(client, session):
    world = client.post('/world-settings', json={'title': 'Synthetic stage', 'world_seed': 'KEEP_WORLD'}).json()
    response, _ = create_room(client, world_setting_id=world['id'])
    room_id = response.json()['id']
    assert client.patch(f'/conversations/{room_id}', json={'world_setting_id': 'missing'}).status_code == 422
    assert client.get(f'/conversations/{room_id}').json()['world_setting_id'] == world['id']
    assert session.get(SceneState, room_id).world_seed == 'KEEP_WORLD'


@pytest.mark.parametrize('mode', ['separate', 'combined_overlay'])
def test_worldless_real_command_fallback_keeps_ownership(client, session, monkeypatch, mode):
    monkeypatch.setenv('CHAT_RESPONSE_PROTOCOL', 'tagged_text')
    monkeypatch.setenv('CHAT_COMMAND_POSTPROCESS_MODE', mode)
    get_settings.cache_clear()
    response, _ = create_room(client, scene={'opening_scene': 'AUTHORED_HOOK'})
    room_id = response.json()['id']
    command = client.post('/chat-commands', json={'name': 'worldless_panel', 'generation_prompt': 'AUTHORED_INTENT',
        'postprocess_prompt': 'AUTHORED_PANEL', 'postprocess_target': 'last_bubble',
        'postprocess_context_options': ['world', 'scene', 'turn_messages'], 'postprocess_block_types': ['note']}).json()
    chat_command_service.set_active_command_ids(session, session.get(Conversation, room_id), [command['id']])
    calls = []
    async def chat(self, messages, **kwargs):
        calls.append((self.purpose, messages))
        if self.purpose == 'chat_command_postprocess':
            return LLMResponse(content='{"messages":[{"content":"","blocks":[{"type":"note","text":"PANEL_SENTINEL"}]}]}')
        return LLMResponse(content='<m c="C1">I will keep my promise and meet you tomorrow.</m>')
    monkeypatch.setattr(LLMClient, 'chat', chat)
    monkeypatch.setattr(api, '_start_message_generation_worker', lambda *a: None)
    queued = client.post(f'/conversations/{room_id}/messages/jobs', json={'speaker_type': 'user', 'speaker_id': 'u', 'content': 'SOURCE_SENTINEL'}).json()
    asyncio.run(generation_execution.run_message_generation_job_in_session(session, queued['job']['id']))
    status = client.get(f"/conversations/{room_id}/generation-jobs/{queued['job']['id']}").json()
    assert status['status'] == 'completed', status
    assert any(purpose == 'chat_command_postprocess' for purpose, _ in calls)
    text = json.dumps(calls, ensure_ascii=False)
    assert 'AUTHORED_INTENT' in text and 'AUTHORED_PANEL' in text
    for unwanted in ['World Setting', 'World setting', '[세계관]', 'world_seed', 'scene/world/cast', 'context=world']:
        assert unwanted not in text
    rows = client.get(f'/conversations/{room_id}/messages').json()
    assert 'PANEL_SENTINEL' in json.dumps(rows)
    assert command['id'] in json.dumps(rows)


@pytest.mark.parametrize('strategy', ['fast', 'quality'])
def test_worldless_actual_compression_request_has_only_history_sources(client, session, monkeypatch, strategy):
    from datetime import datetime, timedelta, timezone
    from test_compression_strategies import extraction_payload, critic_payload, valid_arc
    assert client.patch('/runtime-settings/default', json={'compression_strategy': strategy}).status_code == 200
    response, char = create_room(client, scene={'opening_scene': 'AUTHORED_HOOK', 'user_description': 'USER_SENTINEL'})
    room_id = response.json()['id']
    scene = session.get(SceneState, room_id)
    scene.world_seed = 'ORPHAN_WORLD_SENTINEL'
    scene.summary = '[Rolling Story Arc]\n- PREVIOUS_ARC_SENTINEL'
    session.add(scene)
    start = datetime.now(timezone.utc) - timedelta(hours=1)
    for index in range(36):
        session.add(Message(id=f'compress-{index:03}', conversation_id=room_id,
            speaker_type='user' if index % 2 == 0 else 'character',
            speaker_id='u' if index % 2 == 0 else char['id'],
            content=f'PUBLIC_EVENT_{index}: The visitors made a distinct promise.', created_at=start + timedelta(seconds=index)))
    session.commit()
    calls = []
    async def chat(self, messages, **kwargs):
        calls.append(messages)
        if strategy == 'quality' and len(calls) == 1:
            return LLMResponse(content=json.dumps(extraction_payload(), ensure_ascii=False))
        if strategy == 'quality' and len(calls) == 2:
            return LLMResponse(content=json.dumps(critic_payload(), ensure_ascii=False))
        return LLMResponse(content=valid_arc())
    monkeypatch.setattr(LLMClient, 'chat', chat)
    result = client.post(f'/conversations/{room_id}/compress-now')
    assert result.status_code == 200, result.text
    assert calls
    text = json.dumps(calls, ensure_ascii=False)
    for marker in ['PREVIOUS_ARC_SENTINEL', 'PUBLIC_EVENT_0']:
        assert marker in text
    for unwanted in ['ORPHAN_WORLD_SENTINEL', '**World Setting**', 'fixed world setting', 'genre policy', '**Genre Mode Policy**', 'Battle-domain provenance guard']:
        assert unwanted not in text
    session.expire_all()
    scene = session.get(SceneState, room_id)
    assert scene.last_compression_source_message_id
    assert scene.opening_scene == 'AUTHORED_HOOK' and scene.user_description == 'USER_SENTINEL'
    assert scene.world_seed == 'ORPHAN_WORLD_SENTINEL'  # Source omission is not data deletion.


def test_worldless_preview_preserves_real_official_domain_state(client, session):
    from app.db.models import BattleStanding
    response, char = create_room(client, genre_mode='battle')
    room_id = response.json()['id']
    session.add(BattleStanding(conversation_id=room_id, character_id=char['id'], wins=2, points=6, rank=1))
    session.commit()
    preview = client.get(f'/conversations/{room_id}/context-preview')
    assert preview.status_code == 200
    sections = {section['key']: section for section in preview.json()['sections']}
    assert sections['official_domain_state']['included']
    assert char['id'] in sections['official_domain_state']['content']
    assert 'Genre mode: battle' in sections['genre_mode']['content']
    assert session.get(Conversation, room_id).world_setting_id is None


def test_worldless_policy_scoping_never_rewrites_authored_settings():
    from app.services import system_prompt_service
    custom = {key: 'AUTHORED World Setting must survive' for key in (
        'source_priority_rules', 'compression_source_scope_rules', 'compression_scene_base',
        'chat_command_postprocess_source_priority', 'genre_mode_policy_scope')}
    assert system_prompt_service.scoped_prompt_defaults(custom, include_world=False, include_genre=False) == custom
    defaults = system_prompt_service.prompt_settings_map()
    before = dict(defaults)
    assert 'World Setting' not in system_prompt_service.scoped_prompt_defaults(defaults, include_world=False, include_genre=False)['compression_source_scope_rules']
    assert defaults == before
