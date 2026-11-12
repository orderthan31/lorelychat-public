"""Synthetic file-SQLite proofs for durable image cleanup intents."""
import os

import pytest
from sqlmodel import SQLModel, Session

from app.db.models import Character, CharacterAsset
from app.db.session import make_engine
from app.services import asset_service, image_reference_cleanup as cleanup
from app.services import image_cleanup_journal as journal


@pytest.fixture
def stored(tmp_path):
    root = tmp_path / 'uploads'
    root.mkdir()
    (root / 'image.png').write_bytes(b'synthetic')
    engine = make_engine('sqlite:///' + str(tmp_path / 'journal.db'))
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(Character(id='c', name='Synthetic', persona='Synthetic'))
        session.add(CharacterAsset(id='a', character_id='c', label='Synthetic', image_url='/uploads/image.png'))
        session.commit()
    yield engine, root
    engine.dispose()


def stage(root):
    ticket = journal.stage_cleanup('a', 'c', ['/uploads/image.png'], upload_root=root)
    assert ticket is not None
    return ticket


def tickets(root):
    return list((root / '.image-cleanup').glob('*.json'))


def remove_row(engine):
    with Session(engine) as session:
        session.delete(session.get(CharacterAsset, 'a'))
        session.commit()


def test_crash_gap_replayed_after_committed_delete(stored):
    engine, root = stored
    ticket = stage(root)
    remove_row(engine)
    ticket.release()  # Equivalent to process death closing the locked descriptor.
    assert journal.replay_cleanup(engine, upload_root=root) == 1
    assert not (root / 'image.png').exists()
    assert not tickets(root)


def test_live_ticket_skipped_before_commit_and_after_commit(stored):
    engine, root = stored
    ticket = stage(root)
    try:
        assert journal.replay_cleanup(engine, upload_root=root) == 0
        assert len(tickets(root)) == 1
        remove_row(engine)
        assert journal.replay_cleanup(engine, upload_root=root) == 0
        assert (root / 'image.png').exists()
    finally:
        ticket.release()
    assert journal.replay_cleanup(engine, upload_root=root) == 1


def test_aborted_commit_discards_intent_preserves_original(stored, monkeypatch):
    engine, root = stored
    with Session(engine) as session:
        def fail():
            raise RuntimeError('synthetic commit failure')
        monkeypatch.setattr(session, 'commit', fail)
        with pytest.raises(RuntimeError, match='synthetic commit failure'):
            asset_service.delete_asset(session, session.get(CharacterAsset, 'a'), upload_root=root)
        session.rollback()
    assert len(tickets(root)) == 1
    assert journal.replay_cleanup(engine, upload_root=root) == 1
    assert (root / 'image.png').read_bytes() == b'synthetic'
    assert not tickets(root)


def test_deferred_unlink_retries_on_replay(stored, monkeypatch, caplog):
    engine, root = stored
    original = cleanup._unlink_private
    def fail(*args, **kwargs):
        raise OSError('private synthetic path must not be logged')
    monkeypatch.setattr(cleanup, '_unlink_private', fail)
    with Session(engine) as session:
        asset_service.delete_asset(session, session.get(CharacterAsset, 'a'), upload_root=root)
    assert len(tickets(root)) == 1
    assert (root / 'image.png').exists()
    assert 'private synthetic path' not in caplog.text
    assert '/uploads/image.png' not in caplog.text
    monkeypatch.setattr(cleanup, '_unlink_private', original)
    assert journal.replay_cleanup(engine, upload_root=root) == 1
    assert not tickets(root)
    assert not (root / 'image.png').exists()


def test_missing_inputs_do_not_create_journal(tmp_path):
    root = tmp_path / 'absent'
    assert journal.stage_cleanup('a', 'c', ['/uploads/missing'], upload_root=root) is None
    assert not root.exists()


def test_staging_failure_prevents_domain_delete(stored, monkeypatch):
    engine, root = stored
    monkeypatch.setattr(journal, 'MAX_TICKETS', 0)
    with Session(engine) as session:
        with pytest.raises(RuntimeError, match='intent'):
            asset_service.delete_asset(session, session.get(CharacterAsset, 'a'), upload_root=root)
        session.rollback()
        assert session.get(CharacterAsset, 'a') is not None
    assert (root / 'image.png').exists()


def test_original_asset_guard_inside_write_boundary(stored, monkeypatch):
    engine, root = stored
    def forbidden(*args):
        pytest.fail('original asset must stop cleanup before reference lookup')
    monkeypatch.setattr(cleanup, '_has_reference', forbidden)
    result = cleanup.cleanup_deleted_asset_images(engine, 'c', ['/uploads/image.png'],
        upload_root=root, deleted_asset_id='a')
    assert result['deferred'] == 0
    assert (root / 'image.png').exists()


def test_invalid_symlink_hardlink_oversized_tickets_never_processed(stored):
    engine, root = stored
    ticket = stage(root)
    ticket.release()
    path = tickets(root)[0]
    assert path.stat().st_mode & 0o777 == 0o600
    directory = path.parent
    os.link(path, directory / ('1' * 32 + '.json'))
    (directory / ('2' * 32 + '.json')).symlink_to(path)
    (directory / ('3' * 32 + '.json')).write_bytes(b'x' * (journal.MAX_BYTES + 1))
    (directory / ('4' * 32 + '.json')).write_text('{}')
    remove_row(engine)
    assert journal.replay_cleanup(engine, upload_root=root) == 0
    assert (root / 'image.png').exists()


def test_shared_overlap_fails_before_write(stored):
    engine, root = stored
    before = sorted(root.iterdir())
    with Session(engine) as session:
        with pytest.raises(RuntimeError, match='intent'):
            asset_service.delete_asset(session, session.get(CharacterAsset, 'a'), upload_root=root, shared_root=str(root))
        session.rollback()
        assert session.get(CharacterAsset, 'a') is not None
    assert sorted(root.iterdir()) == before


@pytest.mark.parametrize('url', ['/uploads/image.png', 'http://localhost:8123/uploads/image.png',
                                 'https://127.0.0.1/uploads/image.png', 'http://[::1]/uploads/image.png'])
def test_owned_relative_and_loopback_sources_removed(stored, url):
    engine, root = stored
    with Session(engine) as session:
        asset = session.get(CharacterAsset, 'a')
        asset.image_url = url
        session.add(asset)
        session.commit()
        asset_service.delete_asset(session, asset, upload_root=root)
    assert not (root / 'image.png').exists()


def test_remote_upload_collision_only_purges_remote_cache(stored):
    from app.services.image_derivative_cache import source_key_for
    engine, root = stored
    url = 'https://public.example/uploads/image.png'
    directory = root / '.image-cache' / 'v1' / source_key_for(url)
    directory.mkdir(parents=True)
    variant = directory / ('a' * 64 + '.webp')
    variant.write_bytes(b'synthetic derivative')
    with Session(engine) as session:
        asset = session.get(CharacterAsset, 'a')
        asset.image_url = url
        session.add(asset)
        session.commit()
        asset_service.delete_asset(session, asset, upload_root=root)
    assert (root / 'image.png').read_bytes() == b'synthetic'
    assert not variant.exists()
    assert not tickets(root)


@pytest.mark.parametrize('url', ['https://public.example/uploads/image.png',
    'http://user@localhost/uploads/image.png', 'http://localhost/uploads/image.png?q=1',
    '//localhost/uploads/image.png', 'http://localhost/uploads/image.png#fragment'])
def test_unowned_urls_never_map_to_private_file(url):
    assert cleanup.local_source(url) is None


def test_next_delete_retries_at_most_one_older_ticket(stored):
    engine, root = stored
    older = [stage(root) for _ in range(3)]
    for ticket in older:
        ticket.release()
    with Session(engine) as session:
        asset_service.delete_asset(session, session.get(CharacterAsset, 'a'), upload_root=root)
    assert len(tickets(root)) == 2


def test_replay_limit(stored):
    engine, root = stored
    older = [stage(root) for _ in range(3)]
    for ticket in older:
        ticket.release()
    remove_row(engine)
    assert journal.replay_cleanup(engine, upload_root=root, limit=1) == 1
    assert len(tickets(root)) == 2


def test_startup_replay_runs_after_init_in_worker_thread(stored, monkeypatch):
    import asyncio
    import threading
    from types import SimpleNamespace
    from fastapi import FastAPI
    from app import main
    engine, root = stored
    ticket = stage(root)
    remove_row(engine)
    ticket.release()
    initialized = []
    thread_ids = []
    original = journal.replay_cleanup
    def replay(*args, **kwargs):
        assert initialized == [engine]
        assert kwargs['limit'] == 16
        thread_ids.append(threading.get_ident())
        return original(*args, **kwargs)
    monkeypatch.setattr(main, 'get_settings', lambda: SimpleNamespace(
        llm_mock=True, upload_root=str(root), shared_upload_root=None))
    monkeypatch.setattr(main, 'init_db', lambda bind: initialized.append(bind))
    monkeypatch.setattr(journal, 'replay_cleanup', replay)
    app = FastAPI()
    app.state.engine = engine
    app.state.mock_execution = False
    async def run():
        async with main.lifespan(app):
            assert app.state.ready
    asyncio.run(run())
    assert thread_ids and thread_ids[0] != threading.get_ident()
    assert not tickets(root)
    assert not (root / 'image.png').exists()
