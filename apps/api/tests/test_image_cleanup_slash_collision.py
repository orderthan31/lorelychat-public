"""Unchanged exact-tree collision probe and valid-path controls, synthetic only."""
import asyncio
from io import BytesIO
from urllib.parse import quote

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


def delete(root, database, source):
    engine = make_engine('sqlite:///' + str(database))
    SQLModel.metadata.create_all(engine)
    try:
        with Session(engine) as session:
            session.add(Character(id='c', name='Synthetic', persona='Synthetic'))
            session.add(CharacterAsset(id='a', character_id='c', label='Synthetic', image_url=source))
            session.commit()
            asset_service.delete_asset(session, session.get(CharacterAsset, 'a'), upload_root=root)
            assert session.get(CharacterAsset, 'a') is None
    finally:
        engine.dispose()


def warm(root, source):
    return asyncio.run(cache.get_derivative(source, cache.DerivativeSpec(), upload_root=root))


def cached(root, image):
    try:
        cache.read_cached(root, image.source_key, image.filename)
        return True
    except ValueError:
        return False


def test_literal_percent_slash_does_not_alias_nested_input_original(tmp_path):
    root = tmp_path / 'uploads'
    root.mkdir()
    literal = root / 'a%2fb.png'
    literal.write_bytes(png('red'))
    nested = root / 'a' / 'b.png'
    nested.parent.mkdir()
    nested.write_bytes(png('blue'))
    source = '/uploads/a%252fb.png'
    assert read_local_image(source, root) == literal.read_bytes()
    literal_derivative = warm(root, source)
    nested_derivative = warm(root, '/uploads/a/b.png')
    assert literal_derivative.source_key != nested_derivative.source_key
    delete(root, tmp_path / 'collision.db', source)
    observed = {'literal_original_exists': literal.exists(), 'nested_original_exists': nested.exists(),
                'literal_derivative_exists': cached(root, literal_derivative),
                'nested_derivative_exists': cached(root, nested_derivative)}
    print(observed)
    assert nested.is_file(), 'Unrelated nested input original was unlinked by double decoding'
    assert cached(root, nested_derivative), 'Unrelated source cache was purged'
    assert not literal.exists(), 'Correct owned source was not reclaimed'
    assert not cached(root, literal_derivative), 'Correct source derivative was not purged'


@pytest.mark.parametrize('filename', ['100%.png', 'a b.png', '한글.png'])
def test_valid_percent_space_nonascii_control(tmp_path, filename):
    root = tmp_path / 'uploads'
    root.mkdir()
    target = root / filename
    target.write_bytes(png('red'))
    untouched = root / 'untouched.png'
    untouched.write_bytes(png('blue'))
    source = '/uploads/' + quote(filename, safe='')
    assert read_local_image(source, root) == target.read_bytes()
    derivative = warm(root, source)
    delete(root, tmp_path / 'positive.db', source)
    assert untouched.is_file()
    assert not target.exists()
    assert not cached(root, derivative)
