"""Startup is not retention maintenance; ordinary snapshot writes still are.

conftest establishes synthetic environment roots before importing app modules.
Use the real lifespan and seeders, not the client fixture's patched seeder.
"""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.core.config import get_settings
from app.db.models import ChatCommand, LLMPromptSnapshot
from app.db.session import get_session
from app.main import app
from app.services import prompt_snapshot_service
from app.services.chat_command_service import DEFAULT_CHAT_COMMANDS


def _stored_row(engine, model, row_id):
    with Session(engine) as session:
        row = session.get(model, row_id)
        return None if row is None else row.model_dump()


@pytest.mark.parametrize("preserved_model", [LLMPromptSnapshot, ChatCommand])
def test_real_lifespan_and_command_list_preserve_existing_rows(engine, monkeypatch, preserved_model):
    old = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=30)
    snapshot = LLMPromptSnapshot(
        id="startup-aged-snapshot", compiled_text="Synthetic retained prompt",
        messages_json=[{"role": "user", "content": "Synthetic input"}],
        metadata_={"synthetic": True}, created_at=old,
    )
    command = ChatCommand(
        id="startup-authored-command", name=DEFAULT_CHAT_COMMANDS[0].name,
        display_name="Authored default-name command", description="Synthetic author edit",
        prompt="Authored legacy prompt", generation_prompt="Authored generation prompt",
        postprocess_prompt="Authored postprocess prompt", context_sources=[],
        enabled=False, priority=17, created_at=old - timedelta(days=1), updated_at=old,
    )
    with Session(engine) as session:
        session.add(snapshot)
        session.add(command)
        session.commit()
    row_id = "startup-aged-snapshot" if preserved_model is LLMPromptSnapshot else "startup-authored-command"
    before = _stored_row(engine, preserved_model, row_id)
    assert before is not None
    assert get_settings().llm_mock is True
    monkeypatch.setattr(app.state, "engine", engine, raising=False)
    monkeypatch.setattr(app.state, "mock_execution", False, raising=False)

    def isolated_session():
        with Session(engine) as session:
            yield session

    monkeypatch.setitem(app.dependency_overrides, get_session, isolated_session)
    with TestClient(app) as client:
        assert app.state.ready is True
        assert app.state.dispatchers_started is False
        assert app.state.mock_generation_task is None
        after_startup = _stored_row(engine, preserved_model, row_id)
        response = client.get("/chat-commands", params={"include_disabled": True})
        assert response.status_code == 200
        assert any(row["id"] == "startup-authored-command" for row in response.json())
        after_list = _stored_row(engine, preserved_model, row_id)
    after_shutdown = _stored_row(engine, preserved_model, row_id)
    assert {"startup": after_startup, "list": after_list, "shutdown": after_shutdown} == {
        "startup": before, "list": before, "shutdown": before,
    }


@pytest.mark.parametrize("cleanup", ["record", "explicit"])
def test_seven_day_retention_remains_active(session, monkeypatch, cleanup):
    now = datetime(2030, 1, 15, 12, tzinfo=timezone.utc)
    monkeypatch.setattr(prompt_snapshot_service, "_utc_now", lambda: now)
    cutoff = now.replace(tzinfo=None) - timedelta(days=7)
    for row_id, created_at in (
        ("expired", cutoff - timedelta(microseconds=1)),
        ("boundary", cutoff),
        ("recent", cutoff + timedelta(days=1)),
    ):
        session.add(LLMPromptSnapshot(id=row_id, compiled_text="Synthetic", created_at=created_at))
    session.commit()
    expected = {"boundary", "recent"}
    if cleanup == "record":
        recorded = prompt_snapshot_service.record_prompt_snapshot(
            session, conversation_id=None, compiled_text="New synthetic prompt",
            messages=[], ledger=[], pipeline_steps=[],
        )
        expected.add(recorded.id)
    else:
        assert prompt_snapshot_service.prune_prompt_snapshots(session) == 1
    assert set(session.exec(select(LLMPromptSnapshot.id)).all()) == expected
