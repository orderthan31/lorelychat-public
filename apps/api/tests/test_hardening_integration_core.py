"""Shared-boundary integration, synthetic persistence only."""
import asyncio
from datetime import datetime, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlmodel import select

from app.db.models import (ChatCommand, Conversation, Message, MessageGenerationJob,
                           ModelOption, ModelProviderAccount, PostCommitTask, RuntimeSetting)
from app.services import runtime_settings_service as runtime
from app.services import conversation_service as conversations
from test_hardening_core_acceptance import room


def models(session):
    for name in ('primary', 'secondary'):
        session.add(ModelProviderAccount(id=name, provider_type='openai_compatible', alias=name,
            preset='custom', base_url='http://127.0.0.1:1234/v1'))
        session.add(ModelOption(id=name, key=name, provider_account_id=name,
            provider_type='openai_compatible', model=name, label=name))
    session.commit()


@pytest.mark.parametrize('scope', ['global', 'room'])
@pytest.mark.parametrize('failure', ['disabled', 'deleted', 'missing', 'orphan', 'account_deleted', 'secret_missing', 'unsupported', 'account_disabled'])
def test_unavailable_optional_fallback_preserves_primary_and_requested_keys(session, scope, failure):
    models(session)
    secondary=session.get(ModelOption, 'secondary')
    account=session.get(ModelProviderAccount, 'secondary')
    if failure=='disabled': secondary.enabled=False
    elif failure=='deleted': secondary.deleted_at=datetime.now(timezone.utc)
    elif failure=='missing': session.delete(secondary)
    elif failure=='orphan': session.delete(account)
    elif failure=='account_deleted': account.deleted_at=datetime.now(timezone.utc)
    elif failure=='secret_missing': account.base_url='https://example.invalid/v1'
    elif failure=='unsupported': secondary.supports_chat=secondary.supports_compression=False
    elif failure=='account_disabled': account.enabled=False
    session.add(RuntimeSetting(id=scope, conversation_id=None if scope=='global' else scope,
        model_key='primary', compression_model_key='primary', fallback_model_key='secondary',
        compression_fallback_model_key='secondary'))
    session.commit()
    setting=runtime.get_effective_setting(session, None if scope=='global' else scope)
    assert runtime.llm_overrides_for_setting(setting, session=session)['model']=='primary'
    assert runtime.compression_llm_overrides_for_setting(setting, session=session)['model']=='primary'
    assert runtime.fallback_llm_overrides_for_setting(setting, session=session) is None
    assert runtime.compression_fallback_llm_overrides_for_setting(setting, session=session) is None
    for options in (True, False):
        result=runtime.serialize_setting(setting, scope=scope, options=options, session=session)
        assert result.fallback_model_key==result.compression_fallback_model_key=='secondary'
        assert result.chat_fallback_status==result.compression_fallback_status=='unavailable'
        assert result.effective_compression_fallback_model_key is None
    session.expire_all()
    assert session.get(RuntimeSetting,scope).fallback_model_key=='secondary'


def test_available_inherited_and_absent_fallback_statuses(session):
    models(session)
    setting=RuntimeSetting(id='global', model_key='primary', compression_model_key='primary')
    session.add(setting);session.commit()
    result=runtime.serialize_setting(setting, scope='global', session=session)
    assert result.chat_fallback_status==result.compression_fallback_status=='not_configured'
    setting.fallback_model_key='secondary';session.add(setting);session.commit()
    result=runtime.serialize_setting(setting, scope='global', options=False, session=session)
    assert result.chat_fallback_status==result.compression_fallback_status=='available'
    assert result.effective_compression_fallback_model_key=='secondary'
    assert result.compression_fallback_source=='chat_fallback'
    assert runtime.compression_fallback_llm_overrides_for_setting(setting,session=session)['model']=='secondary'
    setting.compression_fallback_model_key='deleted-requested';session.add(setting);session.commit()
    assert runtime.compression_fallback_llm_overrides_for_setting(setting,session=session) is None
    assert runtime.serialize_setting(setting,scope='global',session=session).compression_fallback_status=='unavailable'


def test_primary_validation_stays_mandatory_and_overrides_propagate(session):
    models(session)
    setting=RuntimeSetting(id='global',model_key='primary',compression_model_key='primary')
    option=session.get(ModelOption,'primary');option.supports_json=False
    session.add(option);session.add(setting);session.commit()
    for resolver in (runtime.llm_overrides_for_setting,runtime.compression_llm_overrides_for_setting):
        value=resolver(setting,session=session)
        assert value['supports_json'] is False
        assert value['keyless_local'] is True
        assert value['provider_config_revision']==session.get(ModelProviderAccount,'primary').updated_at.isoformat()
    option.enabled=False;session.add(option);session.commit()
    for resolver in (runtime.llm_overrides_for_setting,runtime.compression_llm_overrides_for_setting):
        with pytest.raises(ValueError):resolver(setting,session=session)


def test_api_scopes_preserve_unavailable_fallback_without_blocking_settings(client,session):
    models(session)
    cid,_=room(client)
    for key in ('global',cid):
        session.add(RuntimeSetting(id=key,conversation_id=None if key=='global' else cid,
            model_key='primary',compression_model_key='primary',fallback_model_key='missing',compression_fallback_model_key='missing'))
    session.commit()
    for path in ('/runtime-settings/default',f'/runtime-settings/conversations/{cid}'):
        response=client.get(path)
        assert response.status_code==200,response.text
        value=response.json()
        assert value['model_key']=='primary'
        assert value['fallback_model_key']==value['compression_fallback_model_key']=='missing'
        assert value['chat_fallback_status']==value['compression_fallback_status']=='unavailable'
        assert client.patch(path,json={'response_length_preset':'short'}).status_code==200


def test_historical_default_json_support_and_local_revision_not_inferred_false(session):
    models(session)
    setting=RuntimeSetting(id='global',model_key='primary')
    session.add(setting);session.commit()
    before=runtime.llm_overrides_for_setting(setting,session=session)
    assert before['supports_json'] is True
    account=session.get(ModelProviderAccount,'primary')
    account.updated_at=datetime(2030,1,1,tzinfo=timezone.utc)
    session.add(account);session.commit()
    after=runtime.llm_overrides_for_setting(setting,session=session)
    assert after['provider_config_revision']!=before['provider_config_revision']


def test_nullable_command_context_does_not_rewrite_legacy_empty(session):
    omitted=ChatCommand(id='omitted',name='omitted',display_name='Omitted',prompt='Synthetic')
    explicit=ChatCommand(id='explicit',name='explicit',display_name='Explicit',prompt='Synthetic',
        postprocess_context_options=[],context_sources=[])
    session.add(omitted);session.add(explicit);session.commit();session.expire_all()
    assert session.get(ChatCommand,'omitted').context_sources is None
    assert session.get(ChatCommand,'explicit').context_sources==[]
    assert session.get(ChatCommand,'explicit').postprocess_context_options==[]
    assert session.exec(text("SELECT context_sources IS NULL FROM chat_commands WHERE id='omitted'")).one()[0]==1


def test_additive_context_migration_preserves_all_existing_bytes(tmp_path):
    from app.db.session import make_engine, migrate_sqlite_columns
    engine=make_engine('sqlite:///'+str(tmp_path/'commands.db'))
    with engine.begin() as conn:
        conn.exec_driver_sql("CREATE TABLE chat_commands (id TEXT PRIMARY KEY, prompt TEXT, generation_prompt TEXT, postprocess_prompt TEXT, postprocess_target TEXT, postprocess_probability INTEGER, postprocess_block_types JSON, postprocess_context_options JSON)")
        for key, value in [('empty','[]'),('authored','[ "world" ]'),('seed','["scene","turn_messages"]')]:
            conn.exec_driver_sql("INSERT INTO chat_commands VALUES (?, 'private synthetic', 'g', 'p', 'all_bubbles', 100, '[]', ?)",(key,value))
        before=conn.exec_driver_sql('SELECT * FROM chat_commands ORDER BY id').all()
    migrate_sqlite_columns(engine);migrate_sqlite_columns(engine)
    with engine.connect() as conn:
        after=conn.exec_driver_sql('SELECT * FROM chat_commands ORDER BY id').all()
        assert [tuple(row[:-1]) for row in after]==[tuple(row) for row in before]
        assert all(row[-1] is None for row in after)
    engine.dispose()


@pytest.mark.parametrize('status',['queued','running','completed','failed','cancelled'])
def test_readonly_receipt_lookup_all_states_and_missing(client,session,status):
    cid,_=room(client)
    created=client.post(f'/conversations/{cid}/messages/jobs',json={'speaker_type':'user','speaker_id':'u','content':'Synthetic source','metadata':{'client_request_id':'receipt-key'}}).json()
    job=session.get(MessageGenerationJob,created['job']['id']);job.status=status;session.add(job);session.commit()
    before=[tuple(row) for row in session.exec(text('SELECT id,status,state_version,attempt_count FROM message_generation_jobs')).all()]
    result=client.get(f'/conversations/{cid}/generation-jobs/by-request/receipt-key')
    assert result.status_code==200,result.text
    assert result.json()['job']['id']==created['job']['id']
    assert result.json()['job']['status']==status
    assert result.json()['incoming_message']['id']==created['incoming_message']['id']
    assert client.get(f'/conversations/{cid}/generation-jobs/by-request/unknown').status_code==404
    assert client.get(f'/conversations/{cid}/generation-jobs/by-request/'+('x'*201)).status_code==422
    assert client.get(f'/conversations/{cid}/generation-jobs/by-request/%20').status_code==422
    session.expire_all()
    assert before==[tuple(row) for row in session.exec(text('SELECT id,status,state_version,attempt_count FROM message_generation_jobs')).all()]
    assert len(session.exec(select(Message).where(Message.conversation_id==cid)).all())==1


def test_receipt_lookup_deleted_source_is_gone(client,session):
    cid,_=room(client)
    created=client.post(f'/conversations/{cid}/messages/jobs',json={'speaker_type':'user','speaker_id':'u','content':'Synthetic source','metadata':{'client_request_id':'gone-key'}}).json()
    assert client.delete(f"/conversations/{cid}/messages/{created['incoming_message']['id']}").status_code==204
    assert client.get(f'/conversations/{cid}/generation-jobs/by-request/gone-key').status_code==410
    assert not session.exec(select(Message).where(Message.conversation_id==cid)).all()


def test_aggregate_body_limit_is_installed_before_json_parse(client):
    from app.main import app
    from app.ops.request_limits import RequestBodyLimitMiddleware
    assert any(item.cls is RequestBodyLimitMiddleware for item in app.user_middleware)
    response=client.post('/characters',content=b'x'*(10*1024*1024+1),headers={'content-type':'application/json'})
    assert response.status_code==413


def test_optional_asset_failure_keeps_primary_and_other_tasks_atomic(client,session,monkeypatch,caplog):
    from app.api import conversations as api
    cid,char=room(client)
    conv=session.get(Conversation,cid);conv.tts_enabled=True;session.add(conv)
    job=MessageGenerationJob(id='asset-job',conversation_id=cid,incoming_message_id='source',status='running',lease_owner='test')
    session.add(job);session.commit()
    incoming=Message(id='source',conversation_id=cid,speaker_type='user',speaker_id='u',content='Synthetic source')
    reply=Message(id='validated-primary',conversation_id=cid,speaker_type='character',speaker_id=char,content='Validated paid prose')
    def fail(*args,**kwargs):raise RuntimeError('private sentinel must not be logged')
    monkeypatch.setattr(api.asset_service,'select_asset_for_message',fail)
    tasks=api.build_generated_turn_post_commit_tasks(session=session,conversation=conv,scene_state=None,
        incoming_message=incoming,generated_messages=[reply],enqueue_compression=True,character_ids=[char],generation_job_id=job.id)
    assert {task['task_type'] for task in tasks}=={'tts','compression'}
    assert 'private sentinel' not in caplog.text
    result=conversations.finalize_message_generation_turn(session,job,[reply],post_commit_tasks=tasks)
    assert [m.content for m in result]==['Validated paid prose']
    session.expire_all()
    assert session.get(MessageGenerationJob,'asset-job').status=='completed'
    assert {t.task_type for t in session.exec(select(PostCommitTask)).all()}=={'tts','compression'}


def test_asset_db_fault_is_not_swallowed(client,session,monkeypatch):
    from app.api import conversations as api
    cid,char=room(client)
    def fail(*args,**kwargs):raise OperationalError('synthetic query',{},Exception('synthetic DB fault'))
    monkeypatch.setattr(api.asset_service,'select_asset_for_message',fail)
    with pytest.raises(OperationalError):
        api.build_generated_turn_post_commit_tasks(session=session,conversation=session.get(Conversation,cid),scene_state=None,
            incoming_message=Message(id='source',conversation_id=cid,speaker_type='user',content='S'),
            generated_messages=[Message(id='reply',conversation_id=cid,speaker_type='character',speaker_id=char,content='R')],
            enqueue_compression=True,character_ids=[char],generation_job_id='job')


def test_nonmock_claim_never_claims_marked_mock_and_runner_never_sends(session,monkeypatch):
    from app.api import conversations as api
    session.add(MessageGenerationJob(id='mock-pending',conversation_id='room',incoming_message_id='source',execution_options={'execution_mode':'mock'}))
    session.commit()
    settings=api.get_settings().model_copy(update={'llm_mock':False})
    monkeypatch.setattr(api,'get_settings',lambda:settings)

    claimed=conversations.claim_next_message_generation_job(session,lease_owner='real')
    assert claimed is None
    calls=[]
    async def paid(*args,**kwargs):calls.append(1)
    monkeypatch.setattr(api,'generate_replies_from_message',paid)
    asyncio.run(api._run_message_generation_job_in_session(session,'mock-pending'))
    assert calls==[]
    session.expire_all()
    row=session.get(MessageGenerationJob,'mock-pending')
    assert row.status=='queued' and row.attempt_count==0


def test_mock_onboarding_legacy_send_still_executes_mock(client,monkeypatch):
    from app.main import app
    monkeypatch.setattr(app.state,'mock_execution',True,raising=False)
    cid,_=room(client)
    result=client.post(f'/conversations/{cid}/messages',json={'speaker_type':'user','speaker_id':'u','content':'Compatibility mock reply'})
    assert result.status_code==200,result.text
    assert any(message['speaker_type']=='character' for message in result.json())


def test_actual_nonmock_dispatcher_restart_skips_mock_but_runs_live_intended(engine,session):
    import threading
    from app.services.generation_dispatcher import GenerationDispatcher
    for identity,options in [('mock-restart',{'execution_mode':'mock'}),('live-restart',{})]:
        session.add(MessageGenerationJob(id=identity,conversation_id=identity,incoming_message_id=identity,execution_options=options))
    session.commit()
    ran=threading.Event();calls=[]
    def runner(job_id,owner):
        calls.append(job_id)
        ran.set()
    dispatcher=GenerationDispatcher(engine,poll_interval_seconds=.05)
    dispatcher.start(runner)
    try:
        assert ran.wait(3)
    finally:
        assert dispatcher.stop(timeout_seconds=3)
    assert calls==['live-restart']
    session.expire_all()
    assert session.get(MessageGenerationJob,'mock-restart').status=='queued'
    assert session.get(MessageGenerationJob,'mock-restart').attempt_count==0
