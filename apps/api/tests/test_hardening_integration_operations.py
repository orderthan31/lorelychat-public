"""Parent integration regressions; synthetic fixture DB only."""
import pytest
from pydantic import ValidationError
from sqlmodel import select
from app.core.config import Settings
from app.db.models import (Conversation,Message,MessageGenerationJob,GenerationAcceptanceReceipt,GenerationReplyDeletion,GenerationJobEvent,PostCommitTask)
from app.services import conversation_service

@pytest.mark.parametrize('name',['llm_task_deadline_seconds','dispatcher_shutdown_timeout_seconds','tts_timeout_seconds'])
@pytest.mark.parametrize('value',[None,0,-1])
def test_whole_task_and_shutdown_budgets_are_required_positive(name,value):
    with pytest.raises(ValidationError):
        Settings(_env_file=None,**{name:value})

def test_budget_defaults_remain_explicit_and_positive():
    settings=Settings(_env_file=None)
    assert (settings.llm_task_deadline_seconds,settings.dispatcher_shutdown_timeout_seconds,settings.tts_timeout_seconds)==(300.0,30.0,120.0)

def install_receipts(session):
    for suffix in ('a','b'):
        cid='parent-room-'+suffix;source='parent-source-'+suffix;reply='parent-reply-'+suffix;job='parent-job-'+suffix
        session.add(Conversation(id=cid,title='Synthetic '+suffix,mode='user_character'))
        session.add(Message(id=source,conversation_id=cid,speaker_type='user',speaker_id='user',content='Synthetic source '+suffix))
        session.add(Message(id=reply,conversation_id=cid,speaker_type='character',speaker_id='cast',content='Synthetic reply '+suffix,generation_job_id=job,reply_index=0))
        session.add(MessageGenerationJob(id=job,conversation_id=cid,incoming_message_id=source,status='completed',generated_message_ids=[reply]))
        session.add(GenerationAcceptanceReceipt(id='parent-accept-'+suffix,conversation_id=cid,client_request_id='send:parent-'+suffix,payload_sha256='0'*64,incoming_message_id=source,job_id=job))
        session.add(GenerationReplyDeletion(message_id='older-deleted-'+suffix,conversation_id=cid,job_id=job,reply_index=0))
        session.add(GenerationJobEvent(job_id=job,conversation_id=cid,status='completed',state_version=1))
        session.add(PostCommitTask(id='parent-task-'+suffix,unique_key='parent-task-'+suffix,task_type='tts',payload_={'conversation_id':cid,'job_id':job,'message_id':reply}))
    session.commit()

def test_explicit_whole_conversation_delete_purges_only_its_generation_records(session):
    install_receipts(session)
    assert conversation_service.delete_conversation(session,'parent-room-a') is True
    for model in (MessageGenerationJob,GenerationAcceptanceReceipt,GenerationReplyDeletion,GenerationJobEvent):
        rows=session.exec(select(model)).all()
        assert len(rows)==1,(model.__name__,len(rows))
        assert rows[0].conversation_id=='parent-room-b'
    tasks=session.exec(select(PostCommitTask)).all()
    assert [task.id for task in tasks]==['parent-task-b']
    assert session.get(Conversation,'parent-room-b') is not None
    assert session.get(Message,'parent-reply-b').content=='Synthetic reply b'

def test_ordinary_message_delete_retains_no_resurrection_receipts(session):
    install_receipts(session)
    conversation_service.delete_message(session,session.get(Message,'parent-source-a'))
    assert session.get(GenerationAcceptanceReceipt,'parent-accept-a') is not None
    conversation_service.delete_message(session,session.get(Message,'parent-reply-a'))
    marker=session.get(GenerationReplyDeletion,'parent-reply-a')
    assert marker is not None and marker.conversation_id=='parent-room-a'
    assert session.get(GenerationAcceptanceReceipt,'parent-accept-a') is not None


def test_message_cursor_is_total_order_when_timestamps_are_equal(session):
    from datetime import datetime, timezone
    stamp = datetime(2026, 1, 1, tzinfo=timezone.utc)
    session.add(Conversation(id='same-time-room', title='Synthetic ties', mode='user_character'))
    for index in range(1, 7):
        session.add(Message(id=f'tie-{index:02d}', conversation_id='same-time-room',
                            speaker_type='user' if index in (1, 4) else 'character',
                            speaker_id='synthetic', content=f'Synthetic {index}', created_at=stamp))
    session.commit()
    first = conversation_service.list_messages(session, 'same-time-room', limit=2)
    second = conversation_service.list_messages(session, 'same-time-room', limit=2, before_id=first[0].id)
    third = conversation_service.list_messages(session, 'same-time-room', limit=2, before_id=second[0].id) if second else []
    assert [row.id for row in third + second + first] == [f'tie-{index:02d}' for index in range(1, 7)]
    recent = conversation_service.list_messages(session, 'same-time-room', recent_turns=1)
    assert [row.id for row in recent] == ['tie-04', 'tie-05', 'tie-06']


def test_http_whole_conversation_delete_cleans_durable_identifiers(client, session):
    install_receipts(session)
    assert client.delete('/conversations/parent-room-a').status_code == 204
    assert session.exec(select(GenerationAcceptanceReceipt).where(GenerationAcceptanceReceipt.conversation_id=='parent-room-a')).all() == []
    assert session.get(Conversation, 'parent-room-b') is not None


@pytest.mark.parametrize('available', [False, True])
def test_compression_preview_capacity_uses_only_reachable_fallback(session, monkeypatch, available):
    from types import SimpleNamespace
    from app.services import context_preview_service as preview

    setting = SimpleNamespace(model_key='primary', fallback_model_key='stored-fallback',
                              response_length_preset='balanced')
    primary, fallback = object(), object()
    session.add(Conversation(id='preview-fallback-room', title='Synthetic', mode='user_character'))
    session.commit()
    monkeypatch.setattr(preview, 'get_settings', lambda: SimpleNamespace(context_management_mode='automatic'))
    monkeypatch.setattr(preview.runtime_settings_service, 'get_effective_setting', lambda *a: setting)
    # Capacity now reserves the actual selected client's reasoning-aware cap;
    # keep this fixture's intentionally opaque model-option objects isolated
    # from the real account/secret resolver.
    monkeypatch.setattr(preview.runtime_settings_service, 'llm_overrides_for_setting',
                        lambda *a, **kw: {'provider': 'mock', 'model': 'synthetic-primary'})
    monkeypatch.setattr(preview.model_provider_service, 'option_by_key',
                        lambda _session, key: primary if key == 'primary' else fallback)
    monkeypatch.setattr(preview.runtime_settings_service, 'fallback_llm_overrides_for_setting',
                        lambda *a, **kw: {'model': 'synthetic-fallback'} if available else None)
    captured = []

    class CapacityReached(Exception):
        pass

    def capture(primary_option, fallback_option, **kwargs):
        captured.append((primary_option, fallback_option))
        raise CapacityReached

    monkeypatch.setattr(preview, 'resolve_safe_model_context_capacity', capture)
    with pytest.raises(CapacityReached):
        preview.build_compression_preview(session, 'preview-fallback-room')
    assert captured == [(primary, fallback if available else None)]
