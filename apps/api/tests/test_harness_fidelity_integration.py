"""Integration regressions: restored payload and its estimator/preview agree."""
import pytest
from app.core.config import get_settings
from app.db.models import ChatCommand, Conversation, Message
from app.engine import command_overlay, prompts
from app.services import context_preview_service, conversation_service, system_prompt_service


def test_automatic_estimator_counts_complete_public_history():
    message = Message(id="old-long-source", conversation_id="synthetic", speaker_type="user", speaker_id="user_001",
                      content="A distinct instruction. " * 50 + "TAIL_FACT", action="Visible gesture. " * 25 + "ACTION_TAIL", thought="NOT_SHARED")
    expected = prompts.approx_tokens(prompts.format_message_for_context(message, include_thought=False, compact=False)) + 1
    assert conversation_service.automatic_context_message_tokens(message) == expected


@pytest.mark.parametrize("speaker_type", ["user", "system"])
def test_preview_uses_complete_directive_for_all_selected_commands(client, session, monkeypatch, speaker_type):
    monkeypatch.setenv("CHAT_RESPONSE_PROTOCOL", "tagged_text")
    monkeypatch.setenv("CHAT_COMMAND_POSTPROCESS_MODE", "combined_overlay")
    get_settings.cache_clear()
    character = client.post("/characters", json={"name": "Synthetic voice", "persona": "Preserve a distinct voice."}).json()
    room = client.post("/conversations", json={"mode": "user_character", "participants": [
        {"type": "user", "id": "user_001"}, {"type": "character", "id": character["id"]}]}).json()
    commands = [
        ChatCommand(id="synthetic-combined", name="synthetic_combined", display_name="Combined", prompt="Public panel", generation_prompt="Complete command intent. " * 35 + "COMBINED_END",
                    postprocess_target="last_bubble", postprocess_block_types=["note"], priority=10),
        ChatCommand(id="synthetic-standalone", name="synthetic_standalone", display_name="Standalone", prompt="Separate panel", generation_prompt="Independent source intent. " * 35 + "STANDALONE_END",
                    postprocess_target="first_bubble", postprocess_block_types=["image"], priority=1),
    ]
    for command in commands:
        session.add(command)
    stored_room = session.get(Conversation, room["id"])
    stored_room.active_command_ids = [command.id for command in commands]
    session.add(stored_room)
    session.add(Message(id="synthetic-current", conversation_id=room["id"], speaker_type=speaker_type, speaker_id="user_001", content="Current source"))
    session.commit()
    captured = []
    original = prompts.compose_generation_directive
    def capture(**kwargs):
        captured.append(kwargs)
        return original(**kwargs)
    monkeypatch.setattr(prompts, "compose_generation_directive", capture)
    data = client.get(f"/conversations/{room['id']}/context-preview").json()
    directive = next(section for section in data["sections"] if section["key"] == "directive")
    assert "COMBINED_END" in directive["content"] and "STANDALONE_END" in directive["content"]
    assert captured and [command.id for command in captured[-1]["commands"]] == [command.id for command in commands]
    settings = system_prompt_service.prompt_settings_map()
    base_key = "directive_scene_direction" if speaker_type == "system" else "directive_natural_chat"
    assert captured[-1]["base_directive"] == settings[base_key]
    assert captured[-1]["command_overlay_plan"].eligible
    assert directive["content"] == original(**captured[-1])
    assert directive["included"] is True
    assert "Separate postprocessing only for !synthetic_standalone" in directive["content"]


def test_preview_warns_when_required_material_exceeds_fixed_target(client, monkeypatch):
    character = client.post("/characters", json={"name": "Fixed target", "persona": "Do not discard this canon."}).json()
    room = client.post("/conversations", json={"mode": "user_character", "participants": [{"type": "character", "id": character["id"]}]}).json()
    monkeypatch.setattr(prompts, "prompt_budget_for_context", lambda **kwargs: 100)
    data = client.get(f"/conversations/{room['id']}/context-preview").json()
    assert data["total_budget_tokens"] == 100 and data["used_tokens"] > 100
    assert any("context_budget_exceeded" in warning for warning in data["warnings"])
