"""Offline opt-in comparisons: public fidelity and actual wire contracts."""
from dataclasses import replace
from types import SimpleNamespace
import pytest
from pydantic import ValidationError
from app.core.config import Settings, get_settings
from app.db.models import Character, Message, ModelOption, SceneState
from app.engine import prompts
from app.engine.character_runtime import CharacterRuntime
from app.services.context_management_service import ContextMaintenanceRequired, resolve_safe_model_context_capacity


@pytest.fixture(autouse=True)
def clear_settings():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def options(monkeypatch, serialization='native', policy='compact', budget=None):
    monkeypatch.setenv('CHAT_HISTORY_SERIALIZATION', serialization)
    monkeypatch.setenv('CHAT_CONTEXT_POLICY', policy)
    if budget is not None:
        monkeypatch.setenv('CHAT_CONTEXT_INPUT_BUDGET_TOKENS', str(budget))
    get_settings.cache_clear()


def test_comparison_settings_defaults_and_validation():
    settings = Settings(_env_file=None)
    assert settings.chat_history_serialization == 'transcript'
    assert settings.chat_context_policy == 'compact'
    assert settings.chat_context_input_budget_tokens is None
    for fields in ({'chat_history_serialization':'wrong'}, {'chat_context_policy':'wrong'},
                   {'chat_context_input_budget_tokens':0}, {'chat_context_input_budget_tokens':-1}):
        with pytest.raises(ValidationError):
            Settings(_env_file=None, **fields)


def history():
    return [
        Message(id='old-user', conversation_id='room', speaker_type='user', speaker_id='u', content='user full END'),
        Message(id='old-cast', conversation_id='room', speaker_type='character', speaker_id='c', content='legacy END', action='action END', thought='PRIVATE'),
        Message(id='interleave', conversation_id='room', speaker_type='system', content='scene direction DATA'),
        Message(id='bubble', conversation_id='room', speaker_type='character', speaker_id='c', content='aggregate MUST NOT APPEAR', metadata_={'response_protocol':'tagged_text','render_parts':[
            {'type':'action','text':'ordered first'}, {'type':'thought','text':'PRIVATE'}, {'type':'dialogue','text':'ordered last'}]}),
    ]


def test_native_history_channel_full_public_and_scene_controls(monkeypatch):
    options(monkeypatch, policy='full_history')
    scene = SceneState(conversation_id='room', summary='DERIVED ARC', last_compression_source_message_id='old-cast',
                       world_seed='AUTHORED WORLD', opening_scene='STALE OPENING', location='STALE LOCATION', mood='STALE MOOD')
    harness = prompts.build_multi_character_prompt_harness(characters=[Character(id='c', name='Canon')],
        recent_messages=history(), user_message='LATEST', scene_state=scene, context_management_mode='automatic')
    assert harness.history_message_ids == ('old-user','old-cast','interleave','bubble')
    assert [m.role for m in harness.prior_messages] == ['user','assistant','user','assistant']
    text = '\n'.join(m.content for m in harness.prior_messages)
    assert 'character:Canon(c)' in text and 'action END' in text and 'legacy END' in text
    assert text.index('ordered first') < text.index('ordered last')
    assert 'PRIVATE' not in text and 'aggregate MUST NOT APPEAR' not in text
    assert 'scene direction DATA' not in harness.compiled_text
    assert 'AUTHORED WORLD' in harness.compiled_text
    assert not any(x in harness.compiled_text for x in ['DERIVED ARC','STALE OPENING','STALE LOCATION','STALE MOOD'])
    assert scene.summary == 'DERIVED ARC' and scene.last_compression_source_message_id == 'old-cast'


def test_native_and_transcript_share_complete_public_coverage_and_digest(monkeypatch):
    rows=history()
    rows[0].content=' '.join(f'Unique user instruction {i}.' for i in range(100))+' USER_END'
    rows[1].action=' '.join(f'Unique visible action {i}.' for i in range(80))+' ACTION_END'
    rows[1].content=' '.join(f'Unique spoken line {i}.' for i in range(100))+' DIALOGUE_END'
    rows[2].content=' '.join(f'Unique scene direction {i}.' for i in range(100))+' SYSTEM_END'
    options(monkeypatch,'transcript')
    transcript=prompts.build_multi_character_prompt_harness(characters=[Character(id='c',name='Canon')],
        recent_messages=rows,user_message='current',context_management_mode='automatic')
    options(monkeypatch,'native')
    native=prompts.build_multi_character_prompt_harness(characters=[Character(id='c',name='Canon')],
        recent_messages=rows,user_message='current',context_management_mode='automatic')
    section=next(s for s in transcript.sections if s.key=='recent_messages')
    assert section.content=='\n'.join(m.content for m in native.prior_messages)
    assert transcript.history_message_ids==native.history_message_ids
    assert transcript.history_sha256==native.history_sha256
    assert all(marker in section.content for marker in ['USER_END','ACTION_END','DIALOGUE_END','SYSTEM_END'])
    assert 'PRIVATE' not in section.content


def test_full_history_fresh_room_keeps_authored_opening_without_replaying_stale_opening(monkeypatch):
    options(monkeypatch,'native','full_history')
    scene=SceneState(conversation_id='r',location='FRESH LOCATION',mood='FRESH MOOD',opening_scene='AUTHORED FIRST SCENE')
    kwargs={'characters':[Character(id='c',name='Canon')],'scene_state':scene,'user_message':'Begin',
        'context_management_mode':'automatic'}
    fresh=prompts.build_multi_character_prompt_harness(recent_messages=[],**kwargs)
    assert 'AUTHORED FIRST SCENE' in fresh.compiled_text and 'FRESH LOCATION' in fresh.compiled_text
    after=prompts.build_multi_character_prompt_harness(recent_messages=history(),**kwargs)
    assert 'AUTHORED FIRST SCENE' not in after.compiled_text and 'FRESH LOCATION' not in after.compiled_text


@pytest.mark.asyncio
@pytest.mark.parametrize('source_type', ['user','character'])
async def test_native_runtime_retry_frozen_channel_and_real_snapshot(monkeypatch, source_type):
    options(monkeypatch)
    monkeypatch.setenv('CHAT_RESPONSE_PROTOCOL','tagged_text')
    get_settings.cache_clear()
    calls, snapshots = [], []
    async def transport(**kwargs):
        calls.append(kwargs['messages'])
        options(monkeypatch, serialization='transcript', policy='full_history')
        return SimpleNamespace(content='<m c="UNKNOWN">bad</m>' if len(calls)==1 else '<m c="C1">Public reply <t>Private voice</t></m>', finish_reason='STOP')
    monkeypatch.setattr('app.engine.character_runtime.chat_with_optional_conversation_id', transport)
    await CharacterRuntime().generate_replies(characters=[Character(id='c',name='Canon')], recent_messages=history(),
        user_message='LATEST', source_speaker_type=source_type, prompt_snapshot_callback=snapshots.append)
    assert len(calls)==2
    assert [x['role'] for x in calls[0]][:5] == ['system','user','assistant','user','assistant']
    assert calls[0][1:5] == calls[1][1:5]
    assert sum('LATEST' in x['content'] for x in calls[1])==1
    assert calls[0][-1]['role']=='user'
    assert snapshots[-1]['messages']==calls[-1]
    assert snapshots[-1]['metadata']['history_serialization']=='native'
    assert 'PRIVATE' not in str(calls)


def test_expanded_capacity_clamps_and_unknown_does_not_expand(monkeypatch):
    from app.engine.harness_comparison import ComparisonOptions
    primary = ModelOption(key='wide', context_window_tokens=100000, max_output_tokens=2000)
    fallback = ModelOption(key='narrow', context_window_tokens=20000, max_output_tokens=2000)
    opts = ComparisonOptions('native','expanded',90000)
    cap = resolve_safe_model_context_capacity(primary, fallback, room_prompt_budget_tokens=8000,
        completion_reserve_tokens=8192, comparison_options=opts)
    assert cap.working_input_tokens==10784 and cap.completion_reserve_tokens==8192
    unknown = resolve_safe_model_context_capacity(None, primary, room_prompt_budget_tokens=8000,
        completion_reserve_tokens=2048, comparison_options=opts)
    assert unknown.working_input_tokens==8000
    for invalid_context in (-1, 0, True):
        invalid=ModelOption(key='invalid',context_window_tokens=invalid_context,max_output_tokens=2000)
        conservative=resolve_safe_model_context_capacity(invalid,primary,room_prompt_budget_tokens=8000,
            completion_reserve_tokens=2048,comparison_options=opts)
        assert conservative.working_input_tokens<=8000
    for opts in (ComparisonOptions('native','expanded',None), ComparisonOptions('native','compact',90000)):
        unchanged=resolve_safe_model_context_capacity(primary,room_prompt_budget_tokens=8000,
            completion_reserve_tokens=2048,comparison_options=opts)
        assert unchanged.working_input_tokens==8000 and unchanged.completion_reserve_tokens==2048


@pytest.mark.asyncio
@pytest.mark.parametrize('primary_output,fallback_output',[(8192,None),(2048,8192)])
async def test_legacy_wrapper_reserves_actual_output_before_dispatch(monkeypatch,primary_output,fallback_output):
    calls=[]
    async def transport(**kwargs):
        calls.append(kwargs)
        raise AssertionError('must reject before transport')
    monkeypatch.setattr('app.engine.character_runtime.chat_with_optional_conversation_id', transport)
    monkeypatch.setattr('app.engine.character_runtime.build_character_messages', lambda **kwargs: [
        {'role':'system','content':'x'*22000}, {'role':'user','content':'source'}])
    client=SimpleNamespace(max_output_tokens=lambda:primary_output, settings=SimpleNamespace(context_management_mode='automatic'))
    fallback=SimpleNamespace(max_output_tokens=lambda:fallback_output) if fallback_output is not None else None
    with pytest.raises(ContextMaintenanceRequired):
        await CharacterRuntime(llm_client=client,fallback_llm_client=fallback).generate_reply(character=Character(id='c',name='C'), recent_messages=[], user_message='source')
    assert calls==[]


@pytest.mark.asyncio
@pytest.mark.parametrize('provider',['xai','gemini'])
@pytest.mark.parametrize('protocol',['tagged_text','free_body_json'])
@pytest.mark.parametrize('source_type',['user','character'])
async def test_native_actual_mocktransport_initial_retry_and_continuation(monkeypatch,provider,protocol,source_type):
    import httpx,json
    from app.engine.llm_client import LLMClient
    options(monkeypatch)
    monkeypatch.setenv('CHAT_RESPONSE_PROTOCOL',protocol);get_settings.cache_clear()
    payloads=[]
    def handler(request):
        payloads.append(json.loads(request.content))
        who='UNKNOWN' if len(payloads)==1 else 'C1'
        raw=f'<m c="{who}">Full new reply.<t>Private voice.</t></m>' if protocol=='tagged_text' else json.dumps({'replies':[{'who':who,'text':'Full new reply.<t>Private voice.</t>'}]})
        if provider=='gemini':
            return httpx.Response(200,json={'candidates':[{'content':{'parts':[{'text':raw}]},'finishReason':'STOP'}]})
        return httpx.Response(200,json={'choices':[{'message':{'content':raw},'finish_reason':'stop'}]})
    real=httpx.AsyncClient
    monkeypatch.setattr(httpx,'AsyncClient',lambda *a,**k:real(*a,**k,transport=httpx.MockTransport(handler)))
    client=LLMClient(Settings(_env_file=None,llm_mock=False),overrides={'provider':provider,'provider_type':provider,
        'model':'comparison-synthetic','base_url':'https://provider.invalid/v1','api_key':'synthetic','min_output_tokens':1400})
    monkeypatch.setattr(client,'_record_usage',lambda *a,**k:None)
    monkeypatch.setattr(client,'record_response_outcome',lambda *a,**k:None)
    snapshots=[]
    result=await CharacterRuntime(client).generate_replies(characters=[Character(id='c',name='Canon')],
        recent_messages=history(),user_message='ONLY LATEST',source_speaker_type=source_type,prompt_snapshot_callback=snapshots.append)
    assert len(payloads)==2 and result[0].thought=='Private voice.'
    for payload in payloads:
        messages=payload['contents'] if provider=='gemini' else payload['messages'][1:]
        assert [m['role'] for m in messages[:4]]==(['user','model','user','model'] if provider=='gemini' else ['user','assistant','user','assistant'])
        text=json.dumps(messages,ensure_ascii=False)
        assert text.count('ONLY LATEST')==1 and 'PRIVATE' not in text
        assert 'scene direction DATA' in text and 'ordered last' in text
        system=json.dumps(payload['systemInstruction'] if provider=='gemini' else payload['messages'][0])
        assert 'scene direction DATA' not in system and 'ordered first' not in system
        assert (payload['generationConfig']['maxOutputTokens'] if provider=='gemini' else payload['max_tokens'])==client.max_output_tokens()
        if protocol=='tagged_text':
            assert 'response_format' not in payload
            assert not {'responseSchema','responseMimeType'} & payload.get('generationConfig',{}).keys()
    assert snapshots[0]['messages'][1:5]==[m.as_dict() for m in prompts.build_multi_character_prompt_harness(
        characters=[Character(id='c',name='Canon')],recent_messages=history(),user_message='ONLY LATEST',
        context_management_mode='automatic').prior_messages]
