from datetime import datetime, timedelta, timezone
import threading

import pytest
from fastapi import HTTPException
from sqlalchemy.exc import OperationalError
from sqlmodel import Session, select

from app.db.models import Message, MessageGenerationJob, PostCommitTask
from app.services import conversation_service as svc


def job(session, id='job_core', **kw):
    item = MessageGenerationJob(id=id, conversation_id='core_room', incoming_message_id='core_source', **kw)
    session.add(item); session.commit()
    return item


def test_cancelled_orphan_converges_and_next_room_job_claims(session):
    now = datetime.now(timezone.utc)
    old = job(session, status='running', lease_owner='dead', lease_expires_at=now-timedelta(seconds=1), cancel_requested_at=now)
    nxt = job(session, id='next', created_at=now+timedelta(seconds=1))
    svc.requeue_expired_message_generation_jobs(session, now=now)
    session.refresh(old)
    assert old.status == 'cancelled'
    assert svc.claim_next_message_generation_job(session, lease_owner='new', now=now).id == nxt.id


def test_queue_fairness_not_limited_by_blocked_prefix(session):
    now = datetime.now(timezone.utc)
    job(session, id='busy', status='running', lease_owner='busy', lease_expires_at=now+timedelta(minutes=1))
    for n in range(105):
        job(session, id=f'blocked{n:03}', created_at=now+timedelta(seconds=n))
    other = MessageGenerationJob(id='independent', conversation_id='other', incoming_message_id='other_source', created_at=now+timedelta(seconds=200))
    session.add(other); session.commit()
    claimed = svc.claim_next_message_generation_job(session, lease_owner='new', now=now)
    assert claimed is not None and claimed.id == 'independent'


def test_stale_outbox_owner_cannot_acknowledge(session, engine):
    task = PostCommitTask(id='task_core', unique_key='core', task_type='asset', status='processing', lease_owner='old')
    session.add(task); session.commit(); session.refresh(task)
    # Simulate a second writer without refreshing the first identity map.
    with engine.begin() as conn:
        conn.exec_driver_sql("UPDATE post_commit_tasks SET lease_owner='new' WHERE id='task_core'")
    assert not svc.mark_post_commit_task_completed(session, task, lease_owner='old')
    session.expire_all()
    assert session.get(PostCommitTask, task.id).lease_owner == 'new'


def test_finalizer_rejects_lost_owner(session, engine):
    owned = job(session, status='running', lease_owner='old')
    session.refresh(owned)
    with engine.begin() as conn:
        conn.exec_driver_sql("UPDATE message_generation_jobs SET lease_owner='new' WHERE id='job_core'")
    reply = Message(id='reply_core', conversation_id='core_room', speaker_type='character', speaker_id='char', content='Synthetic unique reply')
    with pytest.raises(ValueError, match='ownership|fence'):
        svc.finalize_message_generation_turn(session, owned, [reply])
    assert session.get(Message, reply.id) is None


def test_replay_missing_reply_never_recreates_or_accepts_partial(session):
    owned = job(session, status='running', lease_owner='owner')
    replies = [Message(id=f'reply_{i}', conversation_id='core_room', speaker_type='character', speaker_id='char', content=f'Unique prose {i}') for i in range(2)]
    svc.finalize_message_generation_turn(session, owned, replies)
    session.delete(replies[0]); session.commit()
    with pytest.raises(ValueError, match='receipt'):
        svc.finalize_message_generation_turn(session, owned, [Message(id='replacement', conversation_id='core_room', speaker_type='character', speaker_id='char', content='Never resurrect')])
    assert session.get(Message, 'replacement') is None


def test_ready_rejects_dead_required_supervisors(session, monkeypatch):
    from app import main
    monkeypatch.setattr(main.app.state, 'ready', True, raising=False)
    monkeypatch.setattr(main.app.state, 'dispatchers_started', True, raising=False)
    with pytest.raises(HTTPException) as exc:
        main.ready(session)
    assert exc.value.status_code == 503


@pytest.mark.parametrize("lane", ["generation", "outbox"])
def test_supervisor_survives_one_claim_exception(engine, monkeypatch, lane):
    if lane == "generation":
        from app.services.generation_dispatcher import GenerationDispatcher as Dispatcher
        name = "claim_next_message_generation_job"
    else:
        from app.services.post_commit_dispatcher import PostCommitDispatcher as Dispatcher
        name = "claim_next_post_commit_task"
    dispatcher = Dispatcher(engine, poll_interval_seconds=.05)
    called = threading.Event()
    count = []
    def claim(*a, **kw):
        count.append(1)
        if len(count) == 1:
            raise OperationalError('synthetic', {}, Exception('busy'))
        called.set()
        return None
    monkeypatch.setattr(svc, name, claim)
    dispatcher.start(lambda *a: None)
    try:
        assert called.wait(2), 'supervisor died instead of retrying'
        assert dispatcher.is_running
    finally:
        dispatcher.stop()


@pytest.mark.parametrize('lane', ['generation','outbox'])
def test_heartbeat_exception_is_visible_and_recovers_with_owned_lease(engine, monkeypatch, lane):
    from types import SimpleNamespace
    if lane=='generation':
        from app.services.generation_dispatcher import GenerationDispatcher as Dispatcher
        name='heartbeat_message_generation_job'
    else:
        from app.services.post_commit_dispatcher import PostCommitDispatcher as Dispatcher
        name='heartbeat_post_commit_task'
    dispatcher=Dispatcher(engine,heartbeat_interval_seconds=.1)
    dispatcher._thread=SimpleNamespace(is_alive=lambda:True)
    stop=threading.Event();failed=threading.Event();allow=threading.Event();calls=[]
    def renew(*a,**kw):
        calls.append(1)
        if len(calls)==1:
            raise OperationalError('synthetic heartbeat',{},Exception('busy'))
        assert allow.wait(3)
        stop.set();return True
    monkeypatch.setattr(svc,name,renew)
    import importlib
    monkeypatch.setattr(importlib.import_module(Dispatcher.__module__).logger,'exception',lambda *a,**kw:failed.set())
    thread=threading.Thread(target=dispatcher._heartbeat_loop,args=('heartbeat_core',stop,'owner'))
    thread.start()
    try:
        assert failed.wait(2)
        # The second call blocks until we have observed degraded lease health.
        assert not dispatcher.is_healthy
        allow.set();thread.join(timeout=3)
        assert not thread.is_alive() and dispatcher.is_healthy
    finally:stop.set();allow.set();thread.join(timeout=3)


def test_mock_startup_resumes_only_explicitly_accepted_mock_jobs(engine, monkeypatch):
    from app import main
    from fastapi.testclient import TestClient
    from app.db.models import Character, Conversation, ConversationParticipant, SceneState
    progressed=threading.Event()
    with Session(engine) as s:
        s.add(Character(id='mock_character',name='Synthetic character',persona='A friendly fictional guide.'))
        s.add(Conversation(id='mock_resume_room',title='Resume only mock',mode='user_character'))
        s.add(ConversationParticipant(conversation_id='mock_resume_room',participant_type='character',participant_id='mock_character',order_index=0))
        s.add(SceneState(conversation_id='mock_resume_room'))
        s.add(Message(id='mock_resume_source',conversation_id='mock_resume_room',speaker_type='user',speaker_id='u',content='Resume this explicitly accepted mock request.'))
        s.add(MessageGenerationJob(id='mock_resume_job',conversation_id='mock_resume_room',incoming_message_id='mock_resume_source',execution_options={'execution_mode':'mock'}))
        s.add(MessageGenerationJob(id='untouched_old_live_job',conversation_id='old_room',incoming_message_id='old_source'))
        s.commit()
    run=main._run_mock_generation_queue
    async def observe(bind):
        await run(bind);progressed.set()
    monkeypatch.setattr(main,'_run_mock_generation_queue',observe)
    monkeypatch.setattr(main.app.state,'engine',engine,raising=False)
    monkeypatch.setattr(main.app.state,'mock_execution',True,raising=False)
    with TestClient(main.app):
        assert progressed.wait(3)
        with Session(engine) as s:
            assert s.get(MessageGenerationJob,'mock_resume_job').status=='completed'
            assert s.get(MessageGenerationJob,'untouched_old_live_job').status=='queued'
    assert main.app.state.mock_generation_task is None


def test_lifespan_uses_explicit_fixture_engine(engine, monkeypatch):
    from app import main
    from fastapi.testclient import TestClient
    observed = []
    real = main.init_db
    def init(target_engine=None):
        observed.append(target_engine)
        real(target_engine)
    monkeypatch.setattr(main, 'init_db', init)
    monkeypatch.setattr(main.app.state, 'engine', engine, raising=False)
    with TestClient(main.app):
        pass
    assert observed == [engine]
