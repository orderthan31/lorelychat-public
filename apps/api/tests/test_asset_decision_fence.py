"""Separate SQLite connections prove validation-to-attachment write fencing."""
import asyncio
from types import SimpleNamespace

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlmodel import SQLModel, Session, select

from app.api import conversations as api
from app.db.session import make_engine
from app.db.models import Character, CharacterAsset, Conversation, Message, MessageAsset, ModelOption, ModelProviderAccount, RuntimeSetting, PostCommitTask
from app.services import asset_decision_service as service, decision_transport


def seed(tmp_path, monkeypatch):
    engine = make_engine('sqlite:///' + str(tmp_path / 'decision-fence.db'))
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(service, 'get_settings', lambda: SimpleNamespace(llm_mock=False))
    monkeypatch.setattr(service.provider_secret_service, 'get_secret', lambda _: 'synthetic-key')
    async def decide(*args, **kwargs):
        return {'m1': 'a1'}
    monkeypatch.setattr(decision_transport, 'decide_assets', decide)
    with Session(engine) as session:
        session.add_all([
            Character(id='char', name='Synthetic', persona='Synthetic'),
            Conversation(id='room', mode='user_character'),
            ModelProviderAccount(id='account', provider_type='openai', alias='Synthetic', configured=True, api_key_secret_ref='fake'),
            Message(id='m1', conversation_id='room', speaker_type='character', speaker_id='char', content='Original scene'),
            CharacterAsset(id='a1', character_id='char', label='Synthetic', image_url='x', mood_tags=['happy']),
            CharacterAsset(id='a2', character_id='char', label='Manual', image_url='x', mood_tags=['happy']),
            ModelOption(id='option', key='decision', provider_account_id='account', provider_type='openai', model='synthetic', label='Synthetic', supports_decision=True),
            RuntimeSetting(id='global', decision_enabled=True, decision_model_option_key='decision'),
            PostCommitTask(id='task', unique_key='synthetic:asset_decision', task_type='asset_decision', status='processing', lease_owner='worker', attempt_count=1,
                           payload_={'conversation_id':'room','message_ids':['m1'],'decision_model_option_key':'decision'}),
        ])
        session.commit()
    return engine


def mutate(writer, change):
    if change == 'asset_disabled':
        writer.get(CharacterAsset, 'a1').enabled = False
    elif change == 'message_edited':
        writer.get(Message, 'm1').content = 'Edited unrelated scene'
    else:
        writer.add(MessageAsset(id='manual', message_id='m1', asset_id='a2'))
    writer.commit()


@pytest.mark.parametrize('change', ['asset_disabled', 'message_edited', 'manual_attachment'])
def test_competing_commit_before_application_fence_invalidates_result(tmp_path, monkeypatch, change):
    engine = seed(tmp_path, monkeypatch)
    original = service.select_turn_assets
    async def interleaved(reader, **kwargs):
        fence = kwargs['apply_fence']
        def before_fence():
            assert not reader.connection().connection.driver_connection.in_transaction
            with Session(engine) as writer:
                mutate(writer, change)
            return fence()
        kwargs['apply_fence'] = before_fence
        return await original(reader, **kwargs)
    monkeypatch.setattr(service, 'select_turn_assets', interleaved)
    with Session(engine) as reader:
        asyncio.run(api._execute_claimed_post_commit_task(reader, reader.get(PostCommitTask, 'task'), lease_owner='worker'))
        reader.expire_all()
        links = [(x.message_id, x.asset_id) for x in reader.exec(select(MessageAsset)).all()]
        assert ('m1', 'a1') not in links
        assert reader.get(PostCommitTask, 'task').status == 'completed'
        if change == 'manual_attachment':
            assert links == [('m1', 'a2')]
    engine.dispose()


@pytest.mark.parametrize('change', ['asset_disabled', 'message_edited', 'manual_attachment'])
def test_after_authoritative_validation_competing_writer_cannot_overtake_attachment(tmp_path, monkeypatch, change):
    engine = seed(tmp_path, monkeypatch)
    original = service.select_turn_assets
    blocked = []
    async def interleaved(reader, **kwargs):
        proposed = await original(reader, **kwargs)
        assert proposed == [('m1', 'a1')]
        assert reader.connection().connection.driver_connection.in_transaction is True
        with Session(engine) as writer:
            writer.exec(text('PRAGMA busy_timeout=10'))
            with pytest.raises(OperationalError, match='locked'):
                mutate(writer, change)
            writer.rollback()
            blocked.append(change)
        return proposed
    monkeypatch.setattr(service, 'select_turn_assets', interleaved)
    with Session(engine) as reader:
        asyncio.run(api._execute_claimed_post_commit_task(reader, reader.get(PostCommitTask, 'task'), lease_owner='worker'))
        reader.expire_all()
        assert [(x.message_id, x.asset_id) for x in reader.exec(select(MessageAsset)).all()] == [('m1', 'a1')]
        assert reader.get(PostCommitTask, 'task').status == 'completed'
        assert reader.get(CharacterAsset, 'a1').enabled is True
        assert reader.get(Message, 'm1').content == 'Original scene'
    assert blocked == [change]
    engine.dispose()
