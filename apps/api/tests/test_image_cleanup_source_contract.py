"""Cleanup shares producer admission before any canonical URI conversion."""
import asyncio
from io import BytesIO

import pytest
from PIL import Image
from sqlmodel import SQLModel, Session

from app.db.models import Character, CharacterAsset
from app.db.session import make_engine
from app.services import asset_service, image_derivative_cache as cache
from app.services.image_reference_cleanup import local_source


@pytest.mark.parametrize('source', [
    '/uploads/%ff.png', '/uploads/%c0%af.png', '/uploads/%ed%a0%80.png',
    '/uploads/%e2%82.png', '/uploads/%00.png', '/uploads/%0d.png',
    '/uploads/%7f.png', '/uploads/%5c.png', '/uploads/%2e%2e/a.png',
    '/uploads/.image-cache/v1/private.png', '/uploads/a.png?x=1',
])
def test_producer_rejected_local_sources_never_gain_cleanup_ownership(source):
    with pytest.raises(ValueError):
        cache.source_key_for(source)
    assert local_source(source) is None


@pytest.mark.parametrize('source', [
    '/uploads/%EF%BF%BD.png', '/uploads/100%25.png', '/uploads/a%20b.png',
    '/uploads/%ED%95%9C%EA%B8%80.png', '/uploads/a%252fb.png',
    '/uploads/%256frigin.png', '/uploads/%6frigin.png', '/uploads/%25ff.png',
])
def test_valid_local_identity_and_canonicalization_are_idempotent(source):
    canonical = local_source(source)
    assert canonical is not None and local_source(canonical) == canonical
    assert cache.source_key_for(canonical) == cache.source_key_for(source)


def test_valid_replacement_character_filename_can_still_be_reclaimed(tmp_path):
    root = tmp_path / 'uploads'
    root.mkdir()
    target = root / '\ufffd.png'
    output = BytesIO()
    Image.new('RGB', (16, 16), 'blue').save(output, 'PNG')
    target.write_bytes(output.getvalue())
    source = '/uploads/%EF%BF%BD.png'
    derivative = asyncio.run(cache.get_derivative(source, cache.DerivativeSpec(), upload_root=root))
    engine = make_engine('sqlite:///' + str(tmp_path / 'valid.db'))
    SQLModel.metadata.create_all(engine)
    try:
        with Session(engine) as session:
            session.add(Character(id='c', name='Synthetic', persona='Synthetic'))
            session.add(CharacterAsset(id='a', character_id='c', label='Synthetic', image_url=source))
            session.commit()
            asset_service.delete_asset(session, session.get(CharacterAsset, 'a'), upload_root=root)
        assert not target.exists()
        with pytest.raises(ValueError):
            cache.read_cached(root, derivative.source_key, derivative.filename)
    finally:
        engine.dispose()
