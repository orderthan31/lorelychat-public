from datetime import datetime, timedelta, timezone

from sqlmodel import select

from app.db.models import LLMPromptSnapshot
from app.services import prompt_snapshot_service


def test_record_prompt_snapshot_stores_full_payload_and_ledger(session):
    snapshot = prompt_snapshot_service.record_prompt_snapshot(
        session,
        conversation_id="conv_prompt",
        source_message_id="msg_user_1",
        provider="gemini",
        model="gemini-3-flash-preview",
        purpose="chat_generation",
        compiled_text="SYSTEM BLOCK\nScene and cards",
        messages=[
            {"role": "system", "content": "SYSTEM BLOCK\nScene and cards"},
            {"role": "user", "content": "next beat"},
        ],
        ledger=[{"key": "scene", "included": True}],
        pipeline_steps=["route_sections", "prepare_messages", "generate"],
        used_tokens=123,
        prompt_tokens=111,
        completion_tokens=22,
        total_tokens=133,
        response_preview="{\"replies\": []}",
        metadata={"finish_reason": "STOP", "parse_status": "success"},
    )

    stored = session.get(LLMPromptSnapshot, snapshot.id)

    assert stored is not None
    assert stored.conversation_id == "conv_prompt"
    assert stored.source_message_id == "msg_user_1"
    assert stored.compiled_text == "SYSTEM BLOCK\nScene and cards"
    assert stored.messages_json[1]["content"] == "next beat"
    assert stored.ledger_json == [{"key": "scene", "included": True}]
    assert stored.pipeline_steps == ["route_sections", "prepare_messages", "generate"]
    assert stored.prompt_tokens == 111
    assert stored.metadata_ == {"finish_reason": "STOP", "parse_status": "success"}


def test_prune_prompt_snapshots_removes_only_rows_older_than_retention(session):
    old = LLMPromptSnapshot(
        id="snap_old",
        conversation_id="conv_prompt",
        purpose="chat_generation",
        compiled_text="old prompt",
        messages_json=[],
        ledger_json=[],
        pipeline_steps=[],
        created_at=datetime.now(timezone.utc) - timedelta(days=8),
    )
    fresh = LLMPromptSnapshot(
        id="snap_fresh",
        conversation_id="conv_prompt",
        purpose="chat_generation",
        compiled_text="fresh prompt",
        messages_json=[],
        ledger_json=[],
        pipeline_steps=[],
        created_at=datetime.now(timezone.utc) - timedelta(days=2),
    )
    session.add(old)
    session.add(fresh)
    session.commit()

    deleted = prompt_snapshot_service.prune_prompt_snapshots(session, retention_days=7)

    remaining_ids = {row.id for row in session.exec(select(LLMPromptSnapshot)).all()}
    assert deleted == 1
    assert remaining_ids == {"snap_fresh"}
