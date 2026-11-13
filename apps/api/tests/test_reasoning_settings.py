"""Selectable chat reasoning: synthetic DB only; no provider calls."""
import pytest
from pydantic import ValidationError
from sqlalchemy import inspect
from sqlmodel import SQLModel, create_engine

from app.db.models import RuntimeSetting
from app.db.session import migrate_sqlite_columns
from app.schemas.runtime_settings import RuntimeSettingUpdate
from app.services import runtime_settings_service as service


@pytest.mark.parametrize("level", ["low", "medium", "high", "xhigh", "max", None])
def test_levels_round_trip(session, level):
    setting = service.update_global_setting(session, RuntimeSettingUpdate(thinking_level=level))
    result = service.serialize_setting(setting, scope="default", session=session)
    assert result.thinking_level == (level or "low")
    assert result.effective_thinking_level == (level or "low")
    assert session.get(RuntimeSetting, "global").thinking_level == level


@pytest.mark.parametrize("level", ["off", "minimal", "HIGH", " high ", "", 0, True, [], {}])
def test_invalid_levels_are_rejected(level):
    with pytest.raises(ValidationError):
        RuntimeSettingUpdate(thinking_level=level)


def test_defaults_legacy_null_and_room_without_row(session):
    setting = service.get_global_setting(session)
    assert setting.thinking_level == "low"
    setting.thinking_level = None
    session.add(setting)
    session.commit()
    assert service.serialize_setting(setting, scope="default", session=session).thinking_level == "low"
    room_read = service.serialize_setting(service.get_effective_setting(session, "new-room"), scope="conversation", session=session)
    assert room_read.thinking_level is None
    assert room_read.effective_thinking_level == "low"


def test_dynamic_inheritance_omission_and_explicit_reset(session):
    service.update_global_setting(session, RuntimeSettingUpdate(thinking_level="high"))
    room = service.update_conversation_setting(session, "room", RuntimeSettingUpdate(response_length_preset="long", safety_preset="low"))
    assert room.thinking_level is None
    assert service.serialize_setting(room, scope="conversation", session=session).effective_thinking_level == "high"
    service.update_global_setting(session, RuntimeSettingUpdate(thinking_level="max"))
    assert service.serialize_setting(room, scope="conversation", session=session).effective_thinking_level == "max"
    room = service.update_conversation_setting(session, "room", RuntimeSettingUpdate(thinking_level="medium"))
    room = service.update_conversation_setting(session, "room", RuntimeSettingUpdate(compression_interval_turns=8))
    assert room.thinking_level == "medium"
    before = room.model_dump(exclude={"thinking_level", "updated_at"})
    room = service.update_conversation_setting(session, "room", RuntimeSettingUpdate(thinking_level=None))
    assert room.model_dump(exclude={"thinking_level", "updated_at"}) == before
    assert room.thinking_level is None
    assert service.serialize_setting(room, scope="conversation", session=session).effective_thinking_level == "max"
    assert service.llm_overrides_for_setting(room, session=session)["reasoning_effort"] == "max"


def test_chat_only_primary_fallback_and_mock_overrides(session, monkeypatch):
    service.update_global_setting(session, RuntimeSettingUpdate(thinking_level="xhigh"))
    room = RuntimeSetting(id="room", conversation_id="room", model_key="primary", fallback_model_key="fallback", compression_model_key="compression", default_tts_model_option_key="tts")
    monkeypatch.setattr(service, "_dynamic_llm_overrides", lambda _session, key, capability: {"model": key, "capability": capability})
    monkeypatch.setattr(service, "_option_available", lambda *args: True)
    monkeypatch.setattr(service.model_provider_service, "option_by_key", lambda *args: object())
    assert service.llm_overrides_for_setting(room, session=session)["reasoning_effort"] == "xhigh"
    assert service.fallback_llm_overrides_for_setting(room, session=session)["reasoning_effort"] == "xhigh"
    assert "reasoning_effort" not in service.compression_llm_overrides_for_setting(room, session=session)
    assert "reasoning_effort" not in service.compression_fallback_llm_overrides_for_setting(room, session=session)
    assert "reasoning_effort" not in service.tts_overrides_for_setting(room, session=session)
    monkeypatch.setattr(service, "_dynamic_llm_overrides", lambda *args: None)
    assert service.llm_overrides_for_setting(room, session=session) == {"provider": "mock", "model": "mock", "reasoning_effort": "xhigh"}
    assert service.compression_llm_overrides_for_setting(room, session=session) == {"provider": "mock", "model": "mock"}


@pytest.mark.parametrize("limit", [None, 0, -1, True, "100", 100])
def test_dynamic_overrides_carry_only_positive_integer_limits(session, monkeypatch, limit):
    from datetime import datetime, timezone
    from types import SimpleNamespace

    option = SimpleNamespace(key="synthetic", model="synthetic", provider_account_id="account", supports_json=True,
                             max_output_tokens=limit, context_window_tokens=limit)
    account = SimpleNamespace(id="account", provider_type="openai_compatible", api_key_secret_ref=None,
                              updated_at=datetime.now(timezone.utc), base_url=None)
    monkeypatch.setattr(service.model_provider_service, "option_by_key", lambda *args: option)
    monkeypatch.setattr(service.model_provider_service, "account_by_id", lambda *args: account)
    monkeypatch.setattr(service.model_provider_service, "keyless_local_allowed", lambda *args: True)
    monkeypatch.setattr(service.provider_secret_service, "get_secret", lambda *args: None)
    monkeypatch.setattr(service, "_option_available", lambda *args: True)
    overrides = service._dynamic_llm_overrides(session, option.key, "chat")
    assert overrides is not None
    for key in ("model_max_output_tokens", "model_context_window_tokens"):
        if type(limit) is int and limit > 0:
            assert overrides[key] == limit
        else:
            assert key not in overrides
    assert "reasoning_effort" not in overrides  # capability-neutral builder


def test_http_patch_read_inheritance_and_rejection(client):
    default_url = "/runtime-settings/default"
    assert client.get(default_url).json()["thinking_level"] == "low"
    assert client.patch(default_url, json={"thinking_level": "high"}).status_code == 200
    character = client.post("/characters", json={"name": "Synthetic", "persona": "Synthetic"}).json()
    room = client.post("/conversations", json={"mode": "user_character", "participants": [
        {"type": "user", "id": "user_001"}, {"type": "character", "id": character["id"]},
    ]}).json()
    room_url = f'/runtime-settings/conversations/{room["id"]}'
    initial = client.get(room_url).json()
    assert initial["thinking_level"] is None and initial["effective_thinking_level"] == "high"
    assert client.patch(room_url, json={"response_length_preset": "long"}).json()["thinking_level"] is None
    assert client.patch(default_url, json={"thinking_level": "max"}).status_code == 200
    assert client.get(room_url).json()["effective_thinking_level"] == "max"
    assert client.patch(room_url, json={"thinking_level": "medium"}).json()["effective_thinking_level"] == "medium"
    assert client.patch(room_url, json={"safety_preset": "low"}).json()["thinking_level"] == "medium"
    for invalid in ("off", "HIGH", 1, False):
        assert client.patch(room_url, json={"thinking_level": invalid}).status_code == 422
    assert client.get(room_url).json()["thinking_level"] == "medium"
    reset = client.patch(room_url, json={"thinking_level": None}).json()
    assert reset["thinking_level"] is None and reset["effective_thinking_level"] == "max"
    assert reset["response_length_preset"] == "long" and reset["safety_preset"] == "low"
    assert client.patch(default_url, json={"thinking_level": None}).json()["thinking_level"] == "low"
    assert client.get(room_url).json()["effective_thinking_level"] == "low"


def test_additive_migration_preserves_all_existing_rows(tmp_path):
    engine = create_engine("sqlite:///" + str(tmp_path / "legacy.db"))
    SQLModel.metadata.create_all(engine)
    with engine.begin() as connection:
        if "thinking_level" in {c["name"] for c in inspect(engine).get_columns("runtime_settings")}:
            connection.exec_driver_sql("ALTER TABLE runtime_settings DROP COLUMN thinking_level")
        connection.exec_driver_sql("INSERT INTO runtime_settings (id, conversation_id, compression_strategy, response_length_preset, min_output_tokens, compression_interval_turns, decision_enabled, safety_preset, updated_at) VALUES ('global', NULL, 'quality', 'long', 1280, 8, 0, 'high', '2020-01-02'), ('room', 'room', 'fast', 'short', 320, 3, 0, 'low', '2020-01-03')")
        connection.exec_driver_sql("CREATE TABLE untouched (id INTEGER, payload BLOB)")
        connection.exec_driver_sql("INSERT INTO untouched VALUES (7, x'0001ff')")
        before = {table: connection.exec_driver_sql(f'SELECT * FROM "{table}"').fetchall() for table in inspect(engine).get_table_names()}
        columns = [c["name"] for c in inspect(engine).get_columns("runtime_settings")]
    migrate_sqlite_columns(engine)
    migrate_sqlite_columns(engine)
    with engine.connect() as connection:
        for table, rows in before.items():
            selection = ", ".join(f'"{c}"' for c in columns) if table == "runtime_settings" else "*"
            assert connection.exec_driver_sql(f'SELECT {selection} FROM "{table}"').fetchall() == rows
        assert connection.exec_driver_sql("SELECT thinking_level FROM runtime_settings").fetchall() == [(None,), (None,)]
        column = next(c for c in inspect(engine).get_columns("runtime_settings") if c["name"] == "thinking_level")
        assert column["nullable"] and str(column["type"]) == "TEXT"
        assert connection.exec_driver_sql("PRAGMA integrity_check").scalar_one() == "ok"
    engine.dispose()
