"""Asset DELETE removes only unreferenced private images and cached variants."""
import asyncio
from io import BytesIO
from types import SimpleNamespace

import pytest
from PIL import Image
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlmodel import SQLModel, Session, select

from app.db.session import make_engine, get_session
from app.db.models import Character, CharacterAsset, WorldSetting, MessageAsset
from app.api import assets
from app.services import asset_service, image_derivative_cache as cache


def encoded(color='red'):
    output = BytesIO()
    Image.new('RGB', (600, 400), color).save(output, 'PNG')
    return output.getvalue()


@pytest.fixture
def stored(tmp_path, monkeypatch):
    root = tmp_path / 'uploads'; root.mkdir()
    (root / 'origin.png').write_bytes(encoded())
    (root / 'preview.png').write_bytes(encoded('blue'))
    engine = make_engine('sqlite:///' + str(tmp_path / 'delete.db'))
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(Character(id='c', name='Synthetic', persona='Synthetic'))
        session.add(CharacterAsset(id='a', character_id='c', label='Synthetic',
            image_url='/uploads/origin.png', thumbnail_url='/uploads/preview.png'))
        session.commit()
    before = [asyncio.run(cache.get_derivative(url, cache.DerivativeSpec(), upload_root=root))
              for url in ('/uploads/origin.png', '/uploads/preview.png')]
    monkeypatch.setattr(assets, 'UPLOAD_ROOT', root)
    monkeypatch.setattr(assets, 'get_settings', lambda: SimpleNamespace(shared_upload_root=None))
    yield engine, root, before
    engine.dispose()


def cached_exists(root, image):
    try:
        cache.read_cached(upload_root=root, source_key=image.source_key, filename=image.filename)
        return True
    except (ValueError, OSError):
        return False


def test_invalid_legacy_image_pointer_can_be_deleted_without_filesystem_guessing(stored):
    engine, root, _ = stored
    with Session(engine) as session:
        broken = CharacterAsset(id='broken', character_id='c', label='Synthetic', image_url='invalid-legacy-pointer')
        session.add(broken); session.commit()
        asset_service.delete_asset(session, broken, upload_root=root)
        assert session.get(CharacterAsset, 'broken') is None
    assert (root / 'origin.png').is_file() and (root / 'preview.png').is_file()


def test_media_inventory_does_not_misclassify_private_cache_and_cleanup_controls(stored):
    from app.ops.maintenance import media_inventory
    engine, root, _ = stored
    journal = root / '.image-cleanup'
    journal.mkdir()
    (journal / 'synthetic.json').write_text('{"private":"synthetic"}')
    result = media_inventory(engine.url.database, root)
    assert result['files'] == 2 and result['unreferenced_candidates'] == []
    assert result['deletion_performed'] is False
    assert (journal / 'synthetic.json').is_file()


def test_actual_delete_removes_image_thumbnail_links_and_both_cache_namespaces(stored):
    engine, root, before = stored
    app = FastAPI(); app.include_router(assets.router)
    def sessions():
        with Session(engine) as session:
            yield session
    app.dependency_overrides[get_session] = sessions
    with TestClient(app) as client:
        response = client.delete('/character-assets/a')
        assert response.status_code == 204
    with Session(engine) as session:
        assert session.get(CharacterAsset, 'a') is None
        assert session.exec(select(MessageAsset)).all() == []
    assert not (root / 'origin.png').exists() and not (root / 'preview.png').exists()
    assert all(not cached_exists(root, image) for image in before)
    with pytest.raises((ValueError, OSError)):
        asyncio.run(cache.get_derivative('/uploads/origin.png', cache.DerivativeSpec(), upload_root=root))


@pytest.mark.parametrize('reference', ['avatar', 'asset', 'world', 'json'])
def test_live_references_preserve_original_and_its_cache_but_remove_unique_thumbnail(stored, reference):
    engine, root, before = stored
    with Session(engine) as session:
        if reference == 'avatar':
            character = session.get(Character, 'c'); character.avatar_url = 'http://synthetic.invalid/uploads/origin.png'; session.add(character)
        elif reference == 'world':
            session.add(WorldSetting(id='world', title='Synthetic', thumbnail_url='/uploads/origin.png'))
        else:
            session.add(CharacterAsset(id='other', character_id='c', label='Other',
                image_url='/uploads/origin.png' if reference == 'asset' else '/uploads/other.png',
                metadata_={'nested': {'image': '/uploads/origin.png'}} if reference == 'json' else {}))
        session.commit()
        asset_service.delete_asset(session, session.get(CharacterAsset, 'a'), upload_root=root)
    assert (root / 'origin.png').is_file() and cached_exists(root, before[0])
    assert not (root / 'preview.png').exists() and not cached_exists(root, before[1])


@pytest.mark.parametrize('reference', ['avatar', 'json'])
def test_percent_encoded_alias_reference_preserves_same_owned_source(stored, reference):
    engine, root, before = stored
    with Session(engine) as session:
        if reference == 'avatar':
            character = session.get(Character, 'c')
            character.avatar_url = '/uploads/%6frigin.png'
            session.add(character)
        else:
            session.add(CharacterAsset(id='other', character_id='c', label='Other',
                image_url='/uploads/other.png', metadata_={'image': '/uploads/%6frigin.png'}))
        session.commit()
        asset_service.delete_asset(session, session.get(CharacterAsset, 'a'), upload_root=root)
    assert (root / 'origin.png').is_file() and cached_exists(root, before[0])
    assert not (root / 'preview.png').exists() and not cached_exists(root, before[1])


def test_shared_thumbnail_is_not_deleted(stored):
    engine, root, before = stored
    with Session(engine) as session:
        session.add(CharacterAsset(id='other', character_id='c', label='Other',
            image_url='/uploads/other.png', thumbnail_url='/uploads/preview.png')); session.commit()
        asset_service.delete_asset(session, session.get(CharacterAsset, 'a'), upload_root=root)
    assert not (root / 'origin.png').exists() and not cached_exists(root, before[0])
    assert (root / 'preview.png').is_file() and cached_exists(root, before[1])


def test_failed_database_delete_never_removes_files_or_cache(stored, monkeypatch):
    engine, root, before = stored
    with Session(engine) as session:
        asset = session.get(CharacterAsset, 'a')
        def fail():
            raise RuntimeError('Synthetic database failure')
        monkeypatch.setattr(session, 'commit', fail)
        with pytest.raises(RuntimeError, match='Synthetic database failure'):
            asset_service.delete_asset(session, asset, upload_root=root)
        session.rollback()
    assert (root / 'origin.png').is_file() and (root / 'preview.png').is_file()
    assert all(cached_exists(root, image) for image in before)


def test_reference_committed_after_domain_delete_before_cleanup_is_preserved(stored, monkeypatch):
    engine, root, before = stored
    from app.services import image_reference_cleanup as cleanup
    original = cleanup.cleanup_deleted_asset_images
    def late_reference(*args, **kwargs):
        with Session(engine) as writer:
            writer.add(WorldSetting(id='new-world', title='Synthetic', thumbnail_url='/uploads/origin.png')); writer.commit()
        return original(*args, **kwargs)
    monkeypatch.setattr(cleanup, 'cleanup_deleted_asset_images', late_reference)
    with Session(engine) as session:
        asset_service.delete_asset(session, session.get(CharacterAsset, 'a'), upload_root=root)
    assert (root / 'origin.png').is_file() and cached_exists(root, before[0])
    assert not (root / 'preview.png').exists()


def test_no_shared_root_writes_or_deletion(tmp_path):
    private = tmp_path / 'private'; private.mkdir()
    shared = tmp_path / 'shared'; shared.mkdir()
    source = encoded(); (shared / 'shared.png').write_bytes(source)
    image = asyncio.run(cache.get_derivative('/uploads/shared.png', cache.DerivativeSpec(),
        upload_root=private, shared_root=str(shared)))
    engine = make_engine('sqlite:///' + str(tmp_path / 'shared.db')); SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(Character(id='c', name='Synthetic', persona='Synthetic'))
        session.add(CharacterAsset(id='a', character_id='c', label='Synthetic', image_url='/uploads/shared.png')); session.commit()
        asset_service.delete_asset(session, session.get(CharacterAsset, 'a'), upload_root=private, shared_root=str(shared))
    assert (shared / 'shared.png').read_bytes() == source
    assert not cached_exists(private, image)
    assert sorted(p.name for p in shared.iterdir()) == ['shared.png']
    engine.dispose()


def test_reference_writer_cannot_overtake_fresh_check_and_file_unlink(stored, monkeypatch):
    import sqlite3
    from sqlalchemy import create_engine
    from sqlalchemy.exc import OperationalError
    from app.services import image_reference_cleanup as cleanup
    engine, root, _ = stored
    other = create_engine(str(engine.url), connect_args={'timeout': .05})
    original = cleanup._has_reference
    blocked = []
    def race(session, source):
        result = original(session, source)
        if source == '/uploads/origin.png':
            assert session.connection().connection.driver_connection.in_transaction
            with Session(other) as writer:
                writer.add(WorldSetting(id='late', title='Synthetic', thumbnail_url=source))
                with pytest.raises(OperationalError) as error:
                    writer.commit()
                assert error.value.orig.sqlite_errorcode == sqlite3.SQLITE_BUSY
                blocked.append(True)
                writer.rollback()
        return result
    monkeypatch.setattr(cleanup, '_has_reference', race)
    with Session(engine) as session:
        asset_service.delete_asset(session, session.get(CharacterAsset, 'a'), upload_root=root)
    assert blocked == [True] and not (root / 'origin.png').exists()
    other.dispose()


def test_inflight_producer_finishes_before_delete_purge_and_cannot_republish_deleted_source(stored, monkeypatch):
    import threading
    from app.services import asset_image_service
    engine, root, _ = stored
    # Force a new variant miss, so the producer owns the real source lock.
    spec = cache.DerivativeSpec(max_edge=333)
    entered = threading.Event(); release = threading.Event()
    original = asset_image_service.transform_image
    async def paused(*args, **kwargs):
        entered.set()
        assert await asyncio.to_thread(release.wait, 3)
        return await original(*args, **kwargs)
    monkeypatch.setattr(cache, 'transform_image', paused)
    def remove():
        with Session(engine) as session:
            asset_service.delete_asset(session, session.get(CharacterAsset, 'a'), upload_root=root)
    async def run():
        producer = asyncio.create_task(cache.get_derivative('/uploads/origin.png', spec, upload_root=root))
        assert await asyncio.to_thread(entered.wait, 3)
        deletion = asyncio.create_task(asyncio.to_thread(remove))
        await asyncio.sleep(.05)
        assert not deletion.done()
        release.set()
        output = await producer
        await deletion
        return output
    image = asyncio.run(run())
    assert not (root / 'origin.png').exists() and not cached_exists(root, image)
    with pytest.raises((ValueError, OSError)):
        asyncio.run(cache.get_derivative('/uploads/origin.png', spec, upload_root=root))
