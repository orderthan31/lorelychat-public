"""Preview/postcommit reserve the same selected and fallback reasoning routes."""
import pytest

from app.api import conversations as api
from app.core.config import get_settings
from app.engine.harness_comparison import ComparisonOptions
from app.services import context_preview_service as preview, runtime_settings_service as runtime


class CapturedCapacity(Exception):
    pass


@pytest.mark.asyncio
@pytest.mark.parametrize("surface", ["compression_preview", "context_preview", "postcommit"])
async def test_reasoning_route_reserve_reaches_every_capacity_surface(client, session, monkeypatch, surface):
    monkeypatch.setenv("CONTEXT_MANAGEMENT_MODE", "automatic")
    monkeypatch.setenv("CHAT_CONTEXT_POLICY", "compact")
    get_settings.cache_clear()
    character_response = client.post("/characters", json={"name": "Budget fixture", "persona": "Synthetic card"})
    assert character_response.status_code == 200
    character = character_response.json()
    room_response = client.post("/conversations", json={
        "mode": "user_character", "genre_mode": "slice_of_life",
        "participants": [{"type": "user", "id": "fixture-user"}, {"type": "character", "id": character["id"]}],
    })
    assert room_response.status_code == 200
    room_id = room_response.json()["id"]
    selected = {"provider": "openai", "model": "synthetic-primary", "reasoning_effort": "high", "model_max_output_tokens": 11111}
    fallback = {"provider": "openai", "model": "synthetic-fallback", "reasoning_effort": "high", "model_max_output_tokens": 22222}
    monkeypatch.setattr(runtime, "llm_overrides_for_setting", lambda *a, **kw: dict(selected))
    monkeypatch.setattr(runtime, "fallback_llm_overrides_for_setting", lambda *a, **kw: dict(fallback))
    seen = []

    class BudgetClient:
        def __init__(self, *, profile, overrides):
            assert profile == "chat"
            assert overrides["reasoning_effort"] == "high"
            assert overrides["min_output_tokens"] == runtime.preset_token_target("medium")
            seen.append(dict(overrides))
            self.overrides = overrides

        def max_output_tokens(self):
            return self.overrides["model_max_output_tokens"]

    def capacity(*args, **kwargs):
        assert kwargs["completion_reserve_tokens"] == 22222
        raise CapturedCapacity

    owner = api if surface == "postcommit" else preview
    monkeypatch.setattr(owner, "LLMClient", BudgetClient)
    monkeypatch.setattr(owner, "resolve_safe_model_context_capacity", capacity)
    with pytest.raises(CapturedCapacity):
        if surface == "postcommit":
            await api._run_scene_compression_in_session(
                session, room_id, generated_character_count=1,
                character_ids=[character["id"]], comparison_options=ComparisonOptions(context_policy="compact"),
            )
        elif surface == "compression_preview":
            preview.build_compression_preview(session, room_id)
        else:
            preview.build_context_preview(session, room_id)
    assert [entry["model"] for entry in seen] == ["synthetic-primary", "synthetic-fallback"]
