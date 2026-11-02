"""Direct production-boundary integration without app lifespan or providers."""
import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlmodel import SQLModel, Session, create_engine
from app.db.models import Character, Message, PostCommitTask
from app.services import external_memory_service as memory, tts_service as tts


@pytest.mark.parametrize("result,expected", [({}, None), ({"results": [{}]}, None), ([{}], None), ({"id": "actual-id"}, "actual-id"), ({"results": [{"memory_id": "actual-id"}]}, "actual-id")])
def test_external_ack_never_manufactures_a_remote_identity(result, expected):
    provider = object.__new__(memory.Mem0ExternalMemoryProvider)
    provider.base_url = ""
    provider.settings = SimpleNamespace(mem0_user_id="synthetic")
    provider.client = SimpleNamespace(add=lambda *a, **kw: result)
    assert provider.add_memory("synthetic", metadata={}) == expected


def test_external_disabled_default_has_no_provider_work(monkeypatch):
    from app.core.config import Settings
    from app.db.models import CharacterMemory
    settings = Settings(_env_file=None)
    assert settings.mem0_enabled is False and settings.mem0_write_enabled is False
    def forbidden(*a, **kw):
        raise AssertionError("disabled means zero provider work")
    monkeypatch.setattr(memory, "get_provider", forbidden)
    note = CharacterMemory(id="note", character_id="__room__", conversation_id="r", memory_type="user_note", content="A local note")
    assert memory.sync_local_memory_to_external(note, settings=settings) is None
    assert memory.sync_battle_memory_card_to_external({"content": "record"}, settings=settings) is None


@pytest.mark.parametrize("entrypoint", ["manual", "durable"])
def test_real_tts_entrypoints_reject_an_inflight_edit(tmp_path, monkeypatch, entrypoint):
    from app.api.tts import synthesize_message_tts
    from app.api.conversations import _execute_claimed_post_commit_task
    engine = create_engine("sqlite:///" + str(tmp_path / "integration.db"))
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(Character(id="c", name="C", persona="p", tts_voice_style="F1"))
        session.add(Message(id="m", conversation_id="r", speaker_type="character", speaker_id="c", content="Before", metadata_={"raw_body": "Before"}))
        task = PostCommitTask(id="task", unique_key="tts:m", task_type="tts", status="processing", lease_owner="owner", lease_expires_at=datetime.now(timezone.utc)+timedelta(minutes=1), payload_={"message_id": "m"})
        session.add(task);session.commit()
        def synth(*a, **kw):
            with Session(engine) as other:
                edited = other.get(Message, "m")
                edited.content = "After"
                edited.metadata_ = {"raw_body": "After", "render_parts": [{"type": "dialogue", "text": "After"}]}
                other.add(edited);other.commit()
            return "/uploads/tts/obsolete.wav"
        monkeypatch.setattr(tts, "synthesize_to_upload", synth)
        if entrypoint == "manual":
            response = synthesize_message_tts("m", session=session)
            assert response["audio_url"] == "" and response["tts_text"] == "After"
        else:
            asyncio.run(_execute_claimed_post_commit_task(session, task, lease_owner="owner"))
            session.commit()
        with Session(engine) as verify:
            current = verify.get(Message, "m")
            assert current.content == "After" and current.metadata_["raw_body"] == "After"
            assert "tts_audio_url" not in current.metadata_
            if entrypoint == "durable":
                assert verify.get(PostCommitTask, "task").status == "completed"


@pytest.mark.parametrize("poison_asset", [False, True])
def test_compact_actual_job_preserves_paid_primary_when_auxiliary_exceeds_capacity(client, session, monkeypatch, poison_asset):
    import json
    from app.api.conversations import _run_message_generation_job_in_session
    from app.core.config import get_settings
    from app.db.models import ChatCommand
    from app.engine.llm_client import LLMClient, LLMResponse
    monkeypatch.setenv("CHAT_RESPONSE_PROTOCOL", "free_body_json")
    monkeypatch.setenv("CHAT_CONTEXT_POLICY", "compact")
    monkeypatch.setenv("CHAT_COMMAND_POSTPROCESS_MODE", "separate")
    get_settings.cache_clear()
    monkeypatch.setattr("app.api.conversations._start_message_generation_worker", lambda *a: None)
    cast = [client.post("/characters", json={"name": name, "persona": "Synthetic adult actor."}).json() for name in ("Alpha", "Beta")]
    room = client.post("/conversations", json={"mode": "character_character", "genre_mode": "custom", "participants": [{"type": "character", "id": char["id"], "order_index": i} for i, char in enumerate(cast)]}).json()["id"]
    command = ChatCommand(id="capacity_command", name="derivedcapacity", display_name="Capacity", prompt="Continue.", generation_prompt="Continue normally.", postprocess_prompt="Retain the complete primary and make a panel. " + "Auxiliary card detail. " * 4000, postprocess_target="last_bubble", postprocess_context_options=[], postprocess_block_types=["note"])
    session.add(command)
    if poison_asset:
        from app.db.models import CharacterAsset
        session.add(CharacterAsset(id="poison", character_id=cast[0]["id"], label="poison", image_url="/uploads/synthetic.png", metadata_={"asset_selection_v2": {"scope": "generic", "required_scene_tags": 123}}))
    session.commit()
    calls = []
    body = "<a>Turns to the map.</a>The northern bridge remains open.<t>I should keep my uncertainty private.</t>We can leave at dawn."
    async def fake_chat(self, messages, response_format=None, **kwargs):
        calls.append(response_format)
        assert not response_format or response_format.get("name") != "chat_command_messages", "oversized optional command must not dispatch"
        return LLMResponse(content=json.dumps({"replies": [{"who": "C1", "text": body}, {"who": "C2", "text": "I will bring the lantern."}]}), finish_reason="STOP")
    monkeypatch.setattr(LLMClient, "chat", fake_chat)
    response = client.post(f"/conversations/{room}/messages/jobs", json={"speaker_type": "user", "speaker_id": "synthetic", "content": "!derivedcapacity Continue."})
    assert response.status_code == 200, response.text
    job_id = response.json()["job"]["id"]
    asyncio.run(_run_message_generation_job_in_session(session, job_id))
    state = client.get(f"/conversations/{room}/generation-jobs/{job_id}").json()
    assert state["status"] == "completed", state
    messages = client.get(f"/conversations/{room}/messages").json()
    generated = [row for row in messages if row["id"] in state["generated_message_ids"]]
    assert len(generated) == 2 and len(calls) == 1
    assert generated[0]["metadata"]["raw_body"] == body
    assert generated[0]["thought"] == "I should keep my uncertainty private."
    assert generated[0]["metadata"]["command_postprocess_error_code"] == "context_maintenance_required"
    asyncio.run(_run_message_generation_job_in_session(session, job_id))
    assert len(calls) == 1
