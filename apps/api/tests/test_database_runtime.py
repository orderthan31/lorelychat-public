from __future__ import annotations

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError
from sqlmodel import SQLModel

from app.db import models as _models  # noqa: F401 - registers tables in SQLModel metadata
from app.db.session import (
    SQLITE_BUSY_TIMEOUT_MS,
    checkpoint_sqlite,
    configure_sqlite_runtime,
    make_engine,
    migrate_sqlite_columns,
    sqlite_runtime_status,
)


def test_file_sqlite_engine_creates_missing_parent_directory(tmp_path):
    database_path = tmp_path / "nested" / "runtime.db"

    test_engine = make_engine(f"sqlite:///{database_path}")
    with test_engine.begin() as connection:
        connection.execute(text("CREATE TABLE smoke (id INTEGER PRIMARY KEY)"))

    assert database_path.is_file()


def test_file_sqlite_engine_enforces_runtime_pragmas_on_every_connection(tmp_path):
    database_path = tmp_path / "runtime.db"
    test_engine = make_engine(f"sqlite:///{database_path}")

    status = configure_sqlite_runtime(test_engine)

    assert status["journal_mode"] == "wal"
    assert status["foreign_keys"] == 1
    assert status["busy_timeout_ms"] == SQLITE_BUSY_TIMEOUT_MS
    assert status["synchronous"] == 1
    assert status["integrity_check"] == "ok"

    test_engine.dispose()
    with test_engine.connect() as connection:
        assert connection.execute(text("PRAGMA foreign_keys")).scalar_one() == 1
        assert connection.execute(text("PRAGMA busy_timeout")).scalar_one() == SQLITE_BUSY_TIMEOUT_MS
        assert connection.execute(text("PRAGMA synchronous")).scalar_one() == 1


def test_foreign_key_enforcement_rejects_orphan_rows(tmp_path):
    test_engine = make_engine(f"sqlite:///{tmp_path / 'foreign-keys.db'}")
    configure_sqlite_runtime(test_engine)

    with test_engine.begin() as connection:
        connection.execute(text("CREATE TABLE parents (id INTEGER PRIMARY KEY)"))
        connection.execute(text("CREATE TABLE children (id INTEGER PRIMARY KEY, parent_id INTEGER REFERENCES parents(id))"))

    with pytest.raises(IntegrityError):
        with test_engine.begin() as connection:
            connection.execute(text("INSERT INTO children (id, parent_id) VALUES (1, 999)"))


def test_checkpoint_and_runtime_status_use_application_connection(tmp_path):
    test_engine = make_engine(f"sqlite:///{tmp_path / 'checkpoint.db'}")
    configure_sqlite_runtime(test_engine)
    with test_engine.begin() as connection:
        connection.execute(text("CREATE TABLE events (id INTEGER PRIMARY KEY, payload TEXT NOT NULL)"))
        connection.execute(text("INSERT INTO events (payload) VALUES ('ready')"))

    checkpoint = checkpoint_sqlite(test_engine, mode="PASSIVE")
    status = sqlite_runtime_status(test_engine)

    assert checkpoint["mode"] == "PASSIVE"
    assert set(checkpoint) == {"mode", "busy", "log_frames", "checkpointed_frames"}
    assert status["journal_mode"] == "wal"
    assert status["integrity_check"] == "ok"


def test_checkpoint_rejects_unknown_mode(tmp_path):
    test_engine = make_engine(f"sqlite:///{tmp_path / 'invalid-checkpoint.db'}")
    configure_sqlite_runtime(test_engine)

    with pytest.raises(ValueError, match="Unsupported SQLite checkpoint mode"):
        checkpoint_sqlite(test_engine, mode="DROP TABLE messages")


def test_additive_generation_schema_migration_preserves_existing_rows(tmp_path):
    test_engine = make_engine(f"sqlite:///{tmp_path / 'legacy-generation.db'}")
    configure_sqlite_runtime(test_engine)
    with test_engine.begin() as connection:
        connection.execute(text(
            "CREATE TABLE messages ("
            "id VARCHAR PRIMARY KEY, conversation_id VARCHAR NOT NULL, speaker_type VARCHAR NOT NULL, "
            "speaker_id VARCHAR NOT NULL, content VARCHAR NOT NULL, metadata JSON, created_at DATETIME NOT NULL)"
        ))
        connection.execute(text(
            "CREATE TABLE message_generation_jobs ("
            "id VARCHAR PRIMARY KEY, conversation_id VARCHAR NOT NULL, incoming_message_id VARCHAR NOT NULL, "
            "status VARCHAR NOT NULL, error_message VARCHAR, generated_message_ids JSON, "
            "created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL, completed_at DATETIME)"
        ))
        connection.execute(text(
            "INSERT INTO messages (id, conversation_id, speaker_type, speaker_id, content, metadata, created_at) "
            "VALUES ('msg_legacy', 'conv_legacy', 'user', 'user_001', 'preserve me', '{}', CURRENT_TIMESTAMP)"
        ))
        connection.execute(text(
            "INSERT INTO message_generation_jobs "
            "(id, conversation_id, incoming_message_id, status, generated_message_ids, created_at, updated_at) "
            "VALUES ('job_legacy', 'conv_legacy', 'msg_legacy', 'queued', '[]', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
        ))

    SQLModel.metadata.create_all(test_engine)
    migrate_sqlite_columns(test_engine)

    inspector = inspect(test_engine)
    message_columns = {column["name"] for column in inspector.get_columns("messages")}
    job_columns = {column["name"] for column in inspector.get_columns("message_generation_jobs")}
    index_names = {index["name"] for index in inspector.get_indexes("messages")}

    assert {"thought", "generation_job_id", "reply_index"}.issubset(message_columns)
    assert {
        "lease_owner",
        "lease_expires_at",
        "heartbeat_at",
        "attempt_count",
        "cancel_requested_at",
        "state_version",
    }.issubset(job_columns)
    assert "uq_messages_generation_reply" in index_names
    assert {"generation_job_events", "post_commit_tasks"}.issubset(inspector.get_table_names())

    with test_engine.connect() as connection:
        assert connection.execute(text("SELECT content FROM messages WHERE id='msg_legacy'")).scalar_one() == "preserve me"
        row = connection.execute(text(
            "SELECT status, attempt_count, state_version FROM message_generation_jobs WHERE id='job_legacy'"
        )).one()
        assert tuple(row) == ("queued", 0, 0)


def test_runtime_setting_compression_strategy_migration_defaults_to_quality_and_preserves_rows(tmp_path):
    test_engine = make_engine(f"sqlite:///{tmp_path / 'legacy-runtime-setting.db'}")
    configure_sqlite_runtime(test_engine)
    with test_engine.begin() as connection:
        connection.execute(text(
            "CREATE TABLE runtime_settings ("
            "id VARCHAR PRIMARY KEY, conversation_id VARCHAR, model_key VARCHAR, "
            "response_length_preset VARCHAR NOT NULL DEFAULT 'medium', "
            "min_output_tokens INTEGER NOT NULL DEFAULT 768, updated_at DATETIME NOT NULL)"
        ))
        connection.execute(text(
            "INSERT INTO runtime_settings "
            "(id, conversation_id, model_key, response_length_preset, min_output_tokens, updated_at) "
            "VALUES ('global', NULL, 'keep-model', 'medium', 768, CURRENT_TIMESTAMP)"
        ))

    migrate_sqlite_columns(test_engine)

    columns = {column["name"] for column in inspect(test_engine).get_columns("runtime_settings")}
    assert "compression_strategy" in columns
    with test_engine.connect() as connection:
        row = connection.execute(text(
            "SELECT model_key, compression_strategy FROM runtime_settings WHERE id = 'global'"
        )).one()
    assert tuple(row) == ("keep-model", "quality")
