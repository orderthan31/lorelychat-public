"""Durable admission survives an ambiguous send and expired worker lease."""
import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from sqlmodel import select
from app.api import conversations as api
from app.db.models import CharacterAsset, MessageAsset, PostCommitTask
from app.services import conversation_service, decision_transport as transport
from test_asset_decision_post_commit import finalize


def enable(monkeypatch):
    monkeypatch.setattr(api.asset_decision_service, '_mock', lambda: False)
    monkeypatch.setattr(api.asset_decision_service, '_route', lambda *_: transport.DecisionRoute(
        provider_type='openai', model='requested-model', api_key='PRIVATE_KEY'))


def test_real_selector_applies_multiple_links_atomically(session, monkeypatch):
    _, asset, replies, task = finalize(session, monkeypatch)
    aid, mids = asset.id, [m.id for m in replies]
    session.add(CharacterAsset(id='second', character_id='decision-char', label='Other', image_url='x'))
    session.commit()
    enable(monkeypatch)
    calls = []
    async def decide(route, **kwargs):
        assert not session.in_transaction()
        calls.append(1)
        return {mids[0]: aid, mids[1]: 'second'}
    monkeypatch.setattr(transport, 'decide_assets', decide)
    claimed = conversation_service.claim_post_commit_task_by_unique_key(session, task.unique_key, lease_owner='worker')
    tid = task.id
    asyncio.run(api._execute_claimed_post_commit_task(session, claimed, lease_owner='worker'))
    session.expire_all()
    assert {(a.message_id, a.asset_id) for a in session.exec(select(MessageAsset)).all()} == {(mids[0], aid), (mids[1], 'second')}
    completed = session.get(PostCommitTask, tid)
    assert completed.status == 'completed'
    assert completed.payload_['decision_send']['state'] == 'send_may_have_started'
    diag = completed.payload_['decision_diagnostics']
    assert diag['requested_model'] == 'requested-model'
    assert diag['outcome'] == 'success' and diag['attached_count'] == 2
    assert 'PRIVATE_KEY' not in str(diag) and '/uploads/' not in str(diag)
    assert conversation_service.claim_post_commit_task_by_unique_key(session, task.unique_key, lease_owner='again') is None
    assert calls == [1]


def test_expired_lease_after_send_marker_never_resends(session, monkeypatch):
    _, _, _, task = finalize(session, monkeypatch)
    tid, unique = task.id, task.unique_key
    enable(monkeypatch)
    calls = []
    async def interrupted(*args, **kwargs):
        assert not session.in_transaction()
        calls.append(1)
        raise asyncio.CancelledError()
    monkeypatch.setattr(transport, 'decide_assets', interrupted)
    claimed = conversation_service.claim_post_commit_task_by_unique_key(session, unique, lease_owner='old')
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(api._execute_claimed_post_commit_task(session, claimed, lease_owner='old'))
    session.rollback()
    session.expire_all()
    task = session.get(PostCommitTask, tid)
    assert task.payload_['decision_send']['state'] == 'send_may_have_started'
    task.lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    session.commit()
    assert conversation_service.requeue_expired_post_commit_tasks(session) == 1
    claimed = conversation_service.claim_post_commit_task_by_unique_key(session, unique, lease_owner='recovered')
    assert claimed is not None and claimed.attempt_count == 2
    asyncio.run(api._execute_claimed_post_commit_task(session, claimed, lease_owner='recovered'))
    session.expire_all()
    task = session.get(PostCommitTask, tid)
    assert task.status == 'completed'
    assert task.payload_['decision_diagnostics']['outcome'] == 'send_recovery_abstention'
    assert calls == [1] and session.exec(select(MessageAsset)).all() == []
    assert task.payload_['decision_diagnostics']['requested_model'] == 'requested-model'


def test_markerless_recovered_legacy_attempt_abstains(session, monkeypatch):
    _, _, _, task = finalize(session, monkeypatch)
    unique = task.unique_key
    claimed = conversation_service.claim_post_commit_task_by_unique_key(session, unique, lease_owner='old')
    claimed.lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    session.commit()
    assert conversation_service.requeue_expired_post_commit_tasks(session) == 1
    claimed = conversation_service.claim_post_commit_task_by_unique_key(session, unique, lease_owner='new')
    monkeypatch.setattr(api.asset_decision_service, 'select_turn_assets', lambda *a, **k: pytest.fail('ambiguous replay must not plan/send'))
    asyncio.run(api._execute_claimed_post_commit_task(session, claimed, lease_owner='new'))
    session.expire_all()
    task = session.exec(select(PostCommitTask).where(PostCommitTask.unique_key == unique)).one()
    assert task.status == 'completed'
    assert task.payload_['decision_diagnostics']['outcome'] == 'send_recovery_abstention'


def test_diagnostics_drop_private_and_malformed_values():
    diag = api.asset_decision_service.bounded_diagnostics({
        'error': 'PRIVATE_ERROR', 'prompt': 'PRIVATE_PROMPT', 'url': 'https://private.invalid',
        'requested_model': '/root/private/file', 'requested_provider': ['PRIVATE_PROVIDER'],
        'outcome': {'private': 'PRIVATE_OUTCOME'}, 'preparation_status': [], 'request_bytes': True,
    })
    assert 'PRIVATE' not in str(diag) and '/root' not in str(diag) and 'https:' not in str(diag)
    assert diag['request_bytes'] == 0 and diag['outcome'] == 'none'
