"""Real WebP display endpoints; original source URLs and bytes are preserved."""
import base64
from io import BytesIO
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image
import pytest

from app.api import images
from app.core.upload_files import UploadStaticFiles


def png(color='red', size=(900, 450)):
    output = BytesIO()
    Image.new('RGB', size, color).save(output, 'PNG')
    return output.getvalue()


@pytest.fixture
def display(tmp_path, monkeypatch):
    root = tmp_path / 'uploads'
    root.mkdir()
    monkeypatch.setattr(images, 'get_settings', lambda: SimpleNamespace(upload_root=str(root), shared_upload_root=None))
    app = FastAPI()
    app.include_router(images.router)
    app.mount('/uploads', UploadStaticFiles(root), name='uploads')
    with TestClient(app) as client:
        yield client, root


def count_transforms(monkeypatch):
    from app.services import image_derivative_cache as cache, asset_image_service as service
    original = service.transform_image
    calls = []
    async def transform(*args, **kwargs):
        calls.append(kwargs)
        return await original(*args, **kwargs)
    monkeypatch.setattr(service, 'transform_image', transform)
    if hasattr(cache, 'transform_image'):
        monkeypatch.setattr(cache, 'transform_image', transform)
    return calls


def test_legacy_png_is_served_as_webp_once_with_etag_and_full_variant(display, monkeypatch):
    client, root = display
    original = png()
    (root / 'legacy.png').write_bytes(original)
    calls = count_transforms(monkeypatch)
    params = {'source': '/uploads/legacy.png', 'variant': 'thumbnail'}
    first = client.get('/images/render.webp', params=params)
    assert first.status_code == 200 and first.headers['content-type'] == 'image/webp'
    assert first.headers['x-content-type-options'] == 'nosniff'
    with Image.open(BytesIO(first.content)) as decoded:
        assert decoded.format == 'WEBP' and decoded.size == (512, 256)
    second = client.get('/images/render.webp', params=params)
    assert second.content == first.content and len(calls) == 1
    conditional = client.get('/images/render.webp', params=params, headers={'If-None-Match': first.headers['etag']})
    assert conditional.status_code == 304 and conditional.content == b'' and len(calls) == 1
    full = client.get('/images/render.webp', params={**params, 'variant': 'full'})
    with Image.open(BytesIO(full.content)) as decoded:
        assert decoded.format == 'WEBP' and decoded.size == (900, 450)
    assert len(calls) == 2 and (root / 'legacy.png').read_bytes() == original


def test_source_change_invalidates_derived_bytes_and_etag(display, monkeypatch):
    client, root = display
    (root / 'source.png').write_bytes(png('red'))
    calls = count_transforms(monkeypatch)
    params = {'source': '/uploads/source.png'}
    first = client.get('/images/render.webp', params=params)
    (root / 'source.png').write_bytes(png('blue'))
    second = client.get('/images/render.webp', params=params, headers={'If-None-Match': first.headers['etag']})
    assert first.status_code == second.status_code == 200
    assert first.content != second.content and first.headers['etag'] != second.headers['etag']
    assert len(calls) == 2


@pytest.mark.parametrize('source', ['https://example.invalid/image.png', 'data:image/png;base64,eA==', '/etc/passwd', '/uploads/../secret.png', '/uploads/missing.png'])
def test_get_never_fetches_remote_or_escapes_uploads(display, monkeypatch, source):
    client, _ = display
    from app.services import image_derivative_cache as cache
    async def deny(*args, **kwargs):
        raise AssertionError('No downloader allowed from GET')
    if hasattr(cache, 'download_image'):
        monkeypatch.setattr(cache, 'download_image', deny)
    response = client.get('/images/render.webp', params={'source': source})
    assert response.status_code in (404, 422)


def test_explicit_data_prepare_returns_only_an_opaque_webp_url_and_reuses(display, monkeypatch):
    client, root = display
    calls = count_transforms(monkeypatch)
    source = 'data:image/png;base64,' + base64.b64encode(png()).decode()
    response = client.post('/images/prepare', json={'source': source, 'variant': 'thumbnail'})
    assert response.status_code == 200
    url = response.json()['image_url']
    assert url.startswith('/images/cache/') and url.endswith('.webp') and source not in url
    served = client.get(url)
    assert served.status_code == 200 and served.headers['content-type'] == 'image/webp'
    with Image.open(BytesIO(served.content)) as decoded:
        assert decoded.format == 'WEBP'
    again = client.post('/images/prepare', json={'source': source, 'variant': 'thumbnail'})
    assert again.json()['image_url'] == url and len(calls) == 1
    cached = list((root / '.image-cache').rglob('*'))
    for path in cached:
        if path.is_file() and path.suffix == '.json':
            assert source.encode() not in path.read_bytes()
    assert client.get('/uploads/.image-cache/v1/anything.json').status_code == 404


@pytest.mark.parametrize('body', [b'{"source":"a","source":"b"}', b'{"source":{}}', b'{"source":"a","unexpected":true}', b'{"source":"a","variant":"enormous"}'])
def test_prepare_rejects_invalid_metadata_before_any_transform(display, monkeypatch, body):
    client, _ = display
    calls = count_transforms(monkeypatch)
    response = client.post('/images/prepare', content=body, headers={'Content-Type': 'application/json'})
    assert response.status_code == 422 and calls == []


def test_private_cache_metadata_and_lock_are_not_static_files(display):
    client, root = display
    private = root / '.image-cache' / 'v1' / 'synthetic'
    private.mkdir(parents=True)
    (private / 'metadata.json').write_text('{"private":"synthetic"}')
    (private / '.lock').write_bytes(b'')
    for name in ('metadata.json', '.lock'):
        assert client.get('/uploads/.image-cache/v1/synthetic/' + name).status_code == 404
        assert client.get('/images/cache/synthetic/' + name).status_code == 404


@pytest.mark.parametrize('control', ['.image-cache', '.image-cleanup'])
@pytest.mark.parametrize('alias', [False, True])
def test_private_control_trees_cannot_be_served_through_static_aliases(display, control, alias):
    client, root = display
    directory = root / control / 'private'
    directory.mkdir(parents=True)
    (directory / 'intent.json').write_text('{"private":"synthetic"}')
    if alias:
        (root / 'public-alias').symlink_to(directory, target_is_directory=True)
        path = '/uploads/public-alias/intent.json'
    else:
        path = f'/uploads/{control}/private/intent.json'
    assert client.get(path).status_code == 404
    (root / 'legitimate.png').write_bytes(png())
    assert client.get('/uploads/legitimate.png').status_code == 200
