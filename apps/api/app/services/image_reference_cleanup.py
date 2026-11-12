"""Post-commit, reference-safe removal of private asset images and derivatives."""
from __future__ import annotations

from contextlib import ExitStack
import json
import logging
import os
from pathlib import Path
import stat
import time
from urllib.parse import quote, unquote, urlsplit

from sqlalchemy import inspect, text, update
from sqlmodel import Session

from app.db.models import Character, CharacterAsset
from app.services.asset_image_service import _directory


# Immutable forensic records are not live image ownership. Deleting an asset
# intentionally removes its MessageAsset links; historical logs do not resurrect it.
_FORENSIC_TABLES = {'llm_prompt_snapshots', 'llm_usage_events', 'generation_job_events'}
_LOG = logging.getLogger(__name__)


def local_source(value: str) -> str | None:
    try:
        if not isinstance(value, str) or '\\' in value or any(ord(char) < 32 or ord(char) == 127 for char in value):
            return None
        parsed = urlsplit(value)
        parsed.port  # Invalid legacy ports never establish private ownership.
        # Reuse the producer's admission BEFORE any canonical re-quoting; a
        # rejected byte sequence must not acquire a replacement filename.
        from app.services.image_derivative_cache import source_key_for
        admitted_key = source_key_for(parsed.path)
        path = unquote(parsed.path, errors='strict')
        parts = path.removeprefix('/uploads/').split('/')
        if not path.startswith('/uploads/') or parsed.query or parsed.fragment:
            return None
        if parsed.scheme or parsed.netloc:
            if (parsed.scheme not in ('http', 'https') or parsed.username is not None
                    or parsed.password is not None or parsed.hostname not in ('localhost', '127.0.0.1', '::1')):
                return None
        if any(part in ('', '.', '..', '.image-cache', '.image-cleanup') or '\\' in part or '\x00' in part for part in parts):
            return None
        # Keep this value URI-shaped and idempotent. Decoded filesystem text
        # must never be fed to the producer's single-decode URL parser again.
        canonical = quote(path, safe='/')
        if source_key_for(canonical) != admitted_key:
            return None
        return canonical
    except (TypeError, ValueError):
        return None


def _has_reference(session: Session, source: str) -> bool:
    """Conservative SQL-side lookup: never return/log private row contents.

    Basename matching intentionally preserves ambiguous references rather than
    deleting a file used inside an authored JSON/Markdown field or encoded URL.
    """
    parsed = urlsplit(source)
    name = unquote(parsed.path).rsplit('/', 1)[-1]
    needles = {source, quote(source, safe=''), source.replace('/', '\\/')}
    if name:
        needles.update((name, quote(name, safe=''), json.dumps(name, ensure_ascii=True)[1:-1]))
    needles.discard('')
    params = {'n' + str(i): value for i, value in enumerate(sorted(needles))}
    connection = session.connection()
    inspector = inspect(connection)
    def quoted(name):
        return '"' + name.replace('"', '""') + '"'
    # Raw substring checks remain fast and conservative. Also recognize one
    # percent-encoding layer in authored URLs/JSON; otherwise an alias such as
    # %6frigin.png can lose a still-live reference to origin.png.
    driver = connection.connection.driver_connection
    function = 'lorechat_image_reference_once'
    reference_needles = tuple(needles | {unquote(source)})
    def encoded_reference(value):
        if not isinstance(value, str) or '%' not in value:
            return 0
        decoded = unquote(value)
        return int(any(needle in decoded for needle in reference_needles))
    driver.create_function(function, 1, encoded_reference, deterministic=True)
    try:
        for table in inspector.get_table_names():
            if table.startswith('sqlite_') or table in _FORENSIC_TABLES:
                continue
            columns = [column['name'] for column in inspector.get_columns(table)
                       if any(kind in str(column['type']).upper() for kind in ('TEXT', 'CHAR', 'JSON'))]
            if not columns:
                continue
            expressions = ['CAST(' + quoted(column) + ' AS TEXT)' for column in columns]
            predicates = ['instr(' + value + ', :' + key + ') > 0'
                          for value in expressions for key in params]
            predicates += ["(instr(" + value + ", '%') > 0 AND " + function + '(' + value + ') = 1)'
                           for value in expressions]
            sql = 'SELECT 1 FROM ' + quoted(table) + ' WHERE ' + ' OR '.join(predicates) + ' LIMIT 1'
            if session.execute(text(sql), params).first() is not None:
                return True
        return False
    finally:
        # Pool reuse must not retain a closure over a prior private source.
        driver.create_function(function, 1, None)


def _unlink_private(root: Path, source: str, shared_root: str | None = None) -> bool:
    path = local_source(source)
    if path is None:
        return False
    parts = unquote(path).removeprefix('/uploads/').split('/')
    if shared_root and (root.joinpath(*parts).resolve()).is_relative_to(Path(shared_root).expanduser().resolve()):
        return False
    directory = None
    try:
        directory = _directory(root, parts[:-1])
        info = os.stat(parts[-1], dir_fd=directory, follow_symlinks=False)
        if not stat.S_ISREG(info.st_mode):
            return False
        os.unlink(parts[-1], dir_fd=directory)
        return True
    except FileNotFoundError:
        # Shared sources are never deleted: only a private directory FD is used.
        return False
    finally:
        if directory is not None:
            os.close(directory)


def cleanup_sources(root: Path, urls: list[str]) -> list[str]:
    from app.services.image_derivative_cache import source_key_for
    sources = {}
    for url in urls:
        if not isinstance(url, str) or not url:
            continue
        source = local_source(url) or url
        try:
            key = source_key_for(source)
        except (ValueError, TypeError, UnicodeError):
            # An invalid legacy pointer never established an owned cache/file;
            # removing its row must not guess a filesystem target or fail closed
            # as though a real cleanup intent had been lost.
            continue
        local = local_source(source)
        if ((root / '.image-cache' / 'v1' / key).exists()
                or (local is not None and (root / unquote(local).removeprefix('/uploads/')).exists())):
            sources[key] = source
    # Deterministic ordering prevents lock-order inversions between two deletes.
    return [sources[key] for key in sorted(sources)]


def cleanup_deleted_asset_images(bind, character_id: str, urls: list[str], *, upload_root: Path,
                                 shared_root: str | None = None, deleted_asset_id: str | None = None) -> dict:
    from app.services.image_derivative_cache import source_lock, purge_source_locked
    root = Path(upload_root).expanduser().resolve()
    if shared_root and root.is_relative_to(Path(shared_root).expanduser().resolve()):
        return {'removed': 0, 'protected': 0, 'deferred': 1}
    sources = cleanup_sources(root, urls)
    result = {'removed': 0, 'protected': 0, 'deferred': 0}
    if not sources and deleted_asset_id is None:
        return result
    try:
        with ExitStack() as locks:
            for source in sources:
                locks.enter_context(source_lock(root, source, timeout=5.0))
            with Session(bind) as session:
                # A real SQLite write boundary protects the reference check and
                # unlink from supported concurrent commits, not merely ORM cache.
                session.execute(update(Character).where(Character.id == character_id)
                                .values(updated_at=Character.updated_at)
                                .execution_options(synchronize_session=False))
                connection = session.connection().connection.driver_connection
                deadline = time.monotonic() + 5.0
                if hasattr(connection, 'set_progress_handler'):
                    connection.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
                try:
                    # Must be checked inside this fresh physical write fence:
                    # an unlocked ticket may represent a rolled-back/unknown commit.
                    if deleted_asset_id is not None and session.get(CharacterAsset, deleted_asset_id) is not None:
                        result['protected'] += len(sources)
                        return result
                    for source in sources:
                        if _has_reference(session, source):
                            result['protected'] += 1
                            continue
                        result['removed'] += int(_unlink_private(root, source, shared_root))
                        purge_source_locked(root, source)
                    session.commit()
                finally:
                    if hasattr(connection, 'set_progress_handler'):
                        connection.set_progress_handler(None, 0)
    except Exception:
        # Domain deletion has already committed. Unknown/unsafe filesystem or
        # reference state must retain remaining bytes, never undo rows or guess.
        result['deferred'] += 1
        _LOG.warning('Asset image cleanup deferred; remaining bytes retained')
    return result
