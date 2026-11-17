"""Shared preparation is observational, even when previews resolve live routes."""
import pytest
from sqlalchemy import event
from sqlmodel import select

from app.core.config import get_settings
from app.db.models import Conversation, Message, SceneState
from app.engine.llm_client import LLMClient
from app.services import context_preview_service, generation_preparation, runtime_settings_service
from app.engine.harness_comparison import ComparisonOptions


@pytest.mark.parametrize("policy", ["compact", "expanded", "full_history"])
def test_both_previews_do_not_send_or_write_room_state(client, session, monkeypatch, policy):
    character = client.post("/characters", json={"name": "Preview fixture", "persona": "Synthetic."}).json()
    room = client.post("/conversations", json={"mode": "user_character", "participants": [
        {"type": "user", "id": "user"}, {"type": "character", "id": character["id"]},
    ]}).json()
    cid = room["id"]
    session.add(Message(id="preview-source", conversation_id=cid, speaker_type="system", speaker_id="director", content="Continue at dusk."))
    session.commit()
    monkeypatch.setenv("CONTEXT_MANAGEMENT_MODE", "automatic")
    monkeypatch.setenv("CHAT_CONTEXT_POLICY", policy)
    get_settings.cache_clear()

    def forbidden(*args, **kwargs):
        pytest.fail("Read-only preparation/preview must not send provider requests")

    monkeypatch.setattr(LLMClient, "chat", forbidden)
    # Existing runtime configuration lazily seeds its global defaults. Bootstrap
    # that configuration explicitly; the preview itself must not mutate a room.
    runtime_settings_service.get_global_setting(session)
    before = (session.get(Conversation, cid).model_dump(), session.get(SceneState, cid).model_dump())
    writes = []
    def observe(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().split()[0].upper() in {"INSERT", "UPDATE", "DELETE", "REPLACE"}:
            writes.append(statement)
    bind = session.get_bind()
    event.listen(bind, "before_cursor_execute", observe)
    try:
        preview = context_preview_service.build_context_preview(session, cid)
        compression = context_preview_service.build_compression_preview(session, cid)
        assert preview.conversation_id == compression.conversation_id == cid
        assert not session.new and not session.dirty and not session.deleted
        session.expire_all()
        after = (session.get(Conversation, cid).model_dump(), session.get(SceneState, cid).model_dump())
        assert after == before
        assert writes == []
        assert len(session.exec(select(Message)).all()) == 1
    finally:
        event.remove(bind, "before_cursor_execute", observe)
        get_settings.cache_clear()


def test_preparation_copies_route_overrides_and_ignores_unreachable_fallback(session, monkeypatch):
    from types import SimpleNamespace
    selected = {"provider": "mock", "model": "primary"}
    setting = SimpleNamespace(model_key="primary", fallback_model_key="unreachable", response_length_preset="medium")
    monkeypatch.setattr(runtime_settings_service, "llm_overrides_for_setting", lambda *a, **kw: selected)
    monkeypatch.setattr(runtime_settings_service, "fallback_llm_overrides_for_setting", lambda *a, **kw: None)
    resolved = []
    def option(db, key):
        resolved.append(key)
        return None
    monkeypatch.setattr(generation_preparation.model_provider_service, "option_by_key", option)
    plan = generation_preparation.prepare_generation_route(
        session, setting, genre_mode=None, is_multi_room=False, comparison=ComparisonOptions(),
    )
    assert selected == {"provider": "mock", "model": "primary"}
    assert plan.primary_overrides["min_output_tokens"] == plan.token_target
    assert plan.fallback_client is None and plan.fallback_overrides is None
    assert resolved == ["primary"]
