"""Provider-free expected-correct regressions for derived-state boundaries."""
import asyncio
import copy
import json
import os
import sys
import time
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
from sqlmodel import SQLModel, Session, create_engine, select
from app.db.models import BattleMatchRecord, Character, CharacterAsset, ChatCommand, Conversation, Message
from app.services import asset_selector, battle_ledger_service as battle, chat_command_service as commands, tts_service as tts
from app.services.context_management_service import resolve_model_context_capacity


def wav(path):
    tts._write_wave_file(path, b"\0\0" * 100)


@pytest.mark.parametrize("corrupt", [b"partial", b"RIFF\0\0\0\0WAVE"])
def test_partial_cache_is_not_a_hit(tmp_path, monkeypatch, corrupt):
    monkeypatch.setattr(tts, "TTS_UPLOAD_DIR", tmp_path)
    calls = []
    def synth(text, *, voice_style, output_path):
        calls.append(output_path)
        assert not output_path.name.endswith(".wav") or output_path.name.startswith(".")
        wav(output_path)
    monkeypatch.setattr(tts, "_synthesize_supertonic", synth)
    final = tmp_path / f"sample_supertonic_supertonic-3_F1_{tts._hash_text('spoken')}.wav"
    final.write_bytes(corrupt)
    url = tts.synthesize_to_upload("spoken", voice_style="F1", file_stem="sample")
    assert calls and final.name in url
    assert tts.synthesize_to_upload("spoken", voice_style="F1", file_stem="sample") == url
    assert len(calls) == 1
    assert len(list(tmp_path.iterdir())) == 1


def test_failed_synthesis_never_publishes_partial(tmp_path, monkeypatch):
    monkeypatch.setattr(tts, "TTS_UPLOAD_DIR", tmp_path)
    def fail(text, *, voice_style, output_path):
        output_path.write_bytes(b"partial")
        raise RuntimeError("synthetic interruption")
    monkeypatch.setattr(tts, "_synthesize_supertonic", fail)
    with pytest.raises(RuntimeError):
        tts.synthesize_to_upload("spoken", voice_style="F1", file_stem="sample")
    assert list(tmp_path.iterdir()) == []


def test_invalid_completed_audio_is_not_published(tmp_path, monkeypatch):
    monkeypatch.setattr(tts, "TTS_UPLOAD_DIR", tmp_path)
    monkeypatch.setattr(tts, "_synthesize_supertonic", lambda text, **kw: kw["output_path"].write_bytes(b"bad"))
    with pytest.raises(tts.TTSUnavailableError):
        tts.synthesize_to_upload("spoken", voice_style="F1", file_stem="sample")
    assert not list(tmp_path.glob("*.wav"))


@pytest.mark.parametrize("change", ["edit", "delete", "voice", "unrelated"])
def test_tts_completion_rechecks_current_row_and_voice(tmp_path, monkeypatch, change):
    engine = create_engine("sqlite:///" + str(tmp_path / "race.db"))
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        character = Character(id="speaker", name="Speaker", persona="persona", tts_voice_style="F1")
        message = Message(id="m", conversation_id="room", speaker_type="character", speaker_id="speaker", content="original speech", metadata_={"raw_body": "original speech"})
        session.add(character); session.add(message); session.commit(); session.refresh(message)
        def synth(*args, **kw):
            with Session(engine) as other:
                row = other.get(Message, "m")
                if change == "edit":
                    row.content = "edited speech"
                    row.metadata_ = {"raw_body": "edited speech", "render_parts": [{"type": "dialogue", "text": "edited speech"}]}
                    other.add(row)
                elif change == "delete":
                    other.delete(row)
                elif change == "voice":
                    char = other.get(Character, "speaker"); char.tts_voice_style = "F2"; other.add(char)
                else:
                    row.metadata_ = {**row.metadata_, "new_annotation": "keep"}; other.add(row)
                other.commit()
            return "/uploads/tts/new.wav"
        monkeypatch.setattr(tts, "synthesize_to_upload", synth)
        tts.ensure_message_tts(session, message)
        with Session(engine) as verify:
            row = verify.get(Message, "m")
            if change == "delete":
                assert row is None
            elif change == "edit":
                assert row.content == "edited speech" and row.metadata_["raw_body"] == "edited speech"
                assert "tts_audio_url" not in row.metadata_
            elif change == "voice":
                assert "tts_audio_url" not in row.metadata_
            else:
                assert row.metadata_["new_annotation"] == "keep"
                assert row.metadata_["tts_text"] == "original speech"


def test_cached_metadata_requires_matching_text_and_valid_file(session, tmp_path, monkeypatch):
    monkeypatch.setattr(tts, "TTS_UPLOAD_DIR", tmp_path)
    char = Character(id="c", name="C", persona="p", tts_voice_style="F1")
    msg = Message(id="m", conversation_id="r", speaker_id="c", speaker_type="character", content="new", metadata_={"tts_audio_url": "/uploads/tts/missing.wav", "tts_text": "old", "tts_provider": "supertonic", "tts_model": "supertonic-3", "tts_voice_style": "F1"})
    session.add(char);session.add(msg);session.commit()
    calls = []
    monkeypatch.setattr(tts, "synthesize_to_upload", lambda *a, **kw: calls.append(a) or "/uploads/tts/fresh.wav")
    tts.ensure_message_tts(session, msg)
    assert calls and msg.metadata_["tts_text"] == "new"


def test_local_deadline_terminates_process_and_descendants(tmp_path):
    marker = tmp_path / "late"
    script = "import subprocess,sys,time; subprocess.Popen([sys.executable,'-c',\"import time,pathlib;time.sleep(0.8);pathlib.Path(sys.argv[1]).write_text('bad')\".replace('sys.argv[1]',repr(sys.argv[1]))]);time.sleep(30)"
    started = time.monotonic()
    with pytest.raises(tts.TTSUnavailableError):
        tts._run_tts_process([sys.executable, "-c", script, str(marker)], payload=b"", timeout=0.15)
    assert time.monotonic() - started < 2
    time.sleep(1)
    assert not marker.exists()


@pytest.mark.parametrize("required", [123, "beach", {"bad": 1}, [123], None])
def test_bad_optional_policy_is_skipped_not_raised(session, required):
    asset = CharacterAsset(id="bad", character_id="c", label="bad", image_url="/x", mood_tags=["happy"], metadata_={"asset_selection_v2": {"scope": "generic", "required_scene_tags": required}})
    session.add(asset);session.commit()
    selected = asset_selector.select_asset_for_message(session, conversation_id="r", character_id="c", emotion="happy", action=None, content="spoken", scene_state=None)
    assert selected is None


@pytest.mark.parametrize("raw,expected", [([], set(commands.DEFAULT_POSTPROCESS_CONTEXT_OPTIONS)), (None, set(commands.DEFAULT_POSTPROCESS_CONTEXT_OPTIONS)), (["scene"], {"scene"}), ("[]", set(commands.DEFAULT_POSTPROCESS_CONTEXT_OPTIONS))])
def test_legacy_context_preserves_inheritance(raw, expected):
    # Historical falsey context options always inherited; do not rewrite saved intent.
    assert commands._command_context_options(SimpleNamespace(postprocess_context_options=raw)) == expected


@pytest.mark.parametrize("legacy", [[], "[]", None, ["scene"]])
@pytest.mark.parametrize("explicit,expected", [([], set()), (["scene"], {"scene"})])
def test_empty_context_is_distinct_from_inherit(legacy, explicit, expected):
    # Explicit-none belongs only to the new nullable field, never legacy [].
    command = SimpleNamespace(postprocess_context_options=legacy, context_sources=explicit)
    assert commands._command_context_options(cast(ChatCommand, command)) == expected


@pytest.mark.parametrize("legacy", [[], "[]", None])
def test_new_context_null_keeps_legacy_defaults(legacy):
    command = SimpleNamespace(postprocess_context_options=legacy, context_sources=None)
    assert commands._command_context_options(cast(ChatCommand, command)) == set(commands.DEFAULT_POSTPROCESS_CONTEXT_OPTIONS)


@pytest.mark.asyncio
@pytest.mark.parametrize("policy", ["compact", "expanded", "full_history"])
async def test_all_standalone_modes_admit_before_dispatch_and_preserve_prose(session, monkeypatch, policy):
    from app.engine.harness_comparison import ComparisonOptions
    command = ChatCommand(id="cmd", name="cmd", display_name="cmd", prompt="instructions", postprocess_target="last_bubble", postprocess_context_options=[])
    source = Message(id="s", conversation_id="r", speaker_type="user", speaker_id="u", content="request")
    primary = Message(id="g", conversation_id="r", speaker_type="character", speaker_id="c", content="Original complete public prose.", thought="Private expression retained.", metadata_={"raw_body": "immutable", "render_parts": []})
    before = copy.deepcopy((primary.content, primary.thought, primary.metadata_))
    monkeypatch.setattr(commands, "build_command_postprocess_context", lambda *a, **kw: "")
    calls = []
    async def dispatch(**kw):
        calls.append(kw)
        raise AssertionError("over capacity must never dispatch")
    monkeypatch.setattr(commands, "chat_with_optional_conversation_id", dispatch)
    capacity = resolve_model_context_capacity(None, room_prompt_budget_tokens=1)
    client = SimpleNamespace(max_output_tokens=lambda: 4096)
    result = await commands.apply_chat_command_postprocess(session=session, command=command, conversation_id="r", source_message=source, generated_messages=[primary], llm_client=client, commit=False, comparison_options=ComparisonOptions(context_policy=policy), context_capacity=capacity)
    assert calls == [] and result == [primary]
    assert (primary.content, primary.thought) == before[:2]
    assert primary.metadata_["raw_body"] == before[2]["raw_body"]
    assert primary.metadata_["command_postprocess_error_code"] == "context_maintenance_required"
    assert primary.metadata_["command_postprocess_parse_attempts"] == 0


@pytest.mark.parametrize("status,label", [("in_progress", "in_progress"), ("cancelled", "cancelled")])
def test_official_pending_and_cancelled_never_invent_wins(session, status, label):
    room = Conversation(id="r", title="room", mode="user_character", genre_mode="battle")
    session.add(room)
    session.add(BattleMatchRecord(id="b", conversation_id="r", matchup_key="pair", participant_a_id="A", participant_b_id="B", result_status=status))
    session.commit()
    text = battle.format_official_battle_state(session, room)
    assert label in text and "A" in text and "B" in text
    assert "미정 승" not in text and "미정 패" not in text


def test_official_records_select_latest_and_active(session):
    room = Conversation(id="r", title="room", mode="user_character", genre_mode="battle")
    session.add(room)
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for i in range(20):
        session.add(BattleMatchRecord(id=f"b{i}", conversation_id="r", matchup_key=f"pair{i}", participant_a_id="A", participant_b_id="B", result_status="in_progress" if i == 0 else "completed", winner_id=None if i == 0 else "A", loser_id=None if i == 0 else "B", metadata_={"match_order": i+1}, created_at=base+timedelta(days=i)))
    session.commit()
    text = battle.format_official_battle_state(session, room, limit=3)
    assert "pair19:" in text and "pair18:" in text and "pair0:" in text
    assert "pair1:" not in text


def test_concurrent_wav_producers_publish_only_complete_files(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    barrier = Barrier(2)
    monkeypatch.setattr(tts, "TTS_UPLOAD_DIR", tmp_path)
    observed = []
    def synth(text, *, voice_style, output_path):
        output_path.write_bytes(b"partial")
        observed.append(output_path)
        assert not list(tmp_path.glob("sample_*.wav"))
        barrier.wait(timeout=3)
        wav(output_path)
    monkeypatch.setattr(tts, "_synthesize_supertonic", synth)
    def generate():
        return tts.synthesize_to_upload("same full sentence", voice_style="F1", file_stem="sample")
    with ThreadPoolExecutor(max_workers=2) as pool:
        a = pool.submit(generate); b = pool.submit(generate)
        assert a.result(timeout=5) == b.result(timeout=5)
    assert len(observed) == 2 and observed[0] != observed[1]
    assert len(list(tmp_path.iterdir())) == 1
    assert tts._valid_wave(next(tmp_path.iterdir()))


def test_standings_rebuild_resets_corrected_results_without_history_objects(session, monkeypatch):
    from app.db.models import BattleStanding
    record = BattleMatchRecord(id="b", conversation_id="r", matchup_key="pair", participant_a_id="A", participant_b_id="B", result_status="completed", winner_id="A", loser_id="B")
    session.add(record); session.flush()
    battle._rebuild_standings(session, "r");session.commit()
    assert session.get(BattleStanding, ("r", "A")).wins == 1
    record.result_status = "cancelled";session.add(record);session.flush()
    battle._rebuild_standings(session, "r");session.commit()
    assert session.get(BattleStanding, ("r", "A")).wins == 0
    assert session.get(BattleStanding, ("r", "B")).losses == 0


def test_owned_lookups_do_not_load_history_prose(session):
    from sqlalchemy import event
    from app.db.models import MessageAsset
    statements = []
    def capture(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)
    event.listen(session.get_bind(), "before_cursor_execute", capture)
    try:
        for i in range(25):
            session.add(Message(id=f"m{i:02}", conversation_id="r", speaker_type="character", speaker_id="c", content="large archived prose" * 100))
            session.add(MessageAsset(id=f"link{i}", message_id=f"m{i:02}", asset_id=f"a{i}"))
        session.commit();statements.clear()
        result = asset_selector.recent_asset_ranks(session, "r", "c", limit=3)
        assert len(result) == 3
        assert len(statements) == 1 and "messages.content" not in statements[0]
        statements.clear()
        assert battle._active_match(session, "r") is None
        assert len(statements) == 1 and "LIMIT" in statements[0]
    finally:
        event.remove(session.get_bind(), "before_cursor_execute", capture)


@pytest.mark.asyncio
async def test_compact_retry_checks_schema_and_preserves_primary_on_overflow(session, monkeypatch):
    from app.engine.harness_comparison import rendered_input_tokens
    from app.engine.llm_client import LLMResponse
    command = ChatCommand(id="cmd", name="cmd", display_name="cmd", prompt="instruction", postprocess_target="last_bubble", postprocess_context_options=[])
    source = Message(id="s", conversation_id="r", speaker_type="user", speaker_id="u", content="request")
    primary = Message(id="g", conversation_id="r", speaker_type="character", speaker_id="c", content="Keep complete prose.", thought="Keep private voice.", metadata_={"raw_body": "keep"})
    monkeypatch.setattr(commands, "build_command_postprocess_context", lambda *a, **kw: "")
    capacity = resolve_model_context_capacity(None)
    captures = []
    original_guard = commands.enforce_rendered_context_capacity
    projected = []
    def guard(messages, capacity, **kwargs):
        projected.append(rendered_input_tokens(messages, history_serialization=kwargs["history_serialization"]))
        assert json.loads(messages[-1]["content"]) == commands.command_response_format(1, overlay=True, block_types=command.postprocess_block_types)
        if len(projected) == 1:
            return original_guard(messages, replace(capacity, working_input_tokens=projected[0]), **kwargs)
        return original_guard(messages, replace(capacity, working_input_tokens=projected[0]), **kwargs)
    monkeypatch.setattr(commands, "enforce_rendered_context_capacity", guard)
    async def dispatch(**kwargs):
        captures.append(kwargs)
        return LLMResponse(content="bad", finish_reason="STOP")
    monkeypatch.setattr(commands, "chat_with_optional_conversation_id", dispatch)
    result = await commands.apply_chat_command_postprocess(session=session, command=command, conversation_id="r", source_message=source, generated_messages=[primary], llm_client=SimpleNamespace(max_output_tokens=lambda: 2048), commit=False, context_capacity=capacity)
    assert len(captures) == 1 and len(projected) == 2 and projected[1] > projected[0]
    assert result == [primary] and primary.content == "Keep complete prose." and primary.thought == "Keep private voice."
    assert primary.metadata_["raw_body"] == "keep"
    assert primary.metadata_["command_postprocess_parse_attempts"] == 1
    assert primary.metadata_["command_postprocess_error_code"] == "context_maintenance_required"


def test_message_cache_invalidation_never_unlinks_another_reference(session, tmp_path, monkeypatch):
    monkeypatch.setattr(tts, "TTS_UPLOAD_DIR", tmp_path)
    old = tmp_path / "shared.wav";wav(old)
    char = Character(id="c", name="C", persona="p", tts_voice_style="F1")
    cached = {"tts_audio_url": "/uploads/tts/shared.wav", "tts_text": "Old speech", "tts_voice_style": "F1", "tts_provider": "supertonic", "tts_model": "supertonic-3"}
    edited = Message(id="a", conversation_id="r", speaker_type="character", speaker_id="c", content="New speech", metadata_=dict(cached))
    other = Message(id="b", conversation_id="r", speaker_type="character", speaker_id="c", content="Old speech", metadata_=dict(cached))
    session.add_all([char, edited, other]);session.commit()
    monkeypatch.setattr(tts, "_synthesize_supertonic", lambda text, **kwargs: wav(kwargs["output_path"]))
    tts.ensure_message_tts(session, edited)
    assert tts._valid_wave(old)
    session.refresh(other)
    assert other.metadata_["tts_audio_url"] == "/uploads/tts/shared.wav"
    assert edited.metadata_["tts_audio_url"] != other.metadata_["tts_audio_url"]


def test_local_worker_receives_full_text_and_configured_deadline(monkeypatch, tmp_path):
    observed = []
    text = "A full unabridged sentence. " * 200
    monkeypatch.setattr(tts, "get_settings", lambda: SimpleNamespace(tts_timeout_seconds=0.25))
    monkeypatch.setattr(tts, "_run_tts_process", lambda command, **kwargs: observed.append((command, kwargs)))
    tts._synthesize_supertonic(text, voice_style="F1", output_path=tmp_path / "out.wav")
    command, kwargs = observed[0]
    assert command == [sys.executable, "-m", "app.services.derived_tts_worker"]
    assert kwargs["timeout"] == 0.25
    assert json.loads(kwargs["payload"])["text"] == text
