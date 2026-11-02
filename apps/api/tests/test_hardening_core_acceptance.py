import asyncio
from datetime import datetime, timezone

import pytest
from sqlalchemy import text
from sqlmodel import Session, select
from app.db.models import Message, MessageGenerationJob, GenerationJobEvent
from app.services import conversation_service as svc


def room(client):
    char = client.post('/characters', json={'name':'Synthetic Core', 'persona':'A distinct fictional guide.'}).json()
    result = client.post('/conversations', json={'mode':'user_character','title':'Core synthetic room','participants':[{'type':'user','id':'u','order_index':0},{'type':'character','id':char['id'],'order_index':1}]}).json()
    return result['id'], char['id']


@pytest.mark.parametrize('speaker', ['user','system','character'])
def test_all_source_idempotency_and_payload_conflict(client, session, speaker):
    cid, char = room(client)
    payload={'speaker_type':speaker,'speaker_id':char if speaker=='character' else 'u','content':'Durable source text','metadata':{'client_request_id':'same-key'}}
    a=client.post(f'/conversations/{cid}/messages/jobs',json=payload)
    b=client.post(f'/conversations/{cid}/messages/jobs',json=payload)
    assert a.status_code == b.status_code == 200
    assert a.json()['job']['id'] == b.json()['job']['id']
    conflict=client.post(f'/conversations/{cid}/messages/jobs',json={**payload,'content':'Different payload'})
    assert conflict.status_code == 409
    assert len(session.exec(select(Message).where(Message.conversation_id==cid)).all()) == 1


def test_job_insert_failure_rolls_back_source_and_system_effect(client, session):
    cid, _ = room(client)
    session.exec(text("CREATE TRIGGER fail_accept BEFORE INSERT ON message_generation_jobs BEGIN SELECT RAISE(ABORT, 'synthetic insert failure'); END"));session.commit()
    with pytest.raises(Exception, match='synthetic insert failure'):
        client.post(f'/conversations/{cid}/messages/jobs',json={'speaker_type':'system','speaker_id':'u','content':'Atomic system direction','metadata':{'client_request_id':'fault'}})
    session.expire_all()
    assert not session.exec(select(Message).where(Message.conversation_id==cid)).all()


def test_command_activation_and_source_roll_back_with_job_insert(client, session):
    from app.db.models import ChatCommand, Conversation
    cid,_=room(client)
    session.add(ChatCommand(id='core_command',name='corecmd',display_name='Core command',prompt='Keep the authored mode.',enabled=True));session.commit()
    session.exec(text("CREATE TRIGGER fail_command_accept BEFORE INSERT ON message_generation_jobs BEGIN SELECT RAISE(ABORT, 'command acceptance fault'); END"));session.commit()
    payload={'speaker_type':'user','speaker_id':'u','content':'!corecmd details','metadata':{'client_request_id':'command-once'}}
    with pytest.raises(Exception,match='command acceptance fault'):client.post(f'/conversations/{cid}/messages/jobs',json=payload)
    session.expire_all()
    assert session.get(Conversation,cid).active_command_ids==[]
    assert session.exec(select(Message).where(Message.conversation_id==cid)).all()==[]
    session.exec(text('DROP TRIGGER fail_command_accept'));session.commit()
    a=client.post(f'/conversations/{cid}/messages/jobs',json=payload)
    b=client.post(f'/conversations/{cid}/messages/jobs',json=payload)
    assert a.status_code==b.status_code==200 and a.json()['job']['id']==b.json()['job']['id']
    session.expire_all();assert session.get(Conversation,cid).active_command_ids==['core_command']


def test_battle_effect_fault_after_its_commit_rolls_back_entire_acceptance(client, session, monkeypatch):
    from app.api import conversations as api
    from app.db.models import BattleMatchRecord, ConversationParticipant
    cid,char_a=room(client)
    char_b=client.post('/characters',json={'name':'Second synthetic','persona':'A fictional rival.'}).json()['id']
    session.add(ConversationParticipant(conversation_id=cid,participant_type='character',participant_id=char_b,order_index=2));session.commit()
    apply=api.battle_ledger_service.apply_battle_control
    def crash(*args,**kw):
        result=apply(*args,**kw)
        raise RuntimeError('after battle helper commit')
    monkeypatch.setattr(api.battle_ledger_service,'apply_battle_control',crash)
    payload={'speaker_type':'user','speaker_id':'u','content':'Begin a synthetic match','metadata':{'client_request_id':'battle-once'},'battle_control':{'action':'start','participant_a_id':char_a,'participant_b_id':char_b}}
    with pytest.raises(RuntimeError,match='after battle helper'):client.post(f'/conversations/{cid}/messages/jobs',json=payload)
    session.expire_all()
    assert session.exec(select(Message).where(Message.conversation_id==cid)).all()==[]
    assert session.exec(select(BattleMatchRecord).where(BattleMatchRecord.conversation_id==cid)).all()==[]
    monkeypatch.setattr(api.battle_ledger_service,'apply_battle_control',apply)
    a=client.post(f'/conversations/{cid}/messages/jobs',json=payload)
    b=client.post(f'/conversations/{cid}/messages/jobs',json=payload)
    assert a.status_code==b.status_code==200 and a.json()['job']['id']==b.json()['job']['id']
    assert len(session.exec(select(BattleMatchRecord).where(BattleMatchRecord.conversation_id==cid)).all())==1


def test_terminal_retry_cas_and_readonly_active_discovery(client, session):
    cid,_=room(client)
    created=client.post(f'/conversations/{cid}/messages/jobs',json={'speaker_type':'user','speaker_id':'u','content':'Retry same source'}).json()
    jid=created['job']['id']
    cancelled=client.post(f'/conversations/{cid}/generation-jobs/{jid}/cancel').json()
    active=client.get(f'/conversations/{cid}/generation-jobs/active')
    assert active.status_code==200 and active.json()=={'jobs':[]}
    path=f'/conversations/{cid}/generation-jobs/{jid}/retry'
    assert client.post(path,json={'expected_state_version':-1}).status_code==422
    stale=client.post(path,json={'expected_state_version':0})
    assert stale.status_code==200 and stale.json()['status']=='cancelled'
    version=cancelled['state_version']
    a=client.post(path,json={'expected_state_version':version})
    b=client.post(path,json={'expected_state_version':version})
    assert a.status_code==b.status_code==200
    assert a.json()['status']==b.json()['status']=='queued'
    assert a.json()['state_version']==b.json()['state_version']==version+1
    assert client.get(f'/conversations/{cid}/generation-jobs/active').json()['jobs'][0]['id']==jid
    assert len(session.exec(select(Message).where(Message.conversation_id==cid)).all())==1


def test_mock_real_lifespan_route_reaches_terminal_without_workers(client, monkeypatch):
    from app.main import app
    from app.services.generation_dispatcher import generation_dispatcher
    monkeypatch.setattr(app.state,'mock_execution',True,raising=False)
    cid,_=room(client)
    created=client.post(f'/conversations/{cid}/messages/jobs',json={'speaker_type':'user','speaker_id':'u','content':'Continue this synthetic scene.'})
    assert created.status_code==200
    jid=created.json()['job']['id']
    fetched=client.get(f'/conversations/{cid}/generation-jobs/{jid}').json()
    assert fetched['status']=='completed', fetched
    assert fetched['generated_message_ids']
    assert not generation_dispatcher.is_running


def test_mock_executor_suppresses_external_tts_and_future_paid_tasks(client, session, monkeypatch):
    from app.main import app
    from app.api import conversations as api
    from app.db.models import Conversation, PostCommitTask
    monkeypatch.setattr(app.state,'mock_execution',True,raising=False)
    cid,_=room(client)
    saved=session.get(Conversation,cid);saved.tts_enabled=True;session.add(saved);session.commit()
    calls=[]
    monkeypatch.setattr(api.tts_service,'ensure_message_tts',lambda *a,**kw:calls.append(1))
    response=client.post(f'/conversations/{cid}/messages/jobs',json={'speaker_type':'user','speaker_id':'u','content':'No paid side effects in mock onboarding.'})
    assert response.status_code==200
    assert calls==[]
    assert not session.exec(select(PostCommitTask).where(PostCommitTask.task_type.in_(['tts','compression']))).all()


def test_mock_onboarding_does_not_claim_unrelated_preexisting_jobs(client, session, monkeypatch):
    from app.main import app
    monkeypatch.setattr(app.state,'mock_execution',True,raising=False)
    session.add(MessageGenerationJob(id='protected_old_queue',conversation_id='unrelated_old_room',incoming_message_id='protected_old_source'))
    session.commit()
    cid,_=room(client)
    response=client.post(f'/conversations/{cid}/messages/jobs',json={'speaker_type':'user','speaker_id':'u','content':'Only this mock request may run.'})
    assert response.status_code==200
    session.expire_all()
    assert session.get(MessageGenerationJob,'protected_old_queue').status=='queued'
    assert session.get(MessageGenerationJob,'protected_old_queue').attempt_count==0
    jid=response.json()['job']['id']
    assert client.get(f'/conversations/{cid}/generation-jobs/{jid}').json()['status']=='completed'


def test_legacy_send_is_durable_and_same_request_does_not_reexecute(client, session):
    cid,_=room(client)
    payload={'speaker_type':'user','speaker_id':'u','content':'One compatibility send','metadata':{'client_request_id':'legacy-stable'}}
    a=client.post(f'/conversations/{cid}/messages',json=payload)
    b=client.post(f'/conversations/{cid}/messages',json=payload)
    assert a.status_code==b.status_code==200
    assert [m['id'] for m in a.json()]==[m['id'] for m in b.json()]
    assert len(session.exec(select(MessageGenerationJob).where(MessageGenerationJob.conversation_id==cid)).all())==1


def test_regeneration_uses_durable_receipt_and_explicit_idempotency_key(client, session):
    cid,_=room(client)
    posted=client.post(f'/conversations/{cid}/messages',json={'speaker_type':'user','speaker_id':'u','content':'Base source'})
    assert posted.status_code==200
    source=posted.json()[0]['id']
    url=f'/conversations/{cid}/messages/{source}/regenerate'
    a=client.post(url,json={'mode':'after_message'},headers={'Idempotency-Key':'regen-stable'})
    b=client.post(url,json={'mode':'after_message'},headers={'Idempotency-Key':'regen-stable'})
    assert a.status_code==b.status_code==200
    assert [m['id'] for m in a.json()]==[m['id'] for m in b.json()]
    assert len(session.exec(select(MessageGenerationJob).where(MessageGenerationJob.conversation_id==cid)).all())==2


def test_legacy_orphan_beyond_recent_window_is_reconciled_without_duplicate_source(client, session):
    cid,_=room(client)
    from app.schemas.conversations import MessageCreate
    old=svc.add_message(session,cid,MessageCreate(speaker_type='system',speaker_id='u',content='Old source',metadata={'client_request_id':'old-request'}))
    source_id=old.id
    for n in range(105):
        session.add(Message(id=f'newer_{n}',conversation_id=cid,speaker_type='user',speaker_id='u',content=f'Newer unrelated input {n}'))
    session.commit()
    result=client.post(f'/conversations/{cid}/messages/jobs',json={'speaker_type':'system','speaker_id':'u','content':'Old source','metadata':{'client_request_id':'old-request'}})
    assert result.status_code==200 and result.json()['incoming_message']['id']==source_id
    assert len(session.exec(select(Message).where(Message.conversation_id==cid)).all())==106


def test_source_deletion_is_never_resurrected_by_request_replay_or_retry(client, session):
    cid,_=room(client)
    payload={'speaker_type':'user','speaker_id':'u','content':'Intentionally deleted source','metadata':{'client_request_id':'deleted-source'}}
    accepted=client.post(f'/conversations/{cid}/messages/jobs',json=payload).json()
    jid=accepted['job']['id'];sid=accepted['incoming_message']['id']
    cancelled=client.post(f'/conversations/{cid}/generation-jobs/{jid}/cancel').json()
    assert client.delete(f'/conversations/{cid}/messages/{sid}').status_code==204
    assert client.post(f'/conversations/{cid}/messages/jobs',json=payload).status_code==410
    assert client.post(f'/conversations/{cid}/generation-jobs/{jid}/retry',json={'expected_state_version':cancelled['state_version']}).status_code==410
    assert session.get(Message,sid) is None


def test_sse_after_explicit_retry_does_not_stop_at_previous_attempt_terminal(session):
    from app.api.conversations import stream_message_generation_job_events
    j=MessageGenerationJob(id='sse_retry',conversation_id='room',incoming_message_id='source',status='completed',state_version=4,generated_message_ids=['reply'])
    session.add(j)
    for status,version in [('failed',1),('queued',2),('running',3),('completed',4)]:
        session.add(GenerationJobEvent(job_id=j.id,conversation_id='room',status=status,state_version=version))
    session.commit()
    class Request:
        async def is_disconnected(self):return False
    response=stream_message_generation_job_events('room',j.id,Request(),after_event_id=0,last_event_id=None,heartbeat_seconds=1,session=session)
    async def collect():return ''.join([part async for part in response.body_iterator])
    body=asyncio.run(asyncio.wait_for(collect(),timeout=.8))
    assert 'event: failed' not in body
    assert 'event: completed' in body and '"state_version":4' in body


def test_terminal_sse_ahead_cursor_reconciles_without_waiting(session):
    from app.api.conversations import stream_message_generation_job_events
    j=MessageGenerationJob(id='sse_core',conversation_id='room',incoming_message_id='source',status='cancelled',state_version=4)
    session.add(j);session.commit()
    class Request:
        async def is_disconnected(self):return False
    response=stream_message_generation_job_events('room',j.id,Request(),after_event_id=999,last_event_id=None,heartbeat_seconds=1,session=session)
    async def collect():
        return ''.join([part async for part in response.body_iterator])
    body=asyncio.run(asyncio.wait_for(collect(),timeout=.8))
    assert 'event: cancelled' in body
    assert '"state_version":4' in body
