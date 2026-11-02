"""API-path regressions for durable regeneration execution-mode isolation."""
import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from sqlmodel import col, select

from app.api import conversations as api
from app.db.models import Conversation, GenerationAcceptanceReceipt, Message, MessageGenerationJob, PostCommitTask
from app.main import app
from app.services import conversation_service
from app.services.hardening_core_transactions import isolated_mock_execution


def seed(client, monkeypatch, session):
    monkeypatch.setattr(app.state, "mock_execution", True, raising=False)
    character = client.post("/characters", json={"name": "Synthetic guide", "persona": "A fictional guide."})
    assert character.status_code == 200, character.text
    room = client.post("/conversations", json={"mode": "user_character", "title": "Isolation fixture", "participants": [
        {"type": "user", "id": "u", "order_index": 0},
        {"type": "character", "id": character.json()["id"], "order_index": 1},
    ]})
    assert room.status_code == 200, room.text
    cid = room.json()["id"]
    response = client.post(f"/conversations/{cid}/messages", json={"speaker_type": "user", "speaker_id": "u", "content": "Continue this fictional scene."})
    assert response.status_code == 200, response.text
    return cid, response.json()[0]["id"]


def regenerate(client, cid, source, key="regeneration-isolation"):
    return client.post(f"/conversations/{cid}/messages/{source}/regenerate", json={"mode": "after_message"}, headers={"Idempotency-Key": key})


def receipt_job(session, cid, source, key="regeneration-isolation"):
    session.expire_all()
    receipts = session.exec(select(GenerationAcceptanceReceipt).where(
        GenerationAcceptanceReceipt.conversation_id == cid,
        GenerationAcceptanceReceipt.client_request_id == f"regeneration:{source}:{key}",
    )).all()
    assert len(receipts) == 1
    return receipts[0], session.get(MessageGenerationJob, receipts[0].job_id)


async def no_run(*args, **kwargs):
    pass


def accept_pending(client, session, monkeypatch, cid, source, *, mock=True):
    with monkeypatch.context() as patch:
        patch.setattr(app.state, "mock_execution", mock)
        patch.setattr(api, "_run_mock_generation_queue", no_run)
        patch.setattr(api, "_run_message_generation_job_in_session", no_run)
        patch.setattr(api, "_start_message_generation_worker", lambda *_: None)
        response = regenerate(client, cid, source)
    assert response.status_code == 409, response.text
    return receipt_job(session, cid, source)[1]


@pytest.mark.parametrize("default_flag", [False, True])
def test_actual_regeneration_suppresses_tts_and_compression_and_replays_receipt(client, session, monkeypatch, default_flag):
    cid, source = seed(client, monkeypatch, session)
    if default_flag:
        monkeypatch.delattr(app.state, "mock_execution")
    room = session.get(Conversation, cid)
    room.tts_enabled = True
    session.add(room)
    session.commit()
    # Force eligibility, not the enqueue policy: the real task builder and
    # finalizer must suppress BOTH paid auxiliary task kinds in mock context.
    monkeypatch.setattr(conversation_service, "should_update_scene_orchestration_summary", lambda *a, **k: True)
    calls = []
    async def forbidden(*args, **kwargs):
        calls.append("paid")
        raise AssertionError("paid execution reached")
    monkeypatch.setattr(api.tts_service, "ensure_message_tts", forbidden)
    monkeypatch.setattr(api, "_run_scene_compression_in_session", forbidden)
    for method in ("_chat_litellm", "_chat_openai_compatible", "_chat_gemini"):
        monkeypatch.setattr(api.LLMClient, method, forbidden)
    observations = []
    build = api.build_generated_turn_post_commit_tasks
    def observed_builder(**kwargs):
        tasks = build(**kwargs)
        observations.append((isolated_mock_execution.get(), kwargs["enqueue_compression"], kwargs["conversation"].tts_enabled, tasks))
        return tasks
    monkeypatch.setattr(api, "build_generated_turn_post_commit_tasks", observed_builder)
    first = regenerate(client, cid, source)
    assert first.status_code == 200, first.text
    assert first.json()
    receipt, job = receipt_job(session, cid, source)
    assert job.execution_options["execution_mode"] == "mock"
    assert job.status == "completed" and job.attempt_count == 1
    ids = [message["id"] for message in first.json()]
    assert job.generated_message_ids == ids
    assert observations and all(scope and compression and tts and not tasks for scope, compression, tts, tasks in observations)
    assert not session.exec(select(PostCommitTask).where(col(PostCommitTask.task_type).in_(["tts", "compression"]))).all()
    before = (receipt.id, job.id, job.state_version, len(session.exec(select(Message)).all()), len(session.exec(select(MessageGenerationJob)).all()))
    second = regenerate(client, cid, source)
    assert second.status_code == 200, second.text
    assert [message["id"] for message in second.json()] == ids
    receipt, job = receipt_job(session, cid, source)
    assert before == (receipt.id, job.id, job.state_version, len(session.exec(select(Message)).all()), len(session.exec(select(MessageGenerationJob)).all()))
    readback = client.get(f"/conversations/{cid}/generation-jobs/{job.id}")
    assert readback.status_code == 200 and readback.json()["generated_message_ids"] == ids
    assert len(observations) == 1 and calls == []
    assert isolated_mock_execution.get() is False


def test_pending_mock_acceptance_is_not_real_claimable_and_resumes_in_mock(client, session, monkeypatch):
    cid, source = seed(client, monkeypatch, session)
    job = accept_pending(client, session, monkeypatch, cid, source)
    assert job.execution_options.get("execution_mode") == "mock"
    assert conversation_service.claim_next_message_generation_job(session, lease_owner="real-restart", job_id=job.id) is None
    session.expire_all()
    assert session.get(MessageGenerationJob, job.id).attempt_count == 0
    response = regenerate(client, cid, source)
    assert response.status_code == 200, response.text
    assert receipt_job(session, cid, source)[1].status == "completed"


def test_mock_expired_lease_recovers_without_real_execution(client, session, monkeypatch):
    cid, source = seed(client, monkeypatch, session)
    job = accept_pending(client, session, monkeypatch, cid, source)
    job_id = job.id
    claimed = conversation_service.claim_next_message_generation_job(session, lease_owner="dead-mock", job_id=job_id, execution_mode="mock")
    assert claimed is not None
    claimed.lease_expires_at = datetime.now(timezone.utc) - timedelta(minutes=2)
    session.add(claimed)
    session.commit()
    conversation_service.requeue_expired_message_generation_jobs(session)
    assert conversation_service.claim_next_message_generation_job(session, lease_owner="real-restart", job_id=job_id) is None
    asyncio.run(api._run_mock_generation_queue(session.get_bind()))
    session.expire_all()
    recovered = session.get(MessageGenerationJob, job_id)
    assert recovered.status == "completed" and recovered.attempt_count == 2
    assert recovered.execution_options["execution_mode"] == "mock"


def test_pending_mock_is_held_when_settings_are_live(client, session, monkeypatch):
    cid, source = seed(client, monkeypatch, session)
    job = accept_pending(client, session, monkeypatch, cid, source)
    assert job.execution_options.get("execution_mode") == "mock"
    settings = api.get_settings().model_copy(update={"llm_mock": False})
    monkeypatch.setattr(api, "get_settings", lambda: settings)
    calls = []
    monkeypatch.setattr(api, "_start_message_generation_worker", lambda *a: calls.append("wake"))
    async def forbidden(*a, **k):
        calls.append("run")
    monkeypatch.setattr(api, "_run_message_generation_job_in_session", forbidden)
    monkeypatch.setattr(api, "_run_mock_generation_queue", forbidden)
    response = regenerate(client, cid, source)
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "mock_generation_held"
    assert calls == []
    held = receipt_job(session, cid, source)[1]
    assert held.status == "queued" and held.attempt_count == 0 and held.execution_options["execution_mode"] == "mock"


@pytest.mark.parametrize("status", ["queued", "running"])
def test_existing_live_intended_pending_regeneration_is_not_relabelled_or_run(client, session, monkeypatch, status):
    cid, source = seed(client, monkeypatch, session)
    job = accept_pending(client, session, monkeypatch, cid, source, mock=False)
    assert "execution_mode" not in job.execution_options
    job.status = status
    session.add(job)
    session.commit()
    before = dict(job.execution_options)
    calls = []
    monkeypatch.setattr(api, "_start_message_generation_worker", lambda *a: calls.append("wake"))
    async def forbidden(*a, **k):
        calls.append("run")
    monkeypatch.setattr(api, "_run_message_generation_job_in_session", forbidden)
    monkeypatch.setattr(api, "_run_mock_generation_queue", forbidden)
    response = regenerate(client, cid, source)
    assert response.status_code == 409, response.text
    assert "live-intended" in response.json()["detail"]
    assert calls == []
    persisted = receipt_job(session, cid, source)[1]
    assert persisted.execution_options == before and persisted.status == status and persisted.attempt_count == 0


def test_running_mock_is_pending_not_dispatched_again(client, session, monkeypatch):
    cid, source = seed(client, monkeypatch, session)
    job = accept_pending(client, session, monkeypatch, cid, source)
    claimed = conversation_service.claim_next_message_generation_job(session, lease_owner="other-mock", job_id=job.id, execution_mode="mock")
    assert claimed is not None
    calls = []
    async def forbidden(*a, **k):
        calls.append("run")
    monkeypatch.setattr(api, "_run_mock_generation_queue", forbidden)
    monkeypatch.setattr(api, "_run_message_generation_job_in_session", forbidden)
    response = regenerate(client, cid, source)
    assert response.status_code == 409 and response.json()["detail"]["code"] == "durable_generation_pending"
    assert calls == []
    persisted = receipt_job(session, cid, source)[1]
    assert persisted.status == "running" and persisted.lease_owner == "other-mock" and persisted.attempt_count == 1


@pytest.mark.parametrize("llm_mock,mock_execution", [(True, False), (False, True), (False, False)])
def test_nonisolated_acceptance_preserves_normal_dispatch(client, session, monkeypatch, llm_mock, mock_execution):
    cid, source = seed(client, monkeypatch, session)
    monkeypatch.setattr(app.state, "mock_execution", mock_execution)
    settings = api.get_settings().model_copy(update={"llm_mock": llm_mock})
    monkeypatch.setattr(api, "get_settings", lambda: settings)
    calls = []
    monkeypatch.setattr(api, "_start_message_generation_worker", lambda job_id: calls.append(("wake", job_id)))
    async def normal(bound_session, job_id):
        assert bound_session.get_bind() is session.get_bind()
        calls.append(("normal", job_id))
    async def mock(*a):
        pytest.fail("normal execution routed through isolated mock")
    monkeypatch.setattr(api, "_run_message_generation_job_in_session", normal)
    monkeypatch.setattr(api, "_run_mock_generation_queue", mock)
    response = regenerate(client, cid, source)
    assert response.status_code == 409, response.text
    job = receipt_job(session, cid, source)[1]
    assert "execution_mode" not in job.execution_options
    assert calls == [("wake", job.id), ("normal", job.id)]


def test_existing_normal_execution_keeps_auxiliary_enqueue_and_completed_replay(client, session, monkeypatch):
    cid, source = seed(client, monkeypatch, session)
    monkeypatch.setattr(app.state, "mock_execution", False)
    room = session.get(Conversation, cid)
    room.tts_enabled = True
    session.add(room)
    session.commit()
    monkeypatch.setattr(conversation_service, "should_update_scene_orchestration_summary", lambda *a, **k: True)
    response = regenerate(client, cid, source)
    assert response.status_code == 200, response.text
    receipt, job = receipt_job(session, cid, source)
    assert "execution_mode" not in job.execution_options
    assert job.status == "completed" and job.attempt_count == 1
    assert {task.task_type for task in session.exec(select(PostCommitTask)).all()} == {"tts", "compression"}
    before = (receipt.id, job.id, job.state_version, job.execution_options.copy())
    monkeypatch.setattr(app.state, "mock_execution", True)
    replay = regenerate(client, cid, source)
    assert replay.status_code == 200 and replay.json() == response.json()
    receipt, job = receipt_job(session, cid, source)
    assert before == (receipt.id, job.id, job.state_version, job.execution_options)


def test_persisted_mock_mode_survives_app_flag_and_completed_live_settings_replay(client, session, monkeypatch):
    cid, source = seed(client, monkeypatch, session)
    job = accept_pending(client, session, monkeypatch, cid, source)
    assert job.execution_options["execution_mode"] == "mock"
    monkeypatch.setattr(app.state, "mock_execution", False)
    binds = []
    runner = api._run_mock_generation_queue
    async def observed(bind):
        binds.append(bind)
        await runner(bind)
    monkeypatch.setattr(api, "_run_mock_generation_queue", observed)
    completed = regenerate(client, cid, source)
    assert completed.status_code == 200, completed.text
    assert binds == [session.get_bind()]
    settings = api.get_settings().model_copy(update={"llm_mock": False})
    monkeypatch.setattr(api, "get_settings", lambda: settings)
    replay = regenerate(client, cid, source)
    assert replay.status_code == 200 and replay.json() == completed.json()
    assert len(binds) == 1
    _, persisted = receipt_job(session, cid, source)
    assert persisted.execution_options["execution_mode"] == "mock" and persisted.attempt_count == 1


def test_deleted_completed_regeneration_reply_is_not_recreated(client, session, monkeypatch):
    cid, source = seed(client, monkeypatch, session)
    response = regenerate(client, cid, source)
    assert response.status_code == 200
    _, job = receipt_job(session, cid, source)
    session.delete(session.get(Message, response.json()[0]["id"]))
    session.commit()
    message_ids = {row.id for row in session.exec(select(Message)).all()}
    replay = regenerate(client, cid, source)
    assert replay.status_code == 410, replay.text
    assert {row.id for row in session.exec(select(Message)).all()} == message_ids
    assert receipt_job(session, cid, source)[1].attempt_count == 1


@pytest.mark.parametrize("status", ["failed", "cancelled"])
@pytest.mark.parametrize("mock_intent", [False, True])
def test_explicit_retry_rejects_incompatible_mode_without_mutation(client, session, monkeypatch, status, mock_intent):
    cid, source = seed(client, monkeypatch, session)
    job = accept_pending(client, session, monkeypatch, cid, source, mock=mock_intent)
    job.status = status
    job.state_version += 1
    job.error_message = "preserved failure"
    session.add(job)
    session.commit()
    session.refresh(job)
    before = job.model_dump()
    settings = api.get_settings().model_copy(update={"llm_mock": not mock_intent})
    monkeypatch.setattr(api, "get_settings", lambda: settings)
    wakes = []
    monkeypatch.setattr(api, "_wake_accepted_job", lambda *args: wakes.append(args[0]))
    response = client.post(f"/conversations/{cid}/generation-jobs/{job.id}/retry", json={"expected_state_version": job.state_version})
    assert response.status_code == 409, response.text
    if mock_intent:
        assert response.json()["detail"]["code"] == "mock_generation_held"
    else:
        assert "live-intended" in response.json()["detail"]
    session.expire_all()
    assert session.get(MessageGenerationJob, job.id).model_dump() == before
    assert wakes == []
    assert not session.exec(select(PostCommitTask)).all()


@pytest.mark.parametrize("status", ["failed", "cancelled"])
@pytest.mark.parametrize("mock_intent", [False, True])
def test_explicit_retry_keeps_original_mode_for_compatible_execution(client, session, monkeypatch, status, mock_intent):
    cid, source = seed(client, monkeypatch, session)
    job = accept_pending(client, session, monkeypatch, cid, source, mock=mock_intent)
    job.status = status
    job.state_version += 1
    session.add(job)
    session.commit()
    old_version, old_options = job.state_version, dict(job.execution_options)
    settings = api.get_settings().model_copy(update={"llm_mock": mock_intent})
    monkeypatch.setattr(api, "get_settings", lambda: settings)
    wakes = []
    monkeypatch.setattr(api, "_wake_accepted_job", lambda *args: wakes.append(args[0]))
    response = client.post(f"/conversations/{cid}/generation-jobs/{job.id}/retry", json={"expected_state_version": old_version})
    assert response.status_code == 200, response.text
    session.expire_all()
    updated = session.get(MessageGenerationJob, job.id)
    assert updated.status == "queued" and updated.state_version == old_version + 1
    assert updated.execution_options == old_options
    assert wakes == [job.id]
    replay = client.post(f"/conversations/{cid}/generation-jobs/{job.id}/retry", json={"expected_state_version": old_version})
    assert replay.status_code == 200 and replay.json()["state_version"] == old_version + 1
    assert wakes == [job.id]


@pytest.mark.parametrize("terminal_status", ["failed", "cancelled"])
@pytest.mark.parametrize("app_flag", [True, False])
def test_explicit_mock_retry_runs_with_no_paid_auxiliary_enqueue(client, session, monkeypatch, terminal_status, app_flag):
    cid, source = seed(client, monkeypatch, session)
    job = accept_pending(client, session, monkeypatch, cid, source)
    job.status = terminal_status
    job.state_version += 1
    room = session.get(Conversation, cid)
    room.tts_enabled = True
    session.add(job)
    session.add(room)
    session.commit()
    job_id, version, old_options = job.id, job.state_version, dict(job.execution_options)
    monkeypatch.setattr(app.state, "mock_execution", app_flag)
    calls = []
    real_runner = api._run_mock_generation_queue
    async def observed_runner(bind):
        calls.append(bind is session.get_bind())
        await real_runner(bind)
    monkeypatch.setattr(api, "_run_mock_generation_queue", observed_runner)
    monkeypatch.setattr(conversation_service, "should_update_scene_orchestration_summary", lambda *args, **kwargs: True)
    response = client.post(f"/conversations/{cid}/generation-jobs/{job_id}/retry", json={"expected_state_version": version})
    assert response.status_code == 200, response.text
    session.expire_all()
    completed = session.get(MessageGenerationJob, job_id)
    assert completed.status == "completed" and completed.execution_options == old_options
    assert completed.generated_message_ids and completed.attempt_count == 1
    ids = list(completed.generated_message_ids)
    assert calls == [True]
    replay = client.post(f"/conversations/{cid}/generation-jobs/{job_id}/retry", json={"expected_state_version": version})
    assert replay.status_code == 200 and replay.json()["generated_message_ids"] == ids
    assert calls == [True]
    assert not session.exec(select(PostCommitTask).where(col(PostCommitTask.task_type).in_(["tts", "compression"]))).all()


def test_async_send_receipt_replay_dispatches_persisted_mock_after_app_flag_change(client, session, monkeypatch):
    cid, _ = seed(client, monkeypatch, session)
    room = session.get(Conversation, cid)
    room.tts_enabled = True
    session.add(room)
    session.commit()
    payload = {"speaker_type": "user", "speaker_id": "u", "content": "A distinct synthetic continuation.", "metadata": {"client_request_id": "persisted-mock-acceptance"}}
    with monkeypatch.context() as patch:
        patch.setattr(api, "_run_mock_generation_queue", no_run)
        first = client.post(f"/conversations/{cid}/messages/jobs", json=payload)
    assert first.status_code == 200, first.text
    job_id = first.json()["job"]["id"]
    session.expire_all()
    accepted = session.get(MessageGenerationJob, job_id)
    assert accepted.status == "queued" and accepted.execution_options["execution_mode"] == "mock"
    source_id = accepted.incoming_message_id
    monkeypatch.setattr(app.state, "mock_execution", False)
    calls = []
    real_runner = api._run_mock_generation_queue
    async def observed(bind):
        calls.append(bind is session.get_bind())
        await real_runner(bind)
    monkeypatch.setattr(api, "_run_mock_generation_queue", observed)
    replay = client.post(f"/conversations/{cid}/messages/jobs", json=payload)
    assert replay.status_code == 200 and replay.json()["job"]["id"] == job_id
    session.expire_all()
    completed = session.get(MessageGenerationJob, job_id)
    assert completed.status == "completed" and completed.attempt_count == 1
    assert completed.incoming_message_id == source_id and completed.execution_options["execution_mode"] == "mock"
    assert completed.generated_message_ids and calls == [True]
    assert not session.exec(select(PostCommitTask).where(col(PostCommitTask.task_type).in_(["tts", "compression"]))).all()
