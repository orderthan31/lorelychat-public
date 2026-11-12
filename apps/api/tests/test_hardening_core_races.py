import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
import threading
import subprocess
import sys

import pytest
from fastapi import BackgroundTasks
from sqlmodel import SQLModel, Session, select
from app.db.models import Conversation, Message, MessageGenerationJob, PostCommitTask, GenerationAcceptanceReceipt
from app.db.session import make_engine
from app.services import conversation_service as svc
from app.api import conversations as api
from app.schemas.conversations import MessageCreate


def database(tmp_path):
    engine=make_engine('sqlite:///'+str(tmp_path/'race.db'));SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        s.add(Conversation(id='room',title='Synthetic race',mode='user_character'));s.commit()
    return engine


def test_concurrent_api_acceptance_prepares_once_and_commits_one_receipt(tmp_path, monkeypatch):
    engine=database(tmp_path);barrier=threading.Barrier(2);calls=[]
    prepare=api._prepare_message_payload_for_post
    def counted(*args):calls.append(1);return prepare(*args)
    monkeypatch.setattr(api,'_prepare_message_payload_for_post',counted)
    payload=MessageCreate(speaker_type='system',speaker_id='u',content='One atomic direction',metadata={'client_request_id':'concurrent'})
    request=SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(mock_execution=False)))
    def submit():
        with Session(engine) as s:
            barrier.wait(timeout=5)
            return asyncio.run(api.create_message_generation_job('room',payload,request,BackgroundTasks(),s)).job.id
    with ThreadPoolExecutor(max_workers=2) as pool:
        a,b=[f.result(timeout=10) for f in [pool.submit(submit),pool.submit(submit)]]
    assert a==b and calls==[1]
    with Session(engine) as s:
        assert len(s.exec(select(Message)).all())==1
        assert len(s.exec(select(MessageGenerationJob)).all())==1
        assert len(s.exec(select(GenerationAcceptanceReceipt)).all())==1


def test_wake_crash_after_commit_replays_exact_acceptance(tmp_path, monkeypatch):
    engine=database(tmp_path)
    def crash(*args):raise RuntimeError('synthetic after-commit crash')
    monkeypatch.setattr(api,'_start_message_generation_worker',crash)
    request=SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(mock_execution=False)))
    payload=MessageCreate(speaker_type='user',speaker_id='u',content='Survives crash before wake',metadata={'client_request_id':'wake'})
    with Session(engine) as s:
        with pytest.raises(RuntimeError,match='after-commit'):
            asyncio.run(api.create_message_generation_job('room',payload,request,BackgroundTasks(),s))
    monkeypatch.setattr(api,'_start_message_generation_worker',lambda *a:None)
    with Session(engine) as s:
        result=asyncio.run(api.create_message_generation_job('room',payload,request,BackgroundTasks(),s))
        assert len(s.exec(select(Message)).all())==1
        assert s.exec(select(GenerationAcceptanceReceipt)).one().job_id==result.job.id


def test_old_attempt_same_dispatcher_owner_cannot_ack_task(session, engine):
    task=PostCommitTask(id='same_owner',unique_key='same_owner',task_type='asset',status='processing',lease_owner='owner',attempt_count=1)
    session.add(task);session.commit();session.refresh(task)
    with engine.begin() as c:c.exec_driver_sql("UPDATE post_commit_tasks SET attempt_count=2 WHERE id='same_owner'")
    assert not svc.mark_post_commit_task_completed(session,task,lease_owner='owner')


def test_cancel_commit_before_finalize_fences_output_and_preserves_source_metadata(tmp_path):
    engine=database(tmp_path)
    with Session(engine) as s:
        source=Message(id='source',conversation_id='room',speaker_type='user',speaker_id='u',content='Source',metadata_={'manual_edit':'new value'})
        job=MessageGenerationJob(id='cancel_race',conversation_id='room',incoming_message_id='source',status='running',lease_owner='owner')
        s.add_all([source,job]);s.commit();s.refresh(job)
        with Session(engine) as cancel:
            svc.request_message_generation_job_cancel(cancel,cancel.get(MessageGenerationJob,job.id))
        with pytest.raises(ValueError,match='fence'):
            svc.finalize_message_generation_turn(s,job,[Message(id='forbidden',conversation_id='room',speaker_type='character',speaker_id='c',content='Never commit')],source_message_id='source',source_metadata={'manual_edit':'stale value'})
        assert s.get(Message,'forbidden') is None
        assert s.get(Message,'source').metadata_['manual_edit']=='new value'


def test_content_free_deletion_receipt_is_distinct_from_corruption(session):
    job=MessageGenerationJob(id='deleted_core',conversation_id='room',incoming_message_id='source',status='running',lease_owner='owner')
    session.add(job);session.commit()
    rows=[Message(id=f'deleted_{n}',conversation_id='room',speaker_type='character',speaker_id='c',content=f'Unique prose {n}') for n in range(2)]
    svc.finalize_message_generation_turn(session,job,rows)
    svc.delete_message(session,rows[0])
    with pytest.raises(svc.GenerationReceiptDeleted):svc.finalize_message_generation_turn(session,job,rows)
    assert session.get(Message,'deleted_0') is None


@pytest.mark.parametrize('lane',['generation','outbox'])
def test_kill_then_restart_before_expiry_recovers_after_clock_advance(tmp_path, monkeypatch, lane):
    engine=database(tmp_path)
    with Session(engine) as s:
        s.add(MessageGenerationJob(id='orphan_job',conversation_id='room',incoming_message_id='source'))
        s.add(PostCommitTask(id='orphan_task',unique_key='orphan_task',task_type='asset'))
        s.commit()
    script="""import sys,time
from sqlmodel import Session
from app.db.session import make_engine
from app.services import conversation_service as s
engine=make_engine(sys.argv[1])
with Session(engine) as db:
 s.claim_next_message_generation_job(db,lease_owner='killed',lease_seconds=180)
 s.claim_next_post_commit_task(db,lease_owner='killed',lease_seconds=180)
print('CLAIMED',flush=True)
time.sleep(60)
"""
    process=subprocess.Popen([sys.executable,'-s','-B','-c',script,str(engine.url)],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    try:
        assert process.stdout.readline().strip()=='CLAIMED'
    finally:
        process.kill();process.communicate(timeout=5)
    first=threading.Event();advance=threading.Event();done=threading.Event();runs=[]
    if lane=='generation':
        from app.services.generation_dispatcher import GenerationDispatcher as Dispatcher
        name='requeue_expired_message_generation_jobs'
    else:
        from app.services.post_commit_dispatcher import PostCommitDispatcher as Dispatcher
        name='requeue_expired_post_commit_tasks'
    recover=getattr(svc,name)
    def recovery(s,**kw):
        result=recover(s,now=datetime.now(timezone.utc)+(timedelta(minutes=4) if advance.is_set() else timedelta()))
        first.set();return result
    monkeypatch.setattr(svc,name,recovery)
    claim_name='claim_next_message_generation_job' if lane=='generation' else 'claim_next_post_commit_task'
    claim=getattr(svc,claim_name)
    def timed_claim(s,**kw):
        return claim(s,**kw,now=datetime.now(timezone.utc)+(timedelta(minutes=4) if advance.is_set() else timedelta()))
    monkeypatch.setattr(svc,claim_name,timed_claim)
    def run(id,owner):
        with Session(engine) as s:
            runs.append(id)
            if lane=='generation':
                svc.finalize_message_generation_turn(s,s.get(MessageGenerationJob,id),[Message(id='recovered_reply',conversation_id='room',speaker_type='character',speaker_id='c',content='Recovered exactly once')])
            else:assert svc.mark_post_commit_task_completed(s,s.get(PostCommitTask,id),lease_owner=owner)
        done.set()
    dispatcher=Dispatcher(engine,poll_interval_seconds=.05)
    dispatcher.start(run)
    try:
        assert first.wait(3)
        assert not done.is_set(), 'fresh unexpired orphan must not be stolen'
        advance.set();dispatcher.wake()
        assert done.wait(5), 'normal polling must converge without another restart'
    finally:assert dispatcher.stop(timeout_seconds=3)
    assert runs==['orphan_job' if lane=='generation' else 'orphan_task']


def test_real_sqlite_busy_timeout_does_not_kill_supervision(tmp_path, monkeypatch):
    from app.services.generation_dispatcher import GenerationDispatcher
    engine=database(tmp_path);failed=threading.Event();finished=threading.Event()
    with Session(engine) as s:s.add(MessageGenerationJob(id='busy_sqlite',conversation_id='room',incoming_message_id='source'));s.commit()
    claim=svc.claim_next_message_generation_job
    def observe(*args,**kwargs):
        from sqlalchemy.exc import OperationalError
        try:return claim(*args,**kwargs)
        except OperationalError:
            failed.set();raise
    monkeypatch.setattr(svc,'claim_next_message_generation_job',observe)
    def runner(jid,owner):
        with Session(engine) as s:
            svc.finalize_message_generation_turn(s,s.get(MessageGenerationJob,jid),[Message(id='busy_reply',conversation_id='room',speaker_type='character',speaker_id='c',content='Resumed after a real SQLite writer lock')])
        finished.set()
    dispatcher=GenerationDispatcher(engine,poll_interval_seconds=.05)
    with engine.connect() as lock:
        lock.exec_driver_sql('BEGIN IMMEDIATE')
        dispatcher.start(runner)
        try:
            assert failed.wait(8), 'must exercise the configured 5000ms SQLite busy timeout'
            assert dispatcher.is_running and not dispatcher.is_healthy
            lock.rollback();dispatcher.wake()
            assert finished.wait(5)
        finally:
            lock.rollback();assert dispatcher.stop(timeout_seconds=3)
    with Session(engine) as s:assert s.get(MessageGenerationJob,'busy_sqlite').status=='completed'


def test_stale_compression_outbox_owner_cannot_apply_arc(tmp_path, monkeypatch):
    engine=database(tmp_path)
    from app.db.models import SceneState
    with Session(engine) as s:
        s.add(SceneState(conversation_id='room',summary='Prior Arc'))
        for n in range(14):s.add(Message(id=f'compression_{n:02}',conversation_id='room',speaker_type='character',speaker_id='c',content=f'Unique evidence {n}'))
        task=PostCommitTask(id='compression_task',unique_key='compression_task',task_type='compression',status='processing',lease_owner='old',attempt_count=1,payload_={'conversation_id':'room'})
        s.add(task);s.commit()
        async def summarize(**kw):
            with Session(engine) as other:
                current=other.get(PostCommitTask,'compression_task');current.lease_owner='new';current.attempt_count=2;other.add(current);other.commit()
            return {'scene':{'summary':'Forbidden stale owner Arc'}}
        async def compress(session,conversation_id,**kw):
            return await svc.update_scene_orchestration_summary(session,conversation_id,svc.list_messages(session,conversation_id),llm_client=object(),character_ids=['c'])
        monkeypatch.setattr(svc,'summarize_conversation_state_with_llm',summarize)
        monkeypatch.setattr(api,'_run_scene_compression_in_session',compress)
        with pytest.raises(RuntimeError,match='lease was lost'):
            asyncio.run(api._execute_claimed_post_commit_task(s,task,lease_owner='old'))
        s.expire_all()
        assert s.get(SceneState,'room').summary=='Prior Arc'
        assert s.get(PostCommitTask,'compression_task').lease_owner=='new'


def test_lifespan_timeout_keeps_real_singleton_lock_and_shared_budget(tmp_path, engine, monkeypatch):
    from types import SimpleNamespace
    import time
    from app import main
    from app.ops.sqlite_ops import acquire_runtime_lock, release_runtime_lock
    lock_path=tmp_path/'runtime.lock'
    monkeypatch.setenv('LORECHAT_RUNTIME_LOCK_PATH',str(lock_path))
    monkeypatch.setattr(main,'engine',engine)
    monkeypatch.setattr(main.app.state,'engine',engine,raising=False)
    monkeypatch.setattr(main,'get_settings',lambda:SimpleNamespace(llm_mock=False,dispatcher_shutdown_timeout_seconds=.05, upload_root=str(tmp_path/'uploads'), shared_upload_root=None))
    budgets=[]
    class Dispatcher:
        def start(self,*a):pass
        def request_stop(self):pass
        def stop(self,*,timeout_seconds):
            budgets.append(timeout_seconds)
            if len(budgets)==1:time.sleep(.02)
            return False
    monkeypatch.setattr(main,'generation_dispatcher',Dispatcher())
    monkeypatch.setattr(main,'post_commit_dispatcher',Dispatcher())
    async def execute():
        async with main.lifespan(main.app):pass
    asyncio.run(execute())
    try:
        assert len(budgets)==2 and budgets[1]<budgets[0]-.01
        with pytest.raises(RuntimeError,match='already owned'):acquire_runtime_lock(lock_path)
    finally:
        release_runtime_lock(main.app.state.undrained_runtime_lock)
        del main.app.state.undrained_runtime_lock


def test_dispatcher_stop_retains_active_future_until_drained(tmp_path):
    from app.services.generation_dispatcher import GenerationDispatcher
    engine=database(tmp_path);entered=threading.Event();release=threading.Event()
    with Session(engine) as s:s.add(MessageGenerationJob(id='drain',conversation_id='room',incoming_message_id='source'));s.commit()
    dispatcher=GenerationDispatcher(engine,poll_interval_seconds=.05)
    def run(*a):entered.set();assert release.wait(5)
    dispatcher.start(run)
    assert entered.wait(2)
    try:
        assert dispatcher.stop(timeout_seconds=.05) is False
        assert dispatcher._futures and not all(f.done() for f in dispatcher._futures)
        with pytest.raises(RuntimeError,match='drain'):dispatcher.start(run)
    finally:
        release.set();assert dispatcher.stop(timeout_seconds=3)
