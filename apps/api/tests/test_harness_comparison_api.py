"""Actual isolated API/job/preview tests for comparison paths."""
import asyncio
from datetime import datetime, timedelta, timezone
import pytest
from sqlmodel import select
from app.core.config import get_settings
from app.db.models import Message, SceneState, MessageGenerationJob, PostCommitTask, LLMPromptSnapshot, CharacterMemory
from app.engine.llm_client import LLMResponse
from app.services import conversation_service
from app.api import conversations as api
from test_harness_comparisons import options


@pytest.fixture(autouse=True)
def settings_reset():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def seed(client, session):
    char=client.post('/characters', json={'name':'Canon','persona':'AUTHORED CARD'}).json()
    room=client.post('/conversations', json={'mode':'user_character','genre_mode':'slice_of_life','participants':[
        {'type':'user','id':'user_001'}, {'type':'character','id':char['id']}]}).json()
    start=datetime.now(timezone.utc)-timedelta(days=1)
    rows=[]
    for i, kind in enumerate(['user','character','system','character']):
        row=Message(id=f'comparison-old-{i}',conversation_id=room['id'],speaker_type=kind,
            speaker_id=char['id'] if kind=='character' else 'user_001',content=f'PUBLIC FACT {i} END',
            action=f'ACTION {i} END' if kind=='character' else None, thought='HISTORICAL PRIVATE' if kind=='character' else None,
            created_at=start+timedelta(seconds=i),generation_job_id='historical-job' if kind=='character' else None)
        rows.append(row);session.add(row)
    scene=session.get(SceneState,room['id']) or SceneState(conversation_id=room['id'])
    scene.world_seed='AUTHORED WORLD';scene.user_description='AUTHORED PERSONA'
    scene.summary='DERIVED ARC MUST STAY STORED';scene.last_compression_source_message_id=rows[1].id
    scene.compression_revision=7;scene.location='STALE LOCATION';scene.mood='STALE MOOD';scene.opening_scene='STALE OPENING'
    session.add(scene)
    session.add(CharacterMemory(id='comparison-room-note',conversation_id=room['id'],
        character_id=conversation_service.COMMON_ROOM_MEMORY_CHARACTER_ID,memory_type='user_note',content='AUTHORED ROOM NOTE'))
    session.add(CharacterMemory(id='comparison-cast-note',conversation_id=room['id'],
        character_id=char['id'],memory_type='user_note',content='AUTHORED CHARACTER NOTE'))
    session.commit()
    return room,char,rows


@pytest.mark.parametrize('serialization',['native','transcript'])
@pytest.mark.parametrize('too_small',[False,True])
def test_full_history_real_job_preview_and_saved_arc(client,session,monkeypatch,serialization,too_small):
    options(monkeypatch,serialization,'full_history',100 if too_small else 10000)
    monkeypatch.setenv('CHAT_RESPONSE_PROTOCOL','tagged_text');get_settings.cache_clear()
    room,char,rows=seed(client,session)
    monkeypatch.setattr(api,'_start_message_generation_worker',lambda *a:None)
    main,compress=[],[]
    async def chat(self,messages,**kwargs):
        main.append(messages)
        return LLMResponse(content='<m c="C1">A complete new reply.<t>Private voice survives.</t></m>',finish_reason='STOP')
    async def summarizer(*args,**kwargs):
        compress.append(args)
        raise AssertionError('full history owns no automatic compression')
    monkeypatch.setattr('app.engine.llm_client.LLMClient.chat',chat)
    monkeypatch.setattr(conversation_service,'update_scene_orchestration_summary',summarizer)
    queued=client.post(f"/conversations/{room['id']}/messages/jobs",json={
        'speaker_type':'user','speaker_id':'user_001','content':'EXACT LATEST SOURCE','client_request_id':'comparison-request'})
    assert queued.status_code==200,queued.text
    data=queued.json();job_id=data['job']['id']
    preview_response=client.get(f"/conversations/{room['id']}/context-preview")
    assert preview_response.status_code==200,preview_response.text
    preview=preview_response.json()
    assert preview['context_policy']=='full_history'
    assert preview['history_serialization']==serialization
    assert preview['history_message_ids']==[m.id for m in rows]
    if serialization=='native':
        assert not any(s['key']=='recent_messages' for s in preview['sections'])
        assert all('outside system' in s['title'] for s in preview['sections'] if s['key'].startswith('native_history'))
    asyncio.run(api._run_message_generation_job_in_session(session,job_id))
    session.expire_all()
    job=session.get(MessageGenerationJob,job_id)
    saved=session.get(SceneState,room['id'])
    assert (saved.summary,saved.last_compression_source_message_id,saved.compression_revision)==('DERIVED ARC MUST STAY STORED',rows[1].id,7)
    assert compress==[]
    tasks=session.exec(select(PostCommitTask).where(PostCommitTask.unique_key.startswith(f'job:{job_id}:'))).all()
    assert not any(t.task_type=='compression' for t in tasks)
    got=client.get(f"/conversations/{room['id']}/messages").json()
    public_job=client.get(f"/conversations/{room['id']}/generation-jobs/{job_id}").json()
    if too_small:
        assert main==[] and tasks==[]
        assert job.status=='failed' and public_job['error_code']=='context_maintenance_required'
        assert len(got)==len(rows)+1
        assert any('context_budget_exceeded' in w for w in preview['warnings'])
    else:
        assert job.status=='completed',job.error_message
        assert len(main)==1
        wire='\n'.join(m['content'] for m in main[0])
        for value in ['AUTHORED WORLD','AUTHORED CARD','AUTHORED PERSONA','AUTHORED ROOM NOTE','AUTHORED CHARACTER NOTE',*[m.content for m in rows]]:
            assert value in wire
        assert wire.count('EXACT LATEST SOURCE')==1
        for value in ['HISTORICAL PRIVATE','DERIVED ARC MUST STAY STORED','STALE LOCATION','STALE MOOD','STALE OPENING']:
            assert value not in wire
        assert got[-1]['thought']=='Private voice survives.'
        snap=session.exec(select(LLMPromptSnapshot).where(LLMPromptSnapshot.source_message_id==data['incoming_message']['id'])).one()
        assert snap.messages_json==main[0]
        asyncio.run(api._run_message_generation_job_in_session(session,job_id))
        assert len(main)==1 and compress==[]


@pytest.mark.parametrize('policy',['compact','expanded','full_history'])
def test_native_preview_pressure_and_dispatch_threshold_agree(client,session,monkeypatch,policy):
    from dataclasses import replace
    from app.services import context_preview_service
    from app.services.context_management_service import resolve_safe_model_context_capacity
    options(monkeypatch,'native',policy,6000)
    monkeypatch.setenv('CHAT_RESPONSE_PROTOCOL','tagged_text');get_settings.cache_clear()
    room,char,rows=seed(client,session)
    monkeypatch.setattr(api,'_start_message_generation_worker',lambda *a:None)
    queued=client.post(f"/conversations/{room['id']}/messages/jobs",json={
        'speaker_type':'user','speaker_id':'user_001','content':'THRESHOLD SOURCE'}).json()
    preview=client.get(f"/conversations/{room['id']}/context-preview").json()
    threshold=preview['projected_input_tokens']
    narrow=replace(resolve_safe_model_context_capacity(None),working_input_tokens=threshold-1)
    monkeypatch.setattr(api,'resolve_safe_model_context_capacity',lambda *a,**k:narrow)
    monkeypatch.setattr(context_preview_service,'resolve_safe_model_context_capacity',lambda *a,**k:narrow)
    guarded=[]
    original=conversation_service.automatic_context_plan
    def capture(*args,**kwargs):
        plan=original(*args,**kwargs);guarded.append(plan);return plan
    monkeypatch.setattr(conversation_service,'automatic_context_plan',capture)
    calls=[]
    async def no_compress(*args,**kwargs):
        calls.append('compression')
        return session.get(SceneState,room['id'])
    async def no_chat(*args,**kwargs):
        calls.append('main');raise AssertionError('over capacity')
    monkeypatch.setattr(conversation_service,'update_scene_orchestration_summary',no_compress)
    monkeypatch.setattr('app.engine.llm_client.LLMClient.chat',no_chat)
    tiny=client.get(f"/conversations/{room['id']}/context-preview").json()
    assert tiny['projected_input_tokens']==threshold
    assert any('context_budget_exceeded' in w for w in tiny['warnings'])
    client.get(f"/conversations/{room['id']}/compression-preview")
    asyncio.run(api._run_message_generation_job_in_session(session,queued['job']['id']))
    session.expire_all()
    assert session.get(MessageGenerationJob,queued['job']['id']).status=='failed'
    assert 'main' not in calls
    if policy=='full_history':
        assert calls==[] and guarded==[]
    else:
        assert guarded and all(p.pressure.projected_input_tokens==threshold for p in guarded)


def test_automatic_compression_tasks_respect_frozen_full_policy_but_manual_keeps_authority(client,session,monkeypatch):
    options(monkeypatch,'native','full_history')
    room,char,rows=seed(client,session)
    calls=[]
    async def manual(db,conversation_id,recent_messages,**kwargs):
        calls.append(kwargs)
        return db.get(SceneState,conversation_id)
    monkeypatch.setattr(conversation_service,'update_scene_orchestration_summary',manual)
    # A task marked full-history remains inert even after the deployment rolls back.
    options(monkeypatch,'transcript','compact')
    task=PostCommitTask(id='comparison-task',unique_key='comparison-task',task_type='compression',
        status='processing',lease_owner='comparison-worker',payload_={'conversation_id':room['id'],
        'generated_character_count':1,'character_ids':[char['id']], 'chat_context_policy':'full_history'})
    session.add(task);session.commit()
    asyncio.run(api._execute_claimed_post_commit_task(session,task,lease_owner='comparison-worker'))
    session.expire_all()
    assert session.get(PostCommitTask,task.id).status=='completed' and calls==[]
    options(monkeypatch,'native','full_history')
    response=client.post(f"/conversations/{room['id']}/compress-now")
    assert response.status_code==200,response.text
    assert len(calls)==1


@pytest.mark.parametrize('policy',['compact','full_history'])
@pytest.mark.parametrize('protocol',['tagged_text','free_body_json'])
@pytest.mark.parametrize('case',['success','malformed_primary','targeted_fallback','exhausted_fallback'])
def test_native_real_api_combined_job_atomicity_replay(client,session,monkeypatch,policy,protocol,case):
    import json
    from app.db.models import Conversation
    from app.services import chat_command_service
    from test_combined_command_runtime import encode
    options(monkeypatch,'native',policy)
    monkeypatch.setenv('CHAT_RESPONSE_PROTOCOL',protocol)
    monkeypatch.setenv('CHAT_COMMAND_POSTPROCESS_MODE','combined_overlay');get_settings.cache_clear()
    room,char,rows=seed(client,session)
    command=client.post('/chat-commands',json={'name':'comparison_panel','generation_prompt':'Authored panel intent.',
        'postprocess_prompt':'Render only the panel.','postprocess_target':'last_bubble',
        'postprocess_context_options':[],'postprocess_block_types':['note']}).json()
    chat_command_service.set_active_command_ids(session,session.get(Conversation,room['id']),[command['id']])
    monkeypatch.setattr(api,'_start_message_generation_worker',lambda *a:None)
    calls=[];compress=[]
    async def chat(self,messages,**kwargs):
        calls.append((self.purpose,messages))
        if self.purpose=='chat_command_postprocess':
            return LLMResponse(content='invalid' if case=='exhausted_fallback' else '{"messages":[{"content":"","blocks":[{"type":"note","text":"Recovered panel"}]}]}')
        raw=encode(protocol,who='BAD' if case=='malformed_primary' else 'C1')
        if case in {'targeted_fallback','exhausted_fallback'}:
            raw=raw.split('Ready.')[0]+'Unfinished panel'
        return LLMResponse(content=raw,finish_reason='LENGTH' if 'fallback' in case else 'STOP')
    async def no_compress(*args,**kwargs):
        compress.append(args);raise AssertionError('fixture is below pressure')
    monkeypatch.setattr('app.engine.llm_client.LLMClient.chat',chat)
    monkeypatch.setattr(conversation_service,'update_scene_orchestration_summary',no_compress)
    queued=client.post(f"/conversations/{room['id']}/messages/jobs",json={
        'speaker_type':'user','speaker_id':'user_001','content':'COMPARISON SOURCE'}).json()
    job_id=queued['job']['id']
    asyncio.run(api._run_message_generation_job_in_session(session,job_id))
    status=client.get(f"/conversations/{room['id']}/generation-jobs/{job_id}").json()
    data=client.get(f"/conversations/{room['id']}/messages").json()
    primary_calls=[messages for purpose,messages in calls if purpose=='chat_generation']
    fallback_calls=[messages for purpose,messages in calls if purpose=='chat_command_postprocess']
    assert len(primary_calls)==(2 if case=='malformed_primary' else 1)
    assert len(fallback_calls)==({'targeted_fallback':1,'exhausted_fallback':2}.get(case,0))
    assert compress==[]
    for messages in primary_calls:
        assert len(messages)>2 and messages[1]['role']=='user'
        wire=json.dumps(messages)
        assert wire.count('COMPARISON SOURCE')==1 and 'HISTORICAL PRIVATE' not in wire
        if policy=='full_history':
            assert rows[0].content in wire and 'DERIVED ARC MUST STAY STORED' not in wire
    session.expire_all()
    snapshot=session.exec(select(LLMPromptSnapshot).where(LLMPromptSnapshot.source_message_id==queued['incoming_message']['id'])).one()
    assert snapshot.messages_json==primary_calls[-1]
    if case=='malformed_primary':
        assert status['status']=='failed' and len(data)==len(rows)+1
        assert not session.exec(select(PostCommitTask)).all()
        assert snapshot.metadata_['parse_status']=='failed'
    else:
        assert status['status']=='completed',status
        generated=[m for m in data if m['id'] in status['generated_message_ids']]
        assert generated[0]['content']=='First.\nLast.' and generated[0]['thought']=='Secret.'
        assert len(generated)==(1 if case=='exhausted_fallback' else 2)
        source=next(m for m in data if m['id']==queued['incoming_message']['id'])
        outcome=source['metadata']['command_postprocess_execution'][command['id']]
        assert outcome['status']==('combined_success' if case=='success' else 'fallback')
        assert snapshot.metadata_['parse_status']=='success'
        before=len(calls)
        asyncio.run(api._run_message_generation_job_in_session(session,job_id))
        assert len(calls)==before
    saved=session.get(SceneState,room['id'])
    assert saved.summary=='DERIVED ARC MUST STAY STORED' and saved.compression_revision==7
    assert saved.last_compression_source_message_id==rows[1].id


def test_full_history_complete_prefix_beyond_legacy_tail_excludes_later_input(client,session,monkeypatch):
    options(monkeypatch,'native','full_history')
    monkeypatch.setenv('CHAT_RESPONSE_PROTOCOL','tagged_text');get_settings.cache_clear()
    room,char,rows=seed(client,session)
    start=datetime.now(timezone.utc)-timedelta(hours=1)
    extra=[]
    for i in range(30):
        message=Message(id=f'full-prefix-{i}',conversation_id=room['id'],speaker_type='user',speaker_id='u',
            content=f'Unique chronological public fact {i}',created_at=start+timedelta(seconds=i))
        session.add(message);extra.append(message)
    session.commit()
    monkeypatch.setattr(api,'_start_message_generation_worker',lambda *a:None)
    queued=client.post(f"/conversations/{room['id']}/messages/jobs",json={'speaker_type':'user','speaker_id':'u','content':'SELECTED SOURCE'}).json()
    session.add(Message(id='later-input',conversation_id=room['id'],speaker_type='user',speaker_id='u',content='LATER MUST NOT LEAK'))
    session.commit()
    captured=[]
    async def chat(self,messages,**kwargs):
        captured.append(messages)
        return LLMResponse(content='<m c="C1">Answer selected source.</m>',finish_reason='STOP')
    monkeypatch.setattr('app.engine.llm_client.LLMClient.chat',chat)
    asyncio.run(api._run_message_generation_job_in_session(session,queued['job']['id']))
    session.expire_all()
    snap=session.exec(select(LLMPromptSnapshot).where(LLMPromptSnapshot.source_message_id==queued['incoming_message']['id'])).one()
    assert snap.metadata_['history_message_ids']==[m.id for m in rows+extra]
    assert len(captured)==1
    wire='\n'.join(m['content'] for m in captured[0])
    assert 'LATER MUST NOT LEAK' not in wire and wire.count('SELECTED SOURCE')==1
    offsets=[wire.index(m.content) for m in rows+extra]
    assert offsets==sorted(offsets)
    assert session.get(Message,'later-input').content=='LATER MUST NOT LEAK'


def test_native_real_pressure_keeps_interleaved_multibubble_job_indivisible(client,session,monkeypatch):
    from dataclasses import replace
    from app.services.context_management_service import resolve_safe_model_context_capacity
    options(monkeypatch)
    monkeypatch.setenv('CHAT_RESPONSE_PROTOCOL','tagged_text');get_settings.cache_clear()
    room,char,rows=seed(client,session)
    scene=session.get(SceneState,room['id']);scene.last_compression_source_message_id=None;session.add(scene)
    session.add(MessageGenerationJob(id='historical-job',conversation_id=room['id'],incoming_message_id=rows[0].id,
        status='completed',generated_message_ids=[rows[1].id,rows[3].id]))
    session.commit()
    monkeypatch.setattr(api,'_start_message_generation_worker',lambda *a:None)
    queued=client.post(f"/conversations/{room['id']}/messages/jobs",json={'speaker_type':'user','speaker_id':'u','content':'SOURCE'}).json()
    preview=client.get(f"/conversations/{room['id']}/context-preview").json()
    cap=replace(resolve_safe_model_context_capacity(None),working_input_tokens=preview['projected_input_tokens']-1)
    monkeypatch.setattr(api,'resolve_safe_model_context_capacity',lambda *a,**k:cap)
    plans=[];original=conversation_service.automatic_context_plan
    def plan(*a,**k):
        value=original(*a,**k);plans.append(value);return value
    monkeypatch.setattr(conversation_service,'automatic_context_plan',plan)
    async def noop(*a,**k):
        return session.get(SceneState,room['id'])
    monkeypatch.setattr(conversation_service,'update_scene_orchestration_summary',noop)
    asyncio.run(api._run_message_generation_job_in_session(session,queued['job']['id']))
    assert plans and plans[0].pressure.total_group_count==2
    assert plans[0].fold_message_ids==tuple(m.id for m in rows)


def test_full_native_exact_capacity_accepts_same_preview_request(client,session,monkeypatch):
    from dataclasses import replace
    from app.services.context_management_service import resolve_safe_model_context_capacity
    options(monkeypatch,'native','full_history')
    monkeypatch.setenv('CHAT_RESPONSE_PROTOCOL','tagged_text');get_settings.cache_clear()
    room,char,rows=seed(client,session)
    monkeypatch.setattr(api,'_start_message_generation_worker',lambda *a:None)
    queued=client.post(f"/conversations/{room['id']}/messages/jobs",json={'speaker_type':'user','speaker_id':'u','content':'EXACT CAPACITY SOURCE'}).json()
    preview=client.get(f"/conversations/{room['id']}/context-preview").json()
    threshold=preview['projected_input_tokens']
    cap=replace(resolve_safe_model_context_capacity(None),working_input_tokens=threshold)
    monkeypatch.setattr(api,'resolve_safe_model_context_capacity',lambda *a,**k:cap)
    captured=[]
    async def chat(self,messages,**kwargs):
        captured.append(messages);return LLMResponse(content='<m c="C1">Exactly fits.</m>',finish_reason='STOP')
    monkeypatch.setattr('app.engine.llm_client.LLMClient.chat',chat)
    asyncio.run(api._run_message_generation_job_in_session(session,queued['job']['id']))
    status=client.get(f"/conversations/{room['id']}/generation-jobs/{queued['job']['id']}").json()
    assert status['status']=='completed' and len(captured)==1
    session.expire_all()
    snap=session.exec(select(LLMPromptSnapshot).where(LLMPromptSnapshot.source_message_id==queued['incoming_message']['id'])).one()
    assert snap.metadata_['projected_input_tokens']==threshold==snap.metadata_['working_input_tokens']


def test_actual_full_preview_and_job_clamp_to_reachable_fallback_without_changing_output(client,session,monkeypatch):
    from types import SimpleNamespace
    from app.db.models import ModelOption
    from app.services import runtime_settings_service, model_provider_service
    from app.engine.llm_client import LLMClient
    options(monkeypatch,'native','full_history',100000)
    monkeypatch.setenv('CHAT_RESPONSE_PROTOCOL','tagged_text');get_settings.cache_clear()
    room,char,rows=seed(client,session)
    setting=SimpleNamespace(model_key='wide',fallback_model_key='narrow',compression_model_key=None,
        response_length_preset='medium',compression_strategy='quality',compression_interval_turns=5)
    monkeypatch.setattr(runtime_settings_service,'get_effective_setting',lambda *a:setting)
    monkeypatch.setattr(runtime_settings_service,'llm_overrides_for_setting',lambda *a,**k:{'provider':'mock','model':'wide'})
    monkeypatch.setattr(runtime_settings_service,'fallback_llm_overrides_for_setting',lambda *a,**k:{'provider':'mock','model':'narrow'})
    output=LLMClient(profile='chat',overrides={'min_output_tokens':runtime_settings_service.preset_token_target('medium')}).max_output_tokens()
    wide=ModelOption(key='wide',context_window_tokens=1000000,max_output_tokens=1000)
    narrow=ModelOption(key='narrow',context_window_tokens=output+1024+1000,max_output_tokens=1000)
    monkeypatch.setattr(model_provider_service,'option_by_key',lambda db,key:wide if key=='wide' else narrow)
    monkeypatch.setattr(api,'_start_message_generation_worker',lambda *a:None)
    queued=client.post(f"/conversations/{room['id']}/messages/jobs",json={'speaker_type':'user','speaker_id':'u','content':'NARROW SOURCE'}).json()
    preview=client.get(f"/conversations/{room['id']}/context-preview").json()
    assert preview['working_input_tokens']==1000
    assert preview['completion_reserve_tokens']==output
    calls=[]
    async def forbidden(*a,**k):
        calls.append(1);raise AssertionError('narrow fallback capacity must reject before all providers')
    monkeypatch.setattr(LLMClient,'chat',forbidden)
    monkeypatch.setattr(conversation_service,'update_scene_orchestration_summary',forbidden)
    asyncio.run(api._run_message_generation_job_in_session(session,queued['job']['id']))
    status=client.get(f"/conversations/{room['id']}/generation-jobs/{queued['job']['id']}").json()
    assert status['status']=='failed' and status['error_code']=='context_maintenance_required'
    assert calls==[] and setting.compression_strategy=='quality'
