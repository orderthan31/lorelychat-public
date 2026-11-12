"""Every generated turn is eligible; abstention is not a cooldown."""
import asyncio
from datetime import datetime, timedelta, timezone

from sqlmodel import select
from app.api import conversations as api
from app.db.models import Character, CharacterAsset, Conversation, Message, MessageAsset, MessageGenerationJob, PostCommitTask
from app.services import asset_decision_service as decisions, conversation_service


def test_none_result_allows_consecutive_generated_turn_checks(session, monkeypatch):
    setting = api.get_settings().model_copy(update={'llm_mock': False})
    monkeypatch.setattr(api, 'get_settings', lambda: setting)
    monkeypatch.setattr(decisions, 'decision_model_key_for_turn', lambda *_: 'decision')
    session.add_all([Character(id='c', name='Synthetic', persona='Synthetic'), Conversation(id='room', mode='user_character')])
    session.commit()
    calls = []
    async def none_result(_session, **kwargs):
        calls.append(kwargs['message_ids'])
        return []
    monkeypatch.setattr(decisions, 'select_turn_assets', none_result)
    checked = []
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for index in range(7):
        source = Message(id=f'source_{index}', conversation_id='room', speaker_type='user', speaker_id='u', content='Ordinary exchange', created_at=base+timedelta(seconds=index*10))
        job = MessageGenerationJob(id=f'job_{index}', conversation_id='room', incoming_message_id=source.id, status='running', lease_owner='worker')
        session.add_all([source, job]); session.commit()
        replies = [Message(id=f'reply_{index}_{bubble}', conversation_id='room', speaker_type='character', speaker_id='c', content='Routine reply', created_at=base+timedelta(seconds=index*10+bubble+1)) for bubble in range(3)]
        specs = api.build_generated_turn_post_commit_tasks(session=session, conversation=session.get(Conversation,'room'), scene_state=None, incoming_message=source, generated_messages=replies, enqueue_compression=False, character_ids=['c'], generation_job_id=job.id)
        if specs:
            checked.append(index)
            assert specs[0]['payload']['message_ids'] == [m.id for m in replies]
        conversation_service.finalize_message_generation_turn(session, job, replies, post_commit_tasks=specs)
        for spec in specs:
            claimed = conversation_service.claim_post_commit_task_by_unique_key(session, spec['unique_key'], lease_owner='asset-worker')
            asyncio.run(api._execute_claimed_post_commit_task(session, claimed, lease_owner='asset-worker'))
    assert checked == list(range(7))
    assert len(calls) == 7
    assert session.exec(select(MessageAsset)).all() == []
    assert all(task.status == 'completed' for task in session.exec(select(PostCommitTask)).all())


def test_recent_manual_image_does_not_suppress_entire_turn(session):
    session.add_all([
        Conversation(id='room', mode='user_character'),
        Message(id='recent', conversation_id='room', generation_job_id='prior', speaker_type='character', speaker_id='c', content='Prior'),
        MessageAsset(id='manual', message_id='recent', asset_id='a'),
    ]); session.commit()
    assert decisions.decision_turn_allowed(session, 'room') is True
    assert decisions.decision_turn_allowed(session, 'other-room') is True
