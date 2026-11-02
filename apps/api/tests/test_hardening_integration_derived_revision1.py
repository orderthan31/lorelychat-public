"""Actual auxiliary await / SQLite finalization contracts, synthetic only."""
import asyncio
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest
from sqlalchemy import text
from sqlmodel import Session, select

from app.db.models import ChatCommand, Conversation, Message, PostCommitTask
from app.engine.llm_client import LLMClient, LLMResponse
from app.services import chat_command_service as commands
from app.services import external_memory_service as external


def test_new_context_sources_roundtrip_preserves_historical_empty(client, session):
    legacy = ChatCommand(id="legacy_ctx", name="legacy_ctx", display_name="Legacy", prompt="Authored", postprocess_context_options=[])
    session.add(legacy); session.commit()
    assert client.get("/chat-commands/legacy_ctx").json()["context_sources"] is None
    assert commands._command_context_options(legacy) == set(commands.DEFAULT_POSTPROCESS_CONTEXT_OPTIONS)
    old_updated = legacy.updated_at
    created = client.post("/chat-commands", json={"name": "new_ctx", "prompt": "Authored", "context_sources": []})
    assert created.status_code == 200, created.text
    identity = created.json()["id"]
    assert created.json()["context_sources"] == []
    current = session.get(ChatCommand, identity)
    assert commands._command_context_options(current) == set()
    assert client.patch(f"/chat-commands/{identity}", json={"context_sources": ["world"]}).json()["context_sources"] == ["world"]
    # Omitted preserves the authored new field; explicit null restores legacy.
    assert client.patch(f"/chat-commands/{identity}", json={"description": "Edited"}).json()["context_sources"] == ["world"]
    assert client.patch(f"/chat-commands/{identity}", json={"context_sources": None}).json()["context_sources"] is None
    session.expire_all()
    assert commands._command_context_options(session.get(ChatCommand, identity)) == set(commands.DEFAULT_POSTPROCESS_CONTEXT_OPTIONS)
    historical = session.get(ChatCommand, "legacy_ctx")
    assert historical.postprocess_context_options == [] and historical.context_sources is None
    assert historical.updated_at == old_updated


def _job(client, session, monkeypatch, *, target="last_bubble", context_sources=None):
    from app.core.config import get_settings
    monkeypatch.setenv("CHAT_RESPONSE_PROTOCOL", "free_body_json")
    monkeypatch.setenv("CHAT_COMMAND_POSTPROCESS_MODE", "separate")
    get_settings.cache_clear()
    monkeypatch.setattr("app.api.conversations._start_message_generation_worker", lambda *a: None)
    cast = [client.post("/characters", json={"name": n, "persona": "Synthetic adult explorer."}).json() for n in ("Alpha", "Beta")]
    world = client.post("/world-settings", json={"title": "World sentinel", "world_seed": "World bridge sentinel", "description": "Synthetic canon"}).json()
    room_response = client.post("/conversations", json={"mode": "character_character", "genre_mode": "custom", "world_setting_id": world["id"], "participants": [{"type": "character", "id": c["id"], "order_index": i} for i,c in enumerate(cast)]})
    assert room_response.status_code == 200, room_response.text
    room = room_response.json()["id"]
    from app.db.models import SceneState
    scene = session.get(SceneState, room)
    scene.current_conflict = "Scene conflict sentinel"
    session.add(scene)
    cmd = ChatCommand(id="integration_aux", name="integration_aux", display_name="Auxiliary sentinel", prompt="Keep original prose.", postprocess_prompt="Make a synthetic panel.", postprocess_target=target, postprocess_context_options=[], context_sources=context_sources)
    session.add(cmd); session.commit()
    response = client.post(f"/conversations/{room}/messages/jobs", json={"speaker_type": "user", "speaker_id": "synthetic", "content": "!integration_aux Source directive sentinel."})
    assert response.status_code == 200, response.text
    return room, response.json()["job"]["id"]


BODY = "<a>Studies the distant ridge.</a>The northern bridge remains open.<t>I will keep that uncertainty private.</t>We can leave at dawn."
PRIMARY = {"replies": [{"who": "C1", "text": BODY}, {"who": "C2", "text": "I will bring the lantern and the spare map."}]}
PANEL = {"messages": [{"content": "", "blocks": [{"type": "note", "title": "Status", "text": "Bridge open 🗺️"}]}]}


@pytest.mark.parametrize("outcome", ["success", "repair", "exhausted", "rollback"])
def test_actual_auxiliary_await_ends_reads_before_initial_and_repair(client, session, monkeypatch, outcome):
    from app.api.conversations import _run_message_generation_job_in_session
    room, job = _job(client, session, monkeypatch, context_sources=["world", "scene", "characters", "turn_messages"])
    auxiliary = []
    primary = []
    async def fake_chat(self, messages, response_format=None, **kwargs):
        if response_format and response_format.get("name") == "chat_command_messages":
            assert not session.in_transaction(), "auxiliary await retained an autobegin/read transaction"
            prompt = messages[1]["content"]
            assert "World bridge sentinel" in prompt and "Scene conflict sentinel" in prompt
            assert "Source directive sentinel" in prompt and "Alpha" in prompt
            auxiliary.append(copy.deepcopy(messages))
            # No generated replies or changed source metadata became durable early.
            with Session(session.get_bind()) as reader:
                rows = reader.exec(select(Message).where(Message.conversation_id == room)).all()
                assert len(rows) == 1
                assert (rows[0].metadata_ or {}).get("command_postprocess_status") == "pending"
            # Freeze retries: changes during the first await may not rewrite inputs.
            if len(auxiliary) == 1:
                with Session(session.get_bind()) as editor:
                    cmd = editor.get(ChatCommand, "integration_aux")
                    cmd.postprocess_prompt = "MUTATED command must not leak"
                    from app.db.models import Character, SceneState, WorldSetting
                    actor = editor.exec(select(Character).where(Character.name == "Alpha")).first()
                    actor.persona = "MUTATED cast must not leak"
                    scene = editor.get(SceneState, room)
                    scene.current_conflict = "MUTATED scene must not leak"
                    world = editor.get(WorldSetting, editor.get(Conversation, room).world_setting_id)
                    world.world_seed = "MUTATED world must not leak"
                    for obj in (cmd, actor, scene, world): editor.add(obj)
                    editor.commit()
            if outcome == "exhausted" or outcome == "repair" and len(auxiliary) == 1:
                # Simulate a recorder opening a fresh read/autobegin after capture.
                session.exec(select(Conversation).where(Conversation.id == room)).first()
                return LLMResponse(content="{}", finish_reason="STOP")
            return LLMResponse(content=json.dumps(PANEL), finish_reason="STOP")
        primary.append(messages)
        return LLMResponse(content=json.dumps(PRIMARY), finish_reason="STOP")
    monkeypatch.setattr(LLMClient, "chat", fake_chat)
    if outcome == "rollback":
        session.exec(text("CREATE TRIGGER reject_second_reply BEFORE INSERT ON messages WHEN NEW.generation_job_id IS NOT NULL AND NEW.reply_index = 1 BEGIN SELECT RAISE(ABORT, 'synthetic second insert failure'); END"))
        session.commit()
    asyncio.run(_run_message_generation_job_in_session(session, job))
    state = client.get(f"/conversations/{room}/generation-jobs/{job}").json()
    rows = client.get(f"/conversations/{room}/messages").json()
    assert len(primary) == 1
    assert len(auxiliary) == (2 if outcome in {"repair", "exhausted"} else 1)
    if len(auxiliary) == 2:
        assert auxiliary[0] == auxiliary[1][:-1]
        assert "MUTATED" not in auxiliary[1][1]["content"]
    if outcome == "rollback":
        assert state["status"] == "failed", state
        assert len(rows) == 1 and not state["generated_message_ids"]
        assert not session.exec(select(PostCommitTask)).all()
        return
    assert state["status"] == "completed", state
    generated = [r for r in rows if r["id"] in state["generated_message_ids"]]
    assert len(generated) == (2 if outcome == "exhausted" else 3)
    assert generated[0]["metadata"]["raw_body"] == BODY
    assert generated[0]["thought"] == "I will keep that uncertainty private."
    if outcome == "exhausted":
        assert generated[0]["metadata"]["command_postprocess_error_code"] == "command_schema_invalid"
    else:
        assert generated[-1]["metadata"]["command_blocks"] == PANEL["messages"][0]["blocks"]
    asyncio.run(_run_message_generation_job_in_session(session, job))
    assert len(primary) == 1 and len(auxiliary) == (2 if outcome in {"repair", "exhausted"} else 1)


def _unicode_payload(field, scalar):
    item = {"content": "Replacement valid prose", "blocks": [{"type": "note", "title": "Title", "text": "Text"}]}
    if field == "content": item["content"] = scalar
    elif field == "title": item["blocks"][0]["title"] = scalar
    elif field == "text": item["blocks"][0]["text"] = scalar
    elif field == "author": item["blocks"] = [{"type": "comments", "title": "Title", "comments": [{"author": scalar, "text": "Text"}]}]
    elif field == "cell": item["blocks"] = [{"type": "table", "title": "Title", "headers": ["Column"], "rows": [[scalar]]}]
    elif field == "nested": item["blocks"] = [{"type": "cards", "title": "Title", "cards": [{"title": "Card", "text": scalar}]}]
    return {"messages": [item]}


@pytest.mark.parametrize("raw_scalar", [False, True])
@pytest.mark.parametrize("target", ["last_bubble", "all_bubbles"])
@pytest.mark.parametrize("field", ["content", "title", "text", "author", "cell", "nested"])
@pytest.mark.parametrize("scalar", ["\ud800", "\udfff"])
def test_standalone_and_rewrite_reject_unicode_before_actual_persistence(client, session, monkeypatch, target, field, scalar, raw_scalar):
    from app.api.conversations import _run_message_generation_job_in_session
    room, job = _job(client, session, monkeypatch, target=target, context_sources=[])
    calls = []
    async def fake_chat(self, messages, response_format=None, **kwargs):
        if response_format and response_format.get("name") == "chat_command_messages":
            assert not session.in_transaction()
            calls.append("aux")
            payload = _unicode_payload(field, scalar)
            if target == "all_bubbles": payload["messages"] *= 2
            return LLMResponse(content=json.dumps(payload, ensure_ascii=not raw_scalar), finish_reason="STOP")
        calls.append("primary")
        return LLMResponse(content=json.dumps(PRIMARY), finish_reason="STOP")
    monkeypatch.setattr(LLMClient, "chat", fake_chat)
    asyncio.run(_run_message_generation_job_in_session(session, job))
    state = client.get(f"/conversations/{room}/generation-jobs/{job}").json()
    assert state["status"] == "completed", state
    rows = client.get(f"/conversations/{room}/messages").json()
    generated = [r for r in rows if r["id"] in state["generated_message_ids"]]
    assert len(generated) == 2 and calls == ["primary", "aux"]
    assert generated[0]["metadata"]["raw_body"] == BODY and generated[0]["thought"] == "I will keep that uncertainty private."
    assert generated[0]["metadata"]["command_postprocess_error_code"] == "command_invalid_unicode"
    assert generated[0]["metadata"]["command_postprocess_parse_attempts"] == 1
    assert all("command_blocks" not in r["metadata"] for r in generated)
    asyncio.run(_run_message_generation_job_in_session(session, job))
    assert calls == ["primary", "aux"]


@pytest.mark.parametrize("target", ["last_bubble", "all_bubbles"])
def test_valid_long_rewrite_and_private_thought_persist_losslessly(client, session, monkeypatch, target):
    from app.api.conversations import _run_message_generation_job_in_session
    room, job = _job(client, session, monkeypatch, target=target, context_sources=[])
    prose = " ".join(f"At waypoint {i}, a distinct trail sign describes the valley route {i*i}." for i in range(75)) + " 🗺️ END"
    thought = " ".join(f"Private observation {i}: the mark on stone {i*i} requires a different route." for i in range(40))
    primary = copy.deepcopy(PRIMARY)
    primary["replies"][0]["text"] = f"<a>Studies the ridge.</a>The bridge is open.<t>{thought}</t>We leave at dawn."
    async def fake_chat(self, messages, response_format=None, **kwargs):
        if response_format and response_format.get("name") == "chat_command_messages":
            payload = {"messages": [{"content": prose, "blocks": []}, {"content": "The lantern is ready.", "blocks": []}]} if target == "all_bubbles" else {"messages": [{"content": "", "blocks": [{"type": "note", "title": "Long panel 🗺️", "text": prose}]}]}
            return LLMResponse(content=json.dumps(payload), finish_reason="STOP")
        return LLMResponse(content=json.dumps(primary), finish_reason="STOP")
    monkeypatch.setattr(LLMClient, "chat", fake_chat)
    asyncio.run(_run_message_generation_job_in_session(session, job))
    state = client.get(f"/conversations/{room}/generation-jobs/{job}").json()
    assert state["status"] == "completed", state
    rows = client.get(f"/conversations/{room}/messages").json()
    generated = [r for r in rows if r["id"] in state["generated_message_ids"]]
    if target == "all_bubbles":
        assert generated[0]["content"] == prose
    else:
        assert generated[-1]["metadata"]["command_blocks"][0]["text"] == prose
    assert generated[0]["thought"] == thought


@pytest.mark.parametrize("configured, expected", [(None, 120.0), (0.25, 0.25)])
def test_optional_tts_timeout_none_keeps_a_finite_bound(tmp_path, monkeypatch, configured, expected):
    from app.services import tts_service as tts
    monkeypatch.setattr(tts, "get_settings", lambda: SimpleNamespace(tts_timeout_seconds=configured))
    calls = []
    monkeypatch.setattr(tts, "_run_tts_process", lambda command, **kw: calls.append(kw))
    tts._synthesize_supertonic("Entire spoken sentence", voice_style="F1", output_path=tmp_path / "not-generated.wav")
    assert calls[0]["timeout"] == expected
    assert json.loads(calls[0]["payload"])["text"] == "Entire spoken sentence"


@pytest.mark.parametrize("result,expected", [({}, None), ({"results": [{"id": "one"}, {"id": "two"}]}, None), ({"id": "one", "memory_id": "two"}, None), ({"results": [{"id": "real-memory-id"}]}, "real-memory-id")])
def test_external_bounded_ack_only_uses_unambiguous_real_identity(result, expected):
    provider = external.Mem0ExternalMemoryProvider(SimpleNamespace(mem0_base_url="", llm_task_deadline_seconds=None, mem0_user_id="synthetic"))
    provider.client = SimpleNamespace(add=lambda *a, **kw: result)
    assert provider.add_memory("synthetic content", metadata={}) == expected


def test_external_sdk_constructor_and_request_share_killable_deadline(tmp_path, monkeypatch):
    child_pid = tmp_path / "child.pid"
    provider = external.Mem0ExternalMemoryProvider(SimpleNamespace(mem0_base_url="", llm_task_deadline_seconds=0.15, mem0_user_id="synthetic"))
    def hangs():
        child_pid.write_text(str(os.getpid()))
        time.sleep(30)
    monkeypatch.setattr(provider, "_sdk_client", hangs)
    start = time.monotonic()
    assert provider.add_memory("synthetic", metadata={}) is None
    assert time.monotonic() - start < 3
    pid = int(child_pid.read_text())
    with pytest.raises(ProcessLookupError): os.kill(pid, 0)
    assert list(tmp_path.glob("lorechat-external-*")) == []


def test_external_process_group_descendant_does_not_survive_timeout(tmp_path):
    marker = tmp_path / "descendant.pid"
    def operation():
        child = subprocess.Popen([sys.executable, "-c", "import time;time.sleep(30)"])
        marker.write_text(str(child.pid))
        time.sleep(30)
    with pytest.raises(external.ExternalMemoryOperationError, match="deadline"):
        external._bounded_external_call(operation, timeout=0.2)
    pid = int(marker.read_text())
    # A killed descendant may briefly be a zombie awaiting the host init reaper.
    state = Path(f"/proc/{pid}/stat")
    assert not state.exists() or state.read_text().split()[2] == "Z"


@pytest.mark.parametrize("identity", [None, "actual-window-id"])
def test_external_window_ack_is_not_marked_ingested_without_identity(session, monkeypatch, identity):
    from app.core.config import Settings
    settings = Settings(_env_file=None, memory_provider="mem0", mem0_enabled=True, mem0_write_enabled=True)
    room = Conversation(id="external-window-room", title="Synthetic", mode="multi")
    rows = [Message(id=f"external-window-{i}", conversation_id=room.id, speaker_type="user", speaker_id="user", content=f"Synthetic statement {i}") for i in range(2)]
    session.add(room)
    for row in rows: session.add(row)
    session.commit()
    monkeypatch.setattr(external, "get_provider", lambda settings: SimpleNamespace(add_memory=lambda *a, **kw: identity))
    assert external.sync_message_window_to_external(session, room, rows, genre_mode="custom", settings=settings) == identity
    session.expire_all()
    metadata = session.get(Message, rows[-1].id).metadata_
    if identity is None:
        assert external.MESSAGE_WINDOW_INGEST_KEY not in metadata
    else:
        assert metadata[external.MESSAGE_WINDOW_INGEST_KEY]["memory_id"] == identity


def test_external_http_operation_keeps_inference_off_and_real_ack(tmp_path, monkeypatch):
    import httpx
    receipt = tmp_path / "http-receipt.json"
    def fake_post(url, **kwargs):
        receipt.write_text(json.dumps({"url": url, "payload": kwargs["json"], "timeout": kwargs["timeout"]}))
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"results": [{"id": "real-http-memory-id"}]})
    monkeypatch.setattr(httpx, "post", fake_post)
    settings = SimpleNamespace(mem0_base_url="https://synthetic.invalid", mem0_api_key="", mem0_user_id="synthetic", llm_task_deadline_seconds=None)
    provider = external.Mem0ExternalMemoryProvider(settings)
    assert provider.add_memory("Synthetic stored note", metadata={"local_memory_id": "note-1"}) == "real-http-memory-id"
    saved = json.loads(receipt.read_text())
    assert saved["payload"]["infer"] is False and saved["timeout"] == 60


def test_external_search_fanout_shares_one_deadline(tmp_path, monkeypatch):
    from app.core.config import Settings
    settings = Settings(_env_file=None, memory_provider="mem0", mem0_enabled=True, mem0_read_enabled=True, llm_task_deadline_seconds=0.2)
    provider = external.Mem0ExternalMemoryProvider(settings)
    def slow_search(*args, **kwargs):
        time.sleep(0.3)
        return []
    provider.client = SimpleNamespace(search=slow_search)
    monkeypatch.setattr(external, "get_provider", lambda _: provider)
    start = time.monotonic()
    assert external.search_external_continuity(conversation_id="synthetic", character_id="actor", genre_mode="battle", query="Synthetic", settings=settings) == []
    assert time.monotonic() - start < 1


def test_root_boundary_lifecycle_prototype_retains_real_identity_and_rejects_stale_ack(tmp_path):
    """Executable requested schema/CAS prototype, NOT a production lifecycle fix."""
    import sqlite3
    with sqlite3.connect(tmp_path / "external-outbox-prototype.db") as db:
        db.executescript("""
        CREATE TABLE notes(id TEXT PRIMARY KEY, revision INTEGER NOT NULL, remote_id TEXT);
        CREATE TABLE sync_outbox(note_id TEXT NOT NULL, revision INTEGER NOT NULL,
            operation TEXT NOT NULL, remote_id TEXT, status TEXT NOT NULL,
            UNIQUE(note_id, revision, operation));
        INSERT INTO notes VALUES('note', 1, NULL);
        INSERT INTO sync_outbox VALUES('note', 1, 'add', NULL, 'pending');
        """)
        # A content revision committed while the old provider create awaited.
        db.execute("UPDATE notes SET revision=2 WHERE id='note'")
        identity = "provider-issued-identity"
        # Record actual acceptance in the old outbox even when it is stale for
        # the current row. Reconciliation must UPDATE that remote identity,
        # never issue another add with a fabricated ID.
        db.execute("UPDATE sync_outbox SET remote_id=?, status='stale_ack' WHERE note_id='note' AND revision=1", (identity,))
        changed = db.execute("UPDATE notes SET remote_id=? WHERE id='note' AND revision=1", (identity,)).rowcount
        assert changed == 0
        db.execute("INSERT INTO sync_outbox VALUES('note',2,'update',?,'pending')", (identity,))
        db.execute("UPDATE notes SET remote_id=? WHERE id='note' AND revision=2", (identity,))
        # Delete tombstone outlives the local row with the exact real identity.
        db.execute("INSERT INTO sync_outbox VALUES('note',3,'delete',?,'pending')", (identity,))
        db.execute("DELETE FROM notes WHERE id='note'")
        assert db.execute("SELECT remote_id,status FROM sync_outbox WHERE operation='delete'").fetchone() == (identity, 'pending')
        assert db.execute("SELECT COUNT(*) FROM notes").fetchone()[0] == 0
