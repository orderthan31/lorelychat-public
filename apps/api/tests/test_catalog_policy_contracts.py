from copy import deepcopy
from datetime import datetime, timezone

from app.agent.service import AgentService
from app.db.models import Character, WorldSetting, ChatCommand
from app.schemas.world_settings import WorldSettingCreate, WorldSettingUpdate
from app.services import character_service, chat_command_service, world_setting_service


def test_world_normalization_is_pure_and_shared_with_agent_and_crud(session):
    fields = {'title': '  École  ', 'description': '  ', 'genre_mode': ' FANTASY ', 'tags': [' tag ', '', ' 한글 ']}
    original = deepcopy(fields)
    normalized = world_setting_service.normalize_authored_world_fields(fields)
    assert fields == original
    assert normalized == {'title': 'École', 'description': None, 'genre_mode': 'fantasy', 'tags': ['tag', '한글']}
    assert AgentService(None)._normalize('world', fields) == normalized
    world = world_setting_service.create_world_setting(session, WorldSettingCreate(**fields))
    assert {key: getattr(world, key) for key in fields} == normalized
    world_setting_service.update_world_setting(session, world, WorldSettingUpdate(description=' changed '))
    assert world.description == 'changed'
    assert world.updated_at
    normalized['tags'].append('new')
    assert fields == original


def test_domain_pages_keep_unicode_literal_search_ties_and_clamp(session):
    for id in ('b', 'a'):
        session.add(Character(id=id, name='ÉCOLE', persona='literal %_'))
    session.add(Character(id='c', name='Other', persona='literal XX'))
    when = datetime(2020, 1, 1, tzinfo=timezone.utc)
    for id, enabled in [('w2', True), ('w1', True), ('w3', False)]:
        session.add(WorldSetting(id=id, title='ÉCOLE %_', enabled=enabled, updated_at=when, created_at=when))
    for id, name, enabled in [('c1', 'aa', True), ('c2', 'ab', True), ('c3', 'ac', False)]:
        session.add(ChatCommand(id=id, name=name, display_name='ÉCOLE %_', prompt='', enabled=enabled, priority=5))
    session.commit()
    page = character_service.list_characters_page(session, page=99, page_size=1, q='école')
    assert page['total'] == 2 and page['page'] == 2
    assert [item.id for item in page['items']] == ['b']
    assert character_service.list_characters_page(session, page=1, page_size=20, q='%_')['total'] == 2
    empty = character_service.list_characters_page(session, page=99, page_size=20, q='not present')
    assert empty['items'] == [] and empty['pages'] == empty['page'] == 1
    worlds = world_setting_service.list_world_settings_page(session, page=1, page_size=20, q='école %_')
    assert [item.id for item in worlds['items']] == ['w1', 'w2']
    assert world_setting_service.list_world_settings_page(session, page=1, page_size=20, include_disabled=True)['total'] == 3
    commands = chat_command_service.list_chat_commands_page(session, page=1, page_size=20, q='école %_')
    assert [item.id for item in commands['items']] == ['c1', 'c2']
    assert chat_command_service.list_chat_commands_page(session, page=1, page_size=20, include_disabled=True)['total'] == 3
