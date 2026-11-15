"""Offline help/schema/standard-loop evidence, NOT an LLM quality evaluation."""
import asyncio
import json

import pytest
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy import text
from sqlmodel import Session

from app.agent.contracts import AgentError, CHARACTER_FIELDS, WORLD_FIELDS, TOOL_SCHEMAS
from app.agent.knowledge import FIELD_HELP, PRODUCT_PRIMER, field_help
from app.agent.operations import AgentOperations, TOOL_SPECS
from app.agent.runtime import SYSTEM_PROMPT, build_agent, build_tools
from app.agent.chat_model import LoreAgentChatModel
from app.schemas.characters import CharacterCreate, DEFAULT_TRAIT_SCORES
from app.schemas.world_settings import WorldSettingCreate

WORLD = {
    'title': '종이 항구', 'genre_mode': 'mystery',
    'description': '기억을 맡기는 항구의 미스터리.',
    'world_seed': '항구는 기억을 종이에 보관한다. 야간에는 거래가 금지된다.\n예외: 등대지기는 잃어버린 이름을 찾는 사람만 돕는다.',
    'opening_scene': '새벽의 등대 앞에서 젖은 장부가 발견된다.',
    'opening_line': '이 이름을 어디서 보았죠?',
    'compression_focus': '장부의 이름과 등대지기의 예외 규칙',
    'tags': ['항구', '기억'], 'enabled': True,
}
CHARACTER = {
    'name': '등대지기', 'persona': '잃어버린 이름을 찾는 항해사를 돕는다.\n거래는 거절하지만 이름의 주인에게만 기록을 돌려준다.',
    'appearance': '소금 묻은 외투', 'behavior_style': '장부를 먼저 확인한다.',
    'speech_style': '짧은 존댓말', 'emotional_rules': ['기억을 잃은 손님에게 조용히 공감한다.'],
    'forbidden_rules': ['손님의 결정을 대신하지 않는다.'], 'trait_scores': {'kindness': 4},
}


def test_help_matches_only_authored_schema_fields_and_types():
    for kind, allowed, model in [('character', CHARACTER_FIELDS, CharacterCreate),
                                 ('world', WORLD_FIELDS, WorldSettingCreate)]:
        assert set(FIELD_HELP[kind]) == set(field_help(kind)) == allowed
        assert allowed <= model.model_fields.keys()
        assert all(f'{key}:' in TOOL_SPECS[f'lore_stage_{kind}'].description for key in allowed)
        required = {key for key, field in model.model_fields.items() if field.is_required()}
        assert required == ({'name', 'persona'} if kind == 'character' else {'title'})
        assert all('Required' in field_help(kind)[key] for key in required)
    assert not {'avatar_url', 'default_model', 'tts_provider'} & set(field_help('character'))
    assert 'thumbnail_url' not in field_help('world')
    assert all(key in field_help('character')['trait_scores'] for key in DEFAULT_TRAIT_SCORES)
    assert CharacterCreate.model_validate(CHARACTER).trait_scores['kindness'] == 4
    assert WorldSettingCreate.model_validate(WORLD).world_seed == WORLD['world_seed']
    from app.services.conversation_service import VALID_GENRE_MODES, normalize_genre_mode
    assert all(mode in field_help('world')['genre_mode'] for mode in VALID_GENRE_MODES)
    assert normalize_genre_mode('unsupported') == 'battle'
    assert len(PRODUCT_PRIMER) < 4000


def test_world_help_matches_scene_consumer():
    from app.db.models import WorldSetting
    from app.services.conversation_service import scene_payload_from_world_setting, opening_line_from_scene_data
    world = WorldSetting(id='synthetic-world', **WORLD)
    scene = scene_payload_from_world_setting(world, None)
    assert scene['world_seed'] == WORLD['world_seed']
    assert scene['opening_scene'] == WORLD['opening_scene']
    assert opening_line_from_scene_data(scene) == WORLD['opening_line']
    assert 'description' not in scene
    assert scene_payload_from_world_setting(None, None) == {}


def test_capabilities_and_langchain_share_bounded_help(service, owner):
    operations = AgentOperations(service, owner)
    result = asyncio.run(operations.execute('lore_capabilities', {}))
    assert result['product_guide'] == PRODUCT_PRIMER
    assert result['field_help'] == FIELD_HELP
    assert set(result['tools']) == set(TOOL_SCHEMAS)
    assert result['approval'] == 'owner_only'
    assert len(json.dumps(result, ensure_ascii=False)) < 12000
    result['field_help']['world']['title'] = 'mutated'
    assert asyncio.run(operations.execute('lore_capabilities', {}))['field_help'] == FIELD_HELP
    tools = build_tools(operations, lambda: None)
    assert {tool.name for tool in tools} == set(TOOL_SCHEMAS)
    for tool in tools:
        assert tool.description == TOOL_SPECS[tool.name].description
        assert tool.args_schema is TOOL_SCHEMAS[tool.name]
    assert 'lore_approve_draft' not in TOOL_SPECS


def test_official_mcp_lists_same_help_and_returns_product_guide(service):
    from mcp import Client
    from mcp.server.auth.middleware.auth_context import auth_context_var
    from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser
    from mcp.server.auth.provider import AccessToken
    from app.agent.contracts import SettingsUpdate
    from app.mcp import build_mcp_gateway
    service.update_settings(SettingsUpdate(enabled=True, mcp_enabled=True))
    client = service.create_client('synthetic-help-reader', ['lore:read'])
    gateway = build_mcp_gateway(lambda: service)
    async def exercise():
        async with gateway.lifespan():
            context = auth_context_var.set(AuthenticatedUser(AccessToken(
                token=client['token'], client_id=client['client']['id'], scopes=['lore:read'])))
            try:
                async with Client(gateway.server) as sdk:
                    listed = await sdk.list_tools()
                    assert {tool.name for tool in listed.tools} == set(TOOL_SPECS)
                    for tool in listed.tools:
                        assert tool.description == TOOL_SPECS[tool.name].description
                        assert tool.input_schema == TOOL_SCHEMAS[tool.name].model_json_schema()
                    result = await sdk.call_tool('lore_capabilities', {})
                    assert not result.is_error
                    assert result.structured_content['product_guide'] == PRODUCT_PRIMER
                    assert result.structured_content['field_help'] == FIELD_HELP
            finally:
                auth_context_var.reset(context)
    asyncio.run(exercise())


def response(*, name=None, args=None, call_id='synthetic-call', content=None):
    message = {'role': 'assistant', 'content': content}
    if name:
        message['tool_calls'] = [{'id': call_id, 'type': 'function', 'function': {
            'name': name, 'arguments': json.dumps(args, ensure_ascii=False)}}]
    return {'choices': [{'message': message, 'finish_reason': 'tool_calls' if name else 'stop'}]}


@pytest.mark.parametrize('kind,fields', [('world', WORLD), ('character', CHARACTER)])
def test_first_wire_has_primer_and_real_tools_stage_then_request_review(service, owner, kind, fields):
    calls, drafts = [], []
    async def dispatch(route, *, messages, tools, before_send):
        before_send()
        calls.append(messages)
        prompt = messages[0]['content']
        assert prompt.startswith(PRODUCT_PRIMER)
        assert SYSTEM_PROMPT in prompt
        assert prompt.count(PRODUCT_PRIMER) == 1
        bound = {tool['function']['name']: tool['function'] for tool in tools}
        assert set(bound) == set(TOOL_SCHEMAS)
        assert bound[f'lore_stage_{kind}']['description'] == TOOL_SPECS[f'lore_stage_{kind}'].description
        if len(calls) == 1:
            return response(name=f'lore_stage_{kind}', args={'fields': fields}, call_id='stage')
        tool_result = json.loads(messages[-1]['content'])
        assert messages[-1]['role'] == 'tool'
        if len(calls) == 2:
            assert messages[-1]['tool_call_id'] == 'stage'
            drafts.append(tool_result)
            assert tool_result['status'] == 'draft'
            for key, value in fields.items():
                if key != 'trait_scores':
                    assert tool_result['after'][key] == value
            return response(name='lore_request_approval', args={'draft_id': tool_result['id']}, call_id='review')
        assert messages[-1]['tool_call_id'] == 'review'
        assert tool_result['approval_required'] is True
        assert tool_result['draft']['status'] == 'draft'
        return response(content='초안을 준비했습니다. 소유자 검토 대기 중이며 아직 저장하지 않았습니다.')
    async def exercise():
        operations = AgentOperations(service, owner)
        model = LoreAgentChatModel(resolve_route=lambda: object(), before_dispatch=lambda: None, dispatch=dispatch)
        graph = build_agent(model, build_tools(operations, lambda: None), InMemorySaver())
        result = await graph.ainvoke({'messages': [HumanMessage(content='다음 합성 설정으로 초안을 만들어 줘: ' + json.dumps(fields, ensure_ascii=False))]},
                                    {'configurable': {'thread_id': f'guide-{kind}'}})
        assert '검토 대기' in result['messages'][-1].content
        draft = service.get_draft(drafts[0]['id'], owner)
        assert draft['warnings'] == ['Owner approval requested for this exact preview.']
        assert draft['status'] == 'draft'
        with pytest.raises(AgentError, match='approval'):
            await operations.execute('lore_commit_draft', {'draft_id': draft['id'], 'expected_version': draft['version'],
                'preview_hash': draft['preview_hash'], 'request_id': 'unapproved-attempt'})
    asyncio.run(exercise())
    assert len(calls) == 3
    with service.engine.connect() as connection:
        for table in ('characters', 'world_settings'):
            assert connection.execute(text(f'SELECT count(*) FROM {table}')).scalar_one() == 0


def test_sparse_edit_preserves_unmentioned_fields_and_reapproval(service, owner):
    from app.db.models import Character
    with Session(service.engine) as session:
        session.add(Character(id='synthetic-character', **CHARACTER))
        session.commit()
    async def exercise():
        operations = AgentOperations(service, owner)
        found = await operations.execute('lore_search', {'query': CHARACTER['name'], 'kind': 'character'})
        target = found['items'][0]['id']
        original = await operations.execute('lore_get_character', {'id': target, 'fields': ['persona', 'speech_style']})
        draft = await operations.execute('lore_stage_character', {'target_id': target, 'fields': {'speech_style': '느린 존댓말'}})
        assert draft['after'] == {'speech_style': '느린 존댓말'}
        service.approve_draft(owner, draft['id'], draft['version'], draft['preview_hash'])
        revised = await operations.execute('lore_revise_draft', {'draft_id': draft['id'], 'expected_version': draft['version'],
            'fields': {'speech_style': '차분한 존댓말'}})
        assert revised['status'] == 'draft' and revised['version'] == 2
        review = await operations.execute('lore_request_approval', {'draft_id': draft['id']})
        assert review['approval_required'] is True
        unchanged = await operations.execute('lore_get_character', {'id': target, 'fields': ['persona', 'speech_style']})
        assert unchanged == original
    asyncio.run(exercise())


def test_advice_only_can_finish_without_tools(service, owner):
    calls = []
    async def dispatch(route, *, messages, tools, before_send):
        before_send()
        calls.append(messages)
        assert 'Advice-only or brainstorming questions do not require a write.' in messages[0]['content']
        return response(content='세계관은 선택 사항이며 캐릭터만으로 대화할 수 있습니다.')
    async def exercise():
        graph = build_agent(LoreAgentChatModel(resolve_route=lambda: object(), before_dispatch=lambda: None, dispatch=dispatch),
            build_tools(AgentOperations(service, owner), lambda: None), InMemorySaver())
        result = await graph.ainvoke({'messages': [HumanMessage(content='세계관은 반드시 필요한가요? 저장하지 말고 설명만 해줘.')]},
                                    {'configurable': {'thread_id': 'advice'}})
        assert len(result['messages']) == 2
    asyncio.run(exercise())
    assert len(calls) == 1
