"""Old persisted continuity tasks acknowledge without reviving retired writers."""
import asyncio

from sqlmodel import select

from app.db.models import CharacterMemory, Conversation, ConversationRelationshipState, PostCommitTask
from app.engine.llm_client import LLMClient
from app.services import conversation_job_service, external_memory_service
from app.services.post_commit_execution import execute_claimed_post_commit_task


def test_queued_legacy_continuity_ack_preserves_manual_memory_and_relationships(session, monkeypatch):
    room = Conversation(id='legacy-room', mode='user_character')
    relationship = ConversationRelationshipState(
        conversation_id=room.id, character_id='character',
        counterpart_type='user', counterpart_id='user_001', trust_level=3,
        current_dynamic='Existing authored relationship',
    )
    note = CharacterMemory(
        id='manual-note', conversation_id=room.id, character_id='character',
        memory_type='user_note', content='Keep this manual note unchanged.', importance=5,
    )
    session.add_all([room, relationship, note])
    task = conversation_job_service.enqueue_post_commit_task(
        session, unique_key='old-continuity', task_type='continuity',
        payload={'conversation_id': room.id, 'character_id': 'character',
                 'source_message_id': 'old-source', 'character_message_id': 'old-reply'},
    )
    session.commit()
    session.refresh(relationship)
    session.refresh(note)
    before = (relationship.model_dump(), note.model_dump())

    def forbidden(*args, **kwargs):
        raise AssertionError('Retired continuity must not invoke provider work')

    monkeypatch.setattr(LLMClient, 'chat', forbidden)
    monkeypatch.setattr(external_memory_service, 'get_provider', forbidden)
    claimed = conversation_job_service.claim_next_post_commit_task(session, lease_owner='legacy-worker')
    assert claimed.id == task.id
    asyncio.run(execute_claimed_post_commit_task(session, claimed, lease_owner='legacy-worker'))
    session.expire_all()
    assert session.get(PostCommitTask, task.id).status == 'completed'
    assert [row.model_dump() for row in session.exec(select(ConversationRelationshipState))] == [before[0]]
    assert [row.model_dump() for row in session.exec(select(CharacterMemory))] == [before[1]]
