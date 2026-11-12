"""Synthetic acceptance of opt-in task routing, atomicity and GET hydration."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import update
from sqlmodel import select

from app.api import conversations as api
from app.db.models import Character, CharacterAsset, Conversation, Message, MessageAsset, MessageGenerationJob, PostCommitTask
from app.services import conversation_service
from app.services.hardening_core_transactions import isolated_mock_execution


def setup_turn(session):
    character = Character(id="decision-char", name="Synthetic", persona="Synthetic")
    room = Conversation(id="decision-room", title="Synthetic", mode="user_character")
    source = Message(id="decision-source", conversation_id=room.id, speaker_type="user", speaker_id="u", content="Synthetic source")
    asset = CharacterAsset(id="decision-asset", character_id=character.id, label="Synthetic", image_url="/uploads/synthetic.png", mood_tags=["happy"])
    session.add_all([character, room, source, asset])
    session.commit()
    replies = [Message(id=f"decision-reply-{i}", conversation_id=room.id, speaker_type="character", speaker_id=character.id, content=f"Synthetic reply {i}", emotion="happy") for i in range(2)]
    return room, source, asset, replies


def build(session, room, source, replies):
    return api.build_generated_turn_post_commit_tasks(
        session=session, conversation=room, scene_state=None, incoming_message=source,
        generated_messages=replies, enqueue_compression=False, character_ids=["decision-char"],
        generation_job_id="decision-job",
    )


def allow_decision_builder(monkeypatch, key: str | None = "decision-option"):
    settings = api.get_settings().model_copy(update={"llm_mock": False})
    monkeypatch.setattr(api, "get_settings", lambda: settings)
    monkeypatch.setattr(api.asset_decision_service, "decision_model_key_for_turn", lambda *_: key)


def test_off_builder_preserves_legacy_seed_and_reservations(session, monkeypatch):
    room, source, asset, replies = setup_turn(session)
    allow_decision_builder(monkeypatch, key=None)
    calls = []
    def legacy(_session, **kwargs):
        calls.append((kwargs["selection_seed"], set(kwargs["excluded_asset_ids"])))
        return asset if len(calls) == 1 else None
    monkeypatch.setattr(api.asset_service, "select_asset_for_message", legacy)
    tasks = build(session, room, source, replies)
    assert calls == [(replies[0].id, set()), (replies[1].id, {asset.id})]
    assert [(task["task_type"], task["payload"]["asset_id"]) for task in tasks] == [("asset", asset.id)]


def test_on_builder_enqueues_one_identifier_only_task_without_provider_or_legacy_call(session, monkeypatch):
    room, source, _, replies = setup_turn(session)
    allow_decision_builder(monkeypatch)
    monkeypatch.setattr(api.asset_service, "select_asset_for_message", lambda *_a, **_k: pytest.fail("legacy called while valid decision enabled"))
    tasks = build(session, room, source, replies)
    assert tasks == [{"unique_key": "job:decision-job:asset_decision", "task_type": "asset_decision", "payload": {
        "conversation_id": room.id, "message_ids": [m.id for m in replies], "decision_model_option_key": "decision-option", "turn_id": "decision-job",
    }}]
    assert "Synthetic" not in str(tasks)
    assert session.exec(select(PostCommitTask)).all() == []


@pytest.mark.parametrize("isolated", [True, False])
def test_mock_builder_does_not_even_resolve_decision_route(session, monkeypatch, isolated):
    room, source, _, replies = setup_turn(session)
    if isolated:
        allow_decision_builder(monkeypatch)
    monkeypatch.setattr(api.asset_decision_service, "decision_model_key_for_turn", lambda *_: pytest.fail("mock must not resolve paid route"))
    token = isolated_mock_execution.set(isolated)
    try:
        assert all(task["task_type"] == "asset" for task in build(session, room, source, replies))
    finally:
        isolated_mock_execution.reset(token)


def finalize(session, monkeypatch):
    room, source, asset, replies = setup_turn(session)
    allow_decision_builder(monkeypatch)
    specs = build(session, room, source, replies)
    job = MessageGenerationJob(id="decision-job", conversation_id=room.id, incoming_message_id=source.id, status="running", lease_owner="primary")
    session.add(job)
    session.commit()
    persisted = conversation_service.finalize_message_generation_turn(session, job, replies, post_commit_tasks=specs)
    assert [message.content for message in persisted] == ["Synthetic reply 0", "Synthetic reply 1"]
    assert session.get(MessageGenerationJob, job.id).status == "completed"
    task = session.exec(select(PostCommitTask).where(PostCommitTask.task_type == "asset_decision")).one()
    return room, asset, persisted, task


def test_committed_primary_and_pending_get_then_worker_assets_readback(client, session, monkeypatch):
    room, asset, persisted, task = finalize(session, monkeypatch)
    cid, aid, mids, task_id = room.id, asset.id, [m.id for m in persisted], task.id
    pending = client.get(f"/conversations/{cid}/messages", params=[("message_ids", mid) for mid in mids])
    assert pending.status_code == 200
    assert {row["id"] for row in pending.json()} == set(mids)
    assert [row["asset_decision_pending"] for row in pending.json()] == [True, True]
    assert not any(row["assets"] for row in pending.json())
    calls = []
    async def select_turn(_session, **kwargs):
        calls.append(kwargs)
        _session.rollback()  # same clean I/O boundary as the production selector
        return [(mids[-1], aid)]
    monkeypatch.setattr(api.asset_decision_service, "select_turn_assets", select_turn)
    claimed = conversation_service.claim_post_commit_task_by_unique_key(session, task.unique_key, lease_owner="assets")
    asyncio.run(api._execute_claimed_post_commit_task(session, claimed, lease_owner="assets"))
    final = client.get(f"/conversations/{cid}/messages", params=[("message_ids", mid) for mid in mids]).json()
    assert len(calls) == 1
    assert not any(row["asset_decision_pending"] for row in final)
    assert final[1]["assets"][0]["id"] == aid
    assert final[0]["assets"] == []
    assert [row["content"] for row in final] == ["Synthetic reply 0", "Synthetic reply 1"]
    session.expire_all()
    assert session.get(PostCommitTask, task_id).status == "completed"
    # Completed replay cannot claim, call the provider, duplicate a link, or
    # alter the primary turn's durable receipt.
    assert conversation_service.claim_post_commit_task_by_unique_key(session, task.unique_key, lease_owner="again") is None
    assert len(session.exec(select(MessageAsset)).all()) == 1
    assert len(calls) == 1


def test_lost_decision_task_attempt_rolls_back_staged_asset_link(session, monkeypatch):
    _, asset, persisted, task = finalize(session, monkeypatch)
    aid, mid, tid, unique_key = asset.id, persisted[0].id, task.id, task.unique_key
    claimed = conversation_service.claim_post_commit_task_by_unique_key(session, unique_key, lease_owner="assets")
    async def superseded(_session, **_kwargs):
        _session.rollback()
        _session.exec(update(PostCommitTask).where(PostCommitTask.id == tid).values(lease_owner="new-worker", attempt_count=2))
        _session.commit()
        return [(mid, aid)]
    monkeypatch.setattr(api.asset_decision_service, "select_turn_assets", superseded)
    with pytest.raises(RuntimeError, match="lease was lost"):
        asyncio.run(api._execute_claimed_post_commit_task(session, claimed, lease_owner="assets"))
    session.expire_all()
    assert session.exec(select(MessageAsset)).all() == []
    assert session.get(MessageGenerationJob, "decision-job").status == "completed"
    assert session.get(PostCommitTask, tid).lease_owner == "new-worker"


def test_message_id_read_is_bounded_room_owned_and_nonmutating(client, session):
    room, source, _, replies = setup_turn(session)
    session.add_all(replies)
    session.add(Conversation(id="other-room", title="Other", mode="user_character"))
    session.add(Message(id="foreign-message", conversation_id="other-room", speaker_type="user", speaker_id="u", content="Other"))
    session.commit()
    response = client.get(f"/conversations/{room.id}/messages", params=[("message_ids", replies[0].id), ("message_ids", "foreign-message"), ("message_ids", "deleted-message")])
    assert response.status_code == 200
    assert [row["id"] for row in response.json()] == [replies[0].id]
    assert response.json()[0]["asset_decision_pending"] is False
    assert client.get(f"/conversations/{room.id}/messages", params=[("message_ids", str(i)) for i in range(101)]).status_code == 422
    assert session.exec(select(PostCommitTask)).all() == []
    assert session.get(Message, source.id).content == "Synthetic source"


def test_decision_task_is_supported_by_inline_postcommit_hydration(session, monkeypatch):
    _, asset, persisted, task = finalize(session, monkeypatch)
    aid, mid = asset.id, persisted[0].id
    calls = []
    async def selected(_session, **kwargs):
        calls.append(kwargs)
        _session.rollback()
        return [(mid, aid)]
    monkeypatch.setattr(api.asset_decision_service, "select_turn_assets", selected)
    specs = [{"unique_key": task.unique_key, "task_type": "asset_decision"}]
    asyncio.run(api._process_inline_post_commit_tasks(session.get_bind(), specs))
    session.expire_all()
    assert len(calls) == 1
    assert session.get(PostCommitTask, task.id).status == "completed"
    assert [(link.message_id, link.asset_id) for link in session.exec(select(MessageAsset)).all()] == [(mid, aid)]
