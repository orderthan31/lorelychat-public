from __future__ import annotations

import fcntl
import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO


REQUIRED_RUNTIME_TABLES = (
    "conversations",
    "messages",
    "message_generation_jobs",
    "post_commit_tasks",
    "runtime_settings",
)


@dataclass(frozen=True)
class DatabaseCheck:
    path: str
    integrity: str
    foreign_key_violations: int
    required_tables_present: list[str]
    bytes: int
    sha256: str


@dataclass(frozen=True)
class BackupResult:
    source: str
    backup: str
    manifest: str
    created_at: str
    bytes: int
    sha256: str
    integrity: str
    foreign_key_violations: int
    required_tables_present: list[str]


def file_sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def check_database(
    path: str | Path,
    *,
    full: bool = False,
    required_tables: tuple[str, ...] = REQUIRED_RUNTIME_TABLES,
) -> DatabaseCheck:
    db_path = Path(path).resolve()
    if not db_path.is_file():
        raise FileNotFoundError(db_path)
    query = "PRAGMA integrity_check" if full else "PRAGMA quick_check"
    uri = f"file:{db_path}?mode=ro"
    with sqlite3.connect(uri, uri=True, timeout=10.0) as connection:
        integrity_rows = [str(row[0]) for row in connection.execute(query).fetchall()]
        integrity = "ok" if integrity_rows == ["ok"] else "; ".join(integrity_rows[:10])
        foreign_key_violations = len(connection.execute("PRAGMA foreign_key_check").fetchall())
        table_names = {
            str(row[0])
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
    present = [name for name in required_tables if name in table_names]
    missing = [name for name in required_tables if name not in table_names]
    if integrity != "ok":
        raise RuntimeError(f"SQLite integrity check failed: {integrity}")
    if foreign_key_violations:
        raise RuntimeError(f"SQLite foreign key check failed: {foreign_key_violations} violation(s)")
    if missing:
        raise RuntimeError(f"SQLite required tables missing: {', '.join(missing)}")
    return DatabaseCheck(
        path=str(db_path),
        integrity=integrity,
        foreign_key_violations=foreign_key_violations,
        required_tables_present=present,
        bytes=db_path.stat().st_size,
        sha256=file_sha256(db_path),
    )


def online_backup(
    source: str | Path,
    output_dir: str | Path,
    *,
    keep: int = 14,
    full_check: bool = True,
) -> BackupResult:
    source_path = Path(source).resolve()
    destination_dir = Path(output_dir).resolve()
    destination_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    destination = destination_dir / f"lorechat-{timestamp}.db"
    partial = destination.with_suffix(".db.partial")
    if destination.exists() or partial.exists():
        raise FileExistsError(destination)

    source_uri = f"file:{source_path}?mode=ro"
    try:
        with sqlite3.connect(source_uri, uri=True, timeout=30.0) as source_connection:
            with sqlite3.connect(partial) as destination_connection:
                source_connection.backup(destination_connection, pages=1000, sleep=0.05)
                destination_connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        check = check_database(partial, full=full_check)
        os.replace(partial, destination)
        directory_fd = os.open(destination_dir, os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except Exception:
        partial.unlink(missing_ok=True)
        raise

    created_at = datetime.now(timezone.utc).isoformat()
    result = BackupResult(
        source=str(source_path),
        backup=str(destination),
        manifest=str(destination.with_suffix(".json")),
        created_at=created_at,
        bytes=destination.stat().st_size,
        sha256=file_sha256(destination),
        integrity=check.integrity,
        foreign_key_violations=check.foreign_key_violations,
        required_tables_present=check.required_tables_present,
    )
    manifest_path = Path(result.manifest)
    manifest_path.write_text(json.dumps(asdict(result), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _prune_backups(destination_dir, keep=max(1, keep))
    return result


def verify_restore(backup: str | Path) -> DatabaseCheck:
    backup_path = Path(backup).resolve()
    with tempfile.TemporaryDirectory(prefix="lorechat-restore-") as temp_dir:
        restored = Path(temp_dir) / "restored.db"
        shutil.copy2(backup_path, restored)
        with sqlite3.connect(restored, timeout=10.0) as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.rollback()
        return check_database(restored, full=True)


def _prune_backups(directory: Path, *, keep: int) -> None:
    backups = sorted(directory.glob("lorechat-*.db"), key=lambda path: path.stat().st_mtime, reverse=True)
    for stale in backups[keep:]:
        stale.unlink(missing_ok=True)
        stale.with_suffix(".json").unlink(missing_ok=True)


def acquire_runtime_lock(path: str | Path) -> BinaryIO:
    lock_path = Path(path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = lock_path.open("a+b")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        handle.close()
        raise RuntimeError(f"Lorechat runtime is already owned: {lock_path}") from exc
    handle.seek(0)
    handle.truncate()
    handle.write(f"{os.getpid()}\n".encode("ascii"))
    handle.flush()
    os.fsync(handle.fileno())
    return handle


def release_runtime_lock(handle: BinaryIO | None) -> None:
    if handle is None or handle.closed:
        return
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    finally:
        handle.close()
