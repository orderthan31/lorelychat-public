"""Expected-correct cleanup identity regression; no product patches or mocks."""
import asyncio
from io import BytesIO

import pytest
from PIL import Image
from sqlmodel import SQLModel, Session

from app.db.models import Character, CharacterAsset
from app.db.session import make_engine
from app.services import asset_service, image_derivative_cache as cache
from app.services.asset_image_service import read_local_image


def png(color):
    out = BytesIO()
    Image.new('RGB', (16, 16), color).save(out, 'PNG')
    return out.getvalue()


@pytest.mark.parametrize('literal_source_exists', [False, True])
def test_double_encoded_source_never_unlinks_unrelated_sibling(tmp_path, literal_source_exists):
    root = tmp_path / 'uploads'
    root.mkdir()
    unrelated = root / 'origin.png'
    unrelated.write_bytes(png('blue'))
    literal = root / '%6frigin.png'
    source = '/uploads/%256frigin.png'
    if literal_source_exists:
        literal.write_bytes(png('red'))
        assert read_local_image(source, root) == literal.read_bytes()
        derivative = asyncio.run(cache.get_derivative(source, cache.DerivativeSpec(), upload_root=root))
        assert derivative.source_key != cache.source_key_for('/uploads/origin.png')
    else:
        with pytest.raises(ValueError):
            read_local_image(source, root)
    engine = make_engine('sqlite:///' + str(tmp_path / 'probe.db'))
    SQLModel.metadata.create_all(engine)
    try:
        with Session(engine) as session:
            session.add(Character(id='c', name='Synthetic', persona='Synthetic'))
            session.add(CharacterAsset(id='a', character_id='c', label='Synthetic', image_url=source))
            session.commit()
            asset_service.delete_asset(session, session.get(CharacterAsset, 'a'), upload_root=root)
            assert session.get(CharacterAsset, 'a') is None
        print({'case': 'double_encoded', 'literal_source_exists_before': literal_source_exists,
               'unrelated_exists_after': unrelated.exists(), 'literal_exists_after': literal.exists()})
        assert unrelated.is_file(), 'DELETE followed a second percent decode and removed an unrelated original'
    finally:
        engine.dispose()


def test_single_encoded_positive_control_removes_correct_source(tmp_path):
    root = tmp_path / 'uploads'
    root.mkdir()
    target = root / 'origin.png'
    target.write_bytes(png('red'))
    untouched = root / 'other.png'
    untouched.write_bytes(png('blue'))
    source = '/uploads/%6frigin.png'
    assert read_local_image(source, root) == target.read_bytes()
    engine = make_engine('sqlite:///' + str(tmp_path / 'positive.db'))
    SQLModel.metadata.create_all(engine)
    try:
        with Session(engine) as session:
            session.add(Character(id='c', name='Synthetic', persona='Synthetic'))
            session.add(CharacterAsset(id='a', character_id='c', label='Synthetic', image_url=source))
            session.commit()
            asset_service.delete_asset(session, session.get(CharacterAsset, 'a'), upload_root=root)
            assert session.get(CharacterAsset, 'a') is None
        assert not target.exists()
        assert untouched.is_file()
    finally:
        engine.dispose()
