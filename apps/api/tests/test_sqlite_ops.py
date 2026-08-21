from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from app.ops.sqlite_ops import (
    REQUIRED_RUNTIME_TABLES,
    acquire_runtime_lock,
    check_database,
    online_backup,
    release_runtime_lock,
    verify_restore,
)


def build_runtime_database(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA journal_mode=WAL")
        for table in REQUIRED_RUNTIME_TABLES:
            connection.execute(f'CREATE TABLE "{table}" (id TEXT PRIMARY KEY, value TEXT)')
        connection.execute("INSERT INTO conversations (id, value) VALUES ('conv_1', 'preserved')")
        connection.commit()


def test_online_backup_and_restore_verification_preserve_committed_wal_data(tmp_path):
    source = tmp_path / "runtime.db"
    backups = tmp_path / "backups"
    build_runtime_database(source)

    result = online_backup(source, backups, keep=2)

    assert Path(result.backup).is_file()
    assert Path(result.manifest).is_file()
    assert result.integrity == "ok"
    assert result.foreign_key_violations == 0
    assert set(result.required_tables_present) == set(REQUIRED_RUNTIME_TABLES)
    restored = verify_restore(result.backup)
    assert restored.integrity == "ok"
    with sqlite3.connect(result.backup) as connection:
        assert connection.execute("SELECT value FROM conversations WHERE id='conv_1'").fetchone() == ("preserved",)


def test_check_database_rejects_missing_required_tables(tmp_path):
    path = tmp_path / "incomplete.db"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE conversations (id TEXT PRIMARY KEY)")

    with pytest.raises(RuntimeError, match="required tables missing"):
        check_database(path)


def test_runtime_lock_is_exclusive_and_reusable(tmp_path):
    lock_path = tmp_path / "runtime.lock"
    first = acquire_runtime_lock(lock_path)
    try:
        with pytest.raises(RuntimeError, match="already owned"):
            acquire_runtime_lock(lock_path)
    finally:
        release_runtime_lock(first)

    second = acquire_runtime_lock(lock_path)
    release_runtime_lock(second)
