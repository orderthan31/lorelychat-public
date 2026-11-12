"""Expected-correct source-admission identity proof; synthetic files and SQLite."""
import asyncio
from io import BytesIO

import pytest
from PIL import Image
from sqlmodel import SQLModel, Session

from app.db.models import Character, CharacterAsset
from app.db.session import make_engine
from app.services import asset_service, image_derivative_cache as cache


def test_rejected_utf8_uri_cannot_own_replacement_character_original(tmp_path):
    root = tmp_path / 'uploads'
    root.mkdir()
    unrelated = root / '\ufffd.png'
    out = BytesIO()
    Image.new('RGB', (16, 16), 'blue').save(out, 'PNG')
    unrelated.write_bytes(out.getvalue())
    invalid_source = '/uploads/%ff.png'
    valid_source = '/uploads/%EF%BF%BD.png'
    with pytest.raises(ValueError):
        cache.source_key_for(invalid_source)
    derivative = asyncio.run(cache.get_derivative(valid_source, cache.DerivativeSpec(), upload_root=root))
    engine = make_engine('sqlite:///' + str(tmp_path / 'identity.db'))
    SQLModel.metadata.create_all(engine)
    try:
        with Session(engine) as session:
            session.add(Character(id='c', name='Synthetic', persona='Synthetic'))
            session.add(CharacterAsset(id='a', character_id='c', label='Synthetic', image_url=invalid_source))
            session.commit()
            asset_service.delete_asset(session, session.get(CharacterAsset, 'a'), upload_root=root)
            assert session.get(CharacterAsset, 'a') is None
        try:
            cache.read_cached(root, derivative.source_key, derivative.filename)
            cached = True
        except ValueError:
            cached = False
        print({'unrelated_original_exists': unrelated.exists(), 'unrelated_derivative_exists': cached})
        assert unrelated.is_file(), 'Rejected URI acquired ownership of unrelated replacement-character original'
        assert cached, 'Rejected URI purged unrelated source derivative'
    finally:
        engine.dispose()
