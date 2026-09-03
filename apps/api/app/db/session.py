from collections.abc import Generator
from pathlib import Path

from sqlalchemy import event, inspect, text
from sqlalchemy.engine import Engine, make_url
from sqlmodel import Session, SQLModel, create_engine

from app.core.config import get_settings


SQLITE_BUSY_TIMEOUT_MS = 5_000
SQLITE_WAL_AUTOCHECKPOINT_PAGES = 1_000
SQLITE_CHECKPOINT_MODES = {"PASSIVE", "FULL", "RESTART", "TRUNCATE"}


def _configure_sqlite_connection(dbapi_connection, _connection_record) -> None:
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_MS}")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute(f"PRAGMA wal_autocheckpoint={SQLITE_WAL_AUTOCHECKPOINT_PAGES}")
    finally:
        cursor.close()


def make_engine(database_url: str | None = None):
    url = database_url or get_settings().database_url
    parsed_url = make_url(url)
    if parsed_url.drivername.startswith("sqlite") and parsed_url.database and parsed_url.database != ":memory:":
        Path(parsed_url.database).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
    connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
    created_engine = create_engine(url, echo=False, connect_args=connect_args)
    if url.startswith("sqlite"):
        event.listen(created_engine, "connect", _configure_sqlite_connection)
    return created_engine


engine = make_engine()


def sqlite_runtime_status(target_engine: Engine | None = None) -> dict[str, int | str]:
    active_engine = target_engine or engine
    if not str(active_engine.url).startswith("sqlite"):
        return {}
    with active_engine.connect() as connection:
        return {
            "journal_mode": str(connection.exec_driver_sql("PRAGMA journal_mode").scalar_one()).lower(),
            "foreign_keys": int(connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one()),
            "busy_timeout_ms": int(connection.exec_driver_sql("PRAGMA busy_timeout").scalar_one()),
            "synchronous": int(connection.exec_driver_sql("PRAGMA synchronous").scalar_one()),
            "wal_autocheckpoint_pages": int(connection.exec_driver_sql("PRAGMA wal_autocheckpoint").scalar_one()),
            "integrity_check": str(connection.exec_driver_sql("PRAGMA integrity_check").scalar_one()).lower(),
        }


def configure_sqlite_runtime(target_engine: Engine | None = None) -> dict[str, int | str]:
    active_engine = target_engine or engine
    if not str(active_engine.url).startswith("sqlite"):
        return {}
    with active_engine.connect() as connection:
        connection.exec_driver_sql("PRAGMA journal_mode=WAL").scalar_one()
    return sqlite_runtime_status(active_engine)


def checkpoint_sqlite(target_engine: Engine | None = None, *, mode: str = "PASSIVE") -> dict[str, int | str]:
    active_engine = target_engine or engine
    normalized_mode = str(mode or "PASSIVE").strip().upper()
    if normalized_mode not in SQLITE_CHECKPOINT_MODES:
        raise ValueError(f"Unsupported SQLite checkpoint mode: {mode}")
    if not str(active_engine.url).startswith("sqlite"):
        return {"mode": normalized_mode, "busy": 0, "log_frames": 0, "checkpointed_frames": 0}
    with active_engine.connect() as connection:
        busy, log_frames, checkpointed_frames = connection.exec_driver_sql(
            f"PRAGMA wal_checkpoint({normalized_mode})"
        ).one()
    return {
        "mode": normalized_mode,
        "busy": int(busy),
        "log_frames": int(log_frames),
        "checkpointed_frames": int(checkpointed_frames),
    }


def migrate_sqlite_columns(target_engine: Engine | None = None) -> None:
    active_engine = target_engine or engine
    if not str(active_engine.url).startswith("sqlite"):
        return
    inspector = inspect(active_engine)
    table_names = inspector.get_table_names()
    if "messages" in table_names:
        columns = {column["name"] for column in inspector.get_columns("messages")}
        with active_engine.begin() as connection:
            if "thought" not in columns:
                connection.execute(text("ALTER TABLE messages ADD COLUMN thought VARCHAR"))
            if "generation_job_id" not in columns:
                connection.execute(text("ALTER TABLE messages ADD COLUMN generation_job_id VARCHAR"))
            if "reply_index" not in columns:
                connection.execute(text("ALTER TABLE messages ADD COLUMN reply_index INTEGER"))
            connection.execute(text(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_messages_generation_reply "
                "ON messages(generation_job_id, reply_index) "
                "WHERE generation_job_id IS NOT NULL AND reply_index IS NOT NULL"
            ))
            connection.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_messages_generation_job_id ON messages(generation_job_id)"
            ))
    if "message_generation_jobs" in table_names:
        columns = {column["name"] for column in inspector.get_columns("message_generation_jobs")}
        with active_engine.begin() as connection:
            if "lease_owner" not in columns:
                connection.execute(text("ALTER TABLE message_generation_jobs ADD COLUMN lease_owner VARCHAR"))
            if "lease_expires_at" not in columns:
                connection.execute(text("ALTER TABLE message_generation_jobs ADD COLUMN lease_expires_at DATETIME"))
            if "heartbeat_at" not in columns:
                connection.execute(text("ALTER TABLE message_generation_jobs ADD COLUMN heartbeat_at DATETIME"))
            if "attempt_count" not in columns:
                connection.execute(text("ALTER TABLE message_generation_jobs ADD COLUMN attempt_count INTEGER NOT NULL DEFAULT 0"))
            if "cancel_requested_at" not in columns:
                connection.execute(text("ALTER TABLE message_generation_jobs ADD COLUMN cancel_requested_at DATETIME"))
            if "state_version" not in columns:
                connection.execute(text("ALTER TABLE message_generation_jobs ADD COLUMN state_version INTEGER NOT NULL DEFAULT 0"))
            connection.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_message_generation_jobs_lease_expires_at "
                "ON message_generation_jobs(lease_expires_at)"
            ))
            connection.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_message_generation_jobs_heartbeat_at "
                "ON message_generation_jobs(heartbeat_at)"
            ))
            connection.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_message_generation_jobs_cancel_requested_at "
                "ON message_generation_jobs(cancel_requested_at)"
            ))
    if "generation_job_events" in table_names:
        columns = {column["name"] for column in inspector.get_columns("generation_job_events")}
        with active_engine.begin() as connection:
            if "state_version" not in columns:
                connection.execute(text("ALTER TABLE generation_job_events ADD COLUMN state_version INTEGER NOT NULL DEFAULT 0"))
            connection.execute(text("CREATE INDEX IF NOT EXISTS ix_generation_job_events_state_version ON generation_job_events(state_version)"))
            connection.execute(text("CREATE INDEX IF NOT EXISTS ix_generation_job_events_replay ON generation_job_events(job_id, id)"))
    if "post_commit_tasks" in table_names:
        columns = {column["name"] for column in inspector.get_columns("post_commit_tasks")}
        with active_engine.begin() as connection:
            if "available_at" not in columns:
                connection.execute(text("ALTER TABLE post_commit_tasks ADD COLUMN available_at DATETIME"))
                connection.execute(text("UPDATE post_commit_tasks SET available_at = COALESCE(updated_at, created_at, CURRENT_TIMESTAMP) WHERE available_at IS NULL"))
            connection.execute(text("CREATE INDEX IF NOT EXISTS ix_post_commit_tasks_available_at ON post_commit_tasks(available_at)"))
            connection.execute(text("CREATE INDEX IF NOT EXISTS ix_post_commit_tasks_claim ON post_commit_tasks(status, available_at, lease_expires_at)"))
    if "message_assets" in table_names:
        with active_engine.begin() as connection:
            connection.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_message_assets_message_asset ON message_assets(message_id, asset_id)"))
    if "characters" in table_names:
        columns = {column["name"] for column in inspector.get_columns("characters")}
        with active_engine.begin() as connection:
            if "behavior_style" not in columns:
                connection.execute(text("ALTER TABLE characters ADD COLUMN behavior_style VARCHAR"))
            if "appearance" not in columns:
                connection.execute(text("ALTER TABLE characters ADD COLUMN appearance VARCHAR"))
            if "trait_scores" not in columns:
                connection.execute(text("ALTER TABLE characters ADD COLUMN trait_scores JSON"))
            if "tts_provider" not in columns:
                connection.execute(text("ALTER TABLE characters ADD COLUMN tts_provider VARCHAR NOT NULL DEFAULT 'supertonic'"))
                connection.execute(text("UPDATE characters SET tts_provider = 'supertonic' WHERE tts_provider IS NULL OR tts_provider = ''"))
            if "tts_model" not in columns:
                connection.execute(text("ALTER TABLE characters ADD COLUMN tts_model VARCHAR NOT NULL DEFAULT 'supertonic-3'"))
                connection.execute(text("UPDATE characters SET tts_model = 'supertonic-3' WHERE tts_model IS NULL OR tts_model = ''"))
            if "tts_voice_style" not in columns:
                connection.execute(text("ALTER TABLE characters ADD COLUMN tts_voice_style VARCHAR NOT NULL DEFAULT 'F1'"))
                connection.execute(text("UPDATE characters SET tts_voice_style = 'F1' WHERE tts_voice_style IS NULL OR tts_voice_style = ''"))
            if "tts_sample_text" not in columns:
                connection.execute(text("ALTER TABLE characters ADD COLUMN tts_sample_text VARCHAR"))
    if "conversations" in table_names:
        columns = {column["name"] for column in inspector.get_columns("conversations")}
        with active_engine.begin() as connection:
            if "genre_mode" not in columns:
                connection.execute(text("ALTER TABLE conversations ADD COLUMN genre_mode VARCHAR NOT NULL DEFAULT 'battle'"))
                connection.execute(text("UPDATE conversations SET genre_mode = 'battle' WHERE genre_mode IS NULL OR genre_mode = ''"))
            if "tts_enabled" not in columns:
                connection.execute(text("ALTER TABLE conversations ADD COLUMN tts_enabled BOOLEAN NOT NULL DEFAULT 0"))
            if "tts_provider" not in columns:
                connection.execute(text("ALTER TABLE conversations ADD COLUMN tts_provider VARCHAR NOT NULL DEFAULT 'supertonic'"))
                connection.execute(text("UPDATE conversations SET tts_provider = 'supertonic' WHERE tts_provider IS NULL OR tts_provider = ''"))
            if "tts_model" not in columns:
                connection.execute(text("ALTER TABLE conversations ADD COLUMN tts_model VARCHAR NOT NULL DEFAULT 'supertonic-3'"))
                connection.execute(text("UPDATE conversations SET tts_model = 'supertonic-3' WHERE tts_model IS NULL OR tts_model = ''"))
            if "tts_voice_style" not in columns:
                connection.execute(text("ALTER TABLE conversations ADD COLUMN tts_voice_style VARCHAR NOT NULL DEFAULT 'F1'"))
                connection.execute(text("UPDATE conversations SET tts_voice_style = 'F1' WHERE tts_voice_style IS NULL OR tts_voice_style = ''"))
            if "active_command_id" not in columns:
                connection.execute(text("ALTER TABLE conversations ADD COLUMN active_command_id VARCHAR"))
            if "active_command_ids" not in columns:
                connection.execute(text("ALTER TABLE conversations ADD COLUMN active_command_ids JSON"))
                connection.execute(text("UPDATE conversations SET active_command_ids = CASE WHEN active_command_id IS NOT NULL AND active_command_id != '' THEN json_array(active_command_id) ELSE json_array() END"))
            if "thumbnail_url" not in columns:
                connection.execute(text("ALTER TABLE conversations ADD COLUMN thumbnail_url VARCHAR"))
            if "world_setting_id" not in columns:
                connection.execute(text("ALTER TABLE conversations ADD COLUMN world_setting_id VARCHAR"))
    if "world_settings" in table_names:
        columns = {column["name"] for column in inspector.get_columns("world_settings")}
        with active_engine.begin() as connection:
            if "thumbnail_url" not in columns:
                connection.execute(text("ALTER TABLE world_settings ADD COLUMN thumbnail_url VARCHAR"))
    if "chat_commands" in table_names:
        columns = {column["name"] for column in inspector.get_columns("chat_commands")}
        with active_engine.begin() as connection:
            if "generation_prompt" not in columns:
                connection.execute(text("ALTER TABLE chat_commands ADD COLUMN generation_prompt VARCHAR NOT NULL DEFAULT ''"))
                connection.execute(text("UPDATE chat_commands SET generation_prompt = prompt WHERE generation_prompt IS NULL OR generation_prompt = ''"))
            if "postprocess_prompt" not in columns:
                connection.execute(text("ALTER TABLE chat_commands ADD COLUMN postprocess_prompt VARCHAR NOT NULL DEFAULT ''"))
                connection.execute(text("UPDATE chat_commands SET postprocess_prompt = prompt WHERE postprocess_prompt IS NULL OR postprocess_prompt = ''"))
            if "postprocess_target" not in columns:
                connection.execute(text("ALTER TABLE chat_commands ADD COLUMN postprocess_target VARCHAR NOT NULL DEFAULT 'all_bubbles'"))
                connection.execute(text("UPDATE chat_commands SET postprocess_target = 'all_bubbles' WHERE postprocess_target IS NULL OR postprocess_target = ''"))
            if "postprocess_probability" not in columns:
                connection.execute(text("ALTER TABLE chat_commands ADD COLUMN postprocess_probability INTEGER NOT NULL DEFAULT 100"))
                connection.execute(text("UPDATE chat_commands SET postprocess_probability = 100 WHERE postprocess_probability IS NULL"))
            if "postprocess_context_options" not in columns:
                connection.execute(text("ALTER TABLE chat_commands ADD COLUMN postprocess_context_options JSON"))
                connection.execute(text("UPDATE chat_commands SET postprocess_context_options = json_array('scene','world','turn_messages') WHERE postprocess_context_options IS NULL"))
    if "scene_states" in table_names:
        columns = {column["name"] for column in inspector.get_columns("scene_states")}
        with active_engine.begin() as connection:
            if "compression_focus" not in columns:
                connection.execute(text("ALTER TABLE scene_states ADD COLUMN compression_focus VARCHAR"))
            if "world_seed" not in columns:
                connection.execute(text("ALTER TABLE scene_states ADD COLUMN world_seed VARCHAR"))
                connection.execute(text("UPDATE scene_states SET world_seed = current_conflict WHERE world_seed IS NULL AND current_conflict IS NOT NULL"))
            if "opening_scene" not in columns:
                connection.execute(text("ALTER TABLE scene_states ADD COLUMN opening_scene VARCHAR"))
            if "opening_line" not in columns:
                connection.execute(text("ALTER TABLE scene_states ADD COLUMN opening_line VARCHAR"))
            if "tone_preset" not in columns:
                connection.execute(text("ALTER TABLE scene_states ADD COLUMN tone_preset VARCHAR"))
            if "relationship_archetype" not in columns:
                connection.execute(text("ALTER TABLE scene_states ADD COLUMN relationship_archetype VARCHAR"))
            if "last_compression_error" not in columns:
                connection.execute(text("ALTER TABLE scene_states ADD COLUMN last_compression_error VARCHAR"))
            if "last_compression_attempt_at" not in columns:
                connection.execute(text("ALTER TABLE scene_states ADD COLUMN last_compression_attempt_at DATETIME"))
            if "last_compressed_at" not in columns:
                connection.execute(text("ALTER TABLE scene_states ADD COLUMN last_compressed_at DATETIME"))
            if "last_compression_source_message_id" not in columns:
                connection.execute(text("ALTER TABLE scene_states ADD COLUMN last_compression_source_message_id VARCHAR"))
            if "compression_revision" not in columns:
                connection.execute(text("ALTER TABLE scene_states ADD COLUMN compression_revision INTEGER NOT NULL DEFAULT 0"))
            if "user_description" not in columns:
                connection.execute(text("ALTER TABLE scene_states ADD COLUMN user_description VARCHAR"))
                connection.execute(text("""
                    UPDATE scene_states
                    SET
                        user_description = TRIM(SUBSTR(summary, LENGTH('User description:') + 1)),
                        summary = NULL
                    WHERE summary LIKE 'User description:%'
                """))
    if "llm_prompt_snapshots" in table_names:
        columns = {column["name"] for column in inspector.get_columns("llm_prompt_snapshots")}
        if "metadata" not in columns:
            with active_engine.begin() as connection:
                connection.execute(text("ALTER TABLE llm_prompt_snapshots ADD COLUMN metadata JSON"))
                connection.execute(text("UPDATE llm_prompt_snapshots SET metadata = json_object() WHERE metadata IS NULL"))
    if "runtime_settings" in table_names:
        columns = {column["name"] for column in inspector.get_columns("runtime_settings")}
        with active_engine.begin() as connection:
            if "response_length_preset" not in columns:
                connection.execute(text("ALTER TABLE runtime_settings ADD COLUMN response_length_preset VARCHAR NOT NULL DEFAULT 'medium'"))
                connection.execute(text("UPDATE runtime_settings SET response_length_preset = 'medium' WHERE response_length_preset IS NULL OR response_length_preset = ''"))
            if "compression_model_key" not in columns:
                connection.execute(text("ALTER TABLE runtime_settings ADD COLUMN compression_model_key VARCHAR"))
            if "compression_fallback_model_key" not in columns:
                connection.execute(text("ALTER TABLE runtime_settings ADD COLUMN compression_fallback_model_key VARCHAR"))
            if "compression_strategy" not in columns:
                connection.execute(text("ALTER TABLE runtime_settings ADD COLUMN compression_strategy VARCHAR NOT NULL DEFAULT 'quality'"))
                connection.execute(text("UPDATE runtime_settings SET compression_strategy = 'quality' WHERE compression_strategy IS NULL OR compression_strategy NOT IN ('fast', 'quality')"))
            if "fallback_model_key" not in columns:
                connection.execute(text("ALTER TABLE runtime_settings ADD COLUMN fallback_model_key VARCHAR"))
            if "compression_interval_turns" not in columns:
                connection.execute(text("ALTER TABLE runtime_settings ADD COLUMN compression_interval_turns INTEGER NOT NULL DEFAULT 5"))
                connection.execute(text("UPDATE runtime_settings SET compression_interval_turns = 5 WHERE compression_interval_turns IS NULL OR compression_interval_turns < 1"))
            if "default_tts_model_option_key" not in columns:
                connection.execute(text("ALTER TABLE runtime_settings ADD COLUMN default_tts_model_option_key VARCHAR"))
            if "safety_preset" not in columns:
                connection.execute(text("ALTER TABLE runtime_settings ADD COLUMN safety_preset VARCHAR NOT NULL DEFAULT 'medium'"))
                connection.execute(text("UPDATE runtime_settings SET safety_preset = 'medium' WHERE safety_preset IS NULL OR safety_preset = ''"))
    if "model_options" in table_names:
        columns = {column["name"] for column in inspector.get_columns("model_options")}
        with active_engine.begin() as connection:
            if "context_window_tokens" not in columns:
                connection.execute(text("ALTER TABLE model_options ADD COLUMN context_window_tokens INTEGER"))
            if "max_output_tokens" not in columns:
                connection.execute(text("ALTER TABLE model_options ADD COLUMN max_output_tokens INTEGER"))
    if "conversation_relationship_states" in table_names:
        pk_columns = {column["name"] for column in inspector.get_columns("conversation_relationship_states") if column.get("primary_key")}
        if not {"counterpart_type", "counterpart_id"}.issubset(pk_columns):
            with active_engine.begin() as connection:
                connection.execute(text("ALTER TABLE conversation_relationship_states RENAME TO conversation_relationship_states_old"))
                connection.execute(text("""
                    CREATE TABLE conversation_relationship_states (
                        conversation_id VARCHAR NOT NULL,
                        character_id VARCHAR NOT NULL,
                        counterpart_type VARCHAR NOT NULL DEFAULT 'user',
                        counterpart_id VARCHAR NOT NULL DEFAULT 'user_001',
                        trust_level INTEGER NOT NULL,
                        affinity_level INTEGER NOT NULL,
                        tension_level INTEGER NOT NULL,
                        conflict_level INTEGER NOT NULL,
                        cooperation_level INTEGER NOT NULL,
                        current_mood VARCHAR,
                        current_dynamic VARCHAR,
                        unresolved_hooks JSON,
                        updated_at DATETIME NOT NULL,
                        PRIMARY KEY (conversation_id, character_id, counterpart_type, counterpart_id)
                    )
                """))
                connection.execute(text("""
                    INSERT INTO conversation_relationship_states (
                        conversation_id, character_id, counterpart_type, counterpart_id,
                        trust_level, affinity_level, tension_level, conflict_level, cooperation_level,
                        current_mood, current_dynamic, unresolved_hooks, updated_at
                    )
                    SELECT
                        conversation_id,
                        character_id,
                        COALESCE(counterpart_type, 'user'),
                        COALESCE(counterpart_id, 'user_001'),
                        trust_level,
                        affinity_level,
                        tension_level,
                        conflict_level,
                        cooperation_level,
                        current_mood,
                        current_dynamic,
                        unresolved_hooks,
                        updated_at
                    FROM conversation_relationship_states_old
                """))
                connection.execute(text("DROP TABLE conversation_relationship_states_old"))


def init_db() -> None:
    configure_sqlite_runtime(engine)
    SQLModel.metadata.create_all(engine)
    migrate_sqlite_columns()


def get_session() -> Generator[Session, None, None]:
    with Session(engine) as session:
        yield session
