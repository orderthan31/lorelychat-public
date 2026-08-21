from __future__ import annotations

import json
import threading
import time

from sqlmodel import Session, col, select

from app.db.models import GenerationJobEvent, MessageGenerationJob
from app.services import conversation_service


def _job_with_events(session: Session, *, job_id: str = "job_sse", terminal: bool = True) -> MessageGenerationJob:
    job = MessageGenerationJob(
        id=job_id,
        conversation_id="conv_sse",
        incoming_message_id="msg_sse",
        status="queued",
    )
    session.add(job)
    conversation_service.append_generation_job_event(session, job, "queued")
    session.commit()
    session.refresh(job)
    if terminal:
        conversation_service.mark_message_generation_job_completed(
            session,
            job,
            ["msg_generated_1", "msg_generated_2"],
        )
    return job


def _data_frames(body: str) -> list[dict]:
    return [json.loads(line.removeprefix("data: ")) for line in body.splitlines() if line.startswith("data: ")]


def test_generation_job_sse_replays_lifecycle_and_closes_after_terminal_event(client, session):
    job = _job_with_events(session)

    response = client.get(f"/conversations/{job.conversation_id}/generation-jobs/{job.id}/events")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "no-cache, no-transform"
    assert response.headers["x-accel-buffering"] == "no"
    assert response.text.startswith("retry: 2000\n\n")
    assert "event: queued" in response.text
    assert "event: completed" in response.text
    frames = _data_frames(response.text)
    assert [frame["status"] for frame in frames] == ["queued", "completed"]
    assert frames[-1]["generated_message_ids"] == ["msg_generated_1", "msg_generated_2"]
    assert frames[-1]["state_version"] > frames[0]["state_version"]
    for forbidden in ("dialogue", "action", "thought", "emotion", "prompt", "response_content"):
        assert forbidden not in response.text


def test_generation_job_sse_honors_last_event_id_and_query_cursor(client, session):
    job = _job_with_events(session, job_id="job_resume")
    events = session.exec(
        select(GenerationJobEvent)
        .where(GenerationJobEvent.job_id == job.id)
        .order_by(col(GenerationJobEvent.id))
    ).all()
    first_id = events[0].id
    assert first_id is not None

    header_response = client.get(
        f"/conversations/{job.conversation_id}/generation-jobs/{job.id}/events",
        headers={"Last-Event-ID": str(first_id)},
    )
    query_response = client.get(
        f"/conversations/{job.conversation_id}/generation-jobs/{job.id}/events?after_event_id={first_id}",
    )

    assert [frame["status"] for frame in _data_frames(header_response.text)] == ["completed"]
    assert [frame["status"] for frame in _data_frames(query_response.text)] == ["completed"]
    assert f"id: {first_id}\n" not in header_response.text


def test_generation_job_sse_checks_ownership_and_rejects_invalid_cursor(client, session):
    job = _job_with_events(session, job_id="job_owner")

    assert client.get(f"/conversations/wrong/generation-jobs/{job.id}/events").status_code == 404
    response = client.get(
        f"/conversations/{job.conversation_id}/generation-jobs/{job.id}/events",
        headers={"Last-Event-ID": "not-an-integer"},
    )
    assert response.status_code == 400


def test_generation_job_sse_emits_heartbeat_without_mutating_or_cancelling_job(client, session, engine):
    job = _job_with_events(session, job_id="job_heartbeat_sse", terminal=False)

    def complete_later():
        time.sleep(1.2)
        with Session(engine) as worker_session:
            current = worker_session.get(MessageGenerationJob, job.id)
            assert current is not None
            conversation_service.mark_message_generation_job_completed(worker_session, current, ["msg_after_heartbeat"])

    worker = threading.Thread(target=complete_later)
    worker.start()
    try:
        response = client.get(
            f"/conversations/{job.conversation_id}/generation-jobs/{job.id}/events?heartbeat_seconds=1"
        )
    finally:
        worker.join(timeout=5)

    assert response.status_code == 200
    assert ": heartbeat\n\n" in response.text
    assert "event: completed" in response.text
    session.expire_all()
    assert session.get(MessageGenerationJob, job.id).status == "completed"
