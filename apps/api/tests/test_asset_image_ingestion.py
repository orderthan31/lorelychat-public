"""Real codec + isolated storage/HTTP regressions; no live URL or DB access."""
import asyncio
from contextlib import asynccontextmanager
import hashlib
import io
from pathlib import Path
import socket
import struct
from types import SimpleNamespace
import zlib

import pytest
from PIL import Image
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine, select
from sqlalchemy.pool import StaticPool

from app.services import asset_image_service as images
from app.ops import asset_image_download as remote


def encoded(fmt='PNG', size=(64, 32), color=(200, 10, 20, 128), exif=None):
    image = Image.new('RGBA', size, color)
    if fmt == 'JPEG':
        image = image.convert('RGB')
    out = io.BytesIO()
    image.save(out, format=fmt, **({'exif': exif} if exif else {}))
    return out.getvalue()


def animation(fmt='GIF'):
    out = io.BytesIO()
    a, b = Image.new('RGB', (32, 16), 'red'), Image.new('RGB', (32, 16), 'blue')
    a.save(out, format=fmt, save_all=True, append_images=[b], duration=[100, 200], loop=2)
    return out.getvalue()


@pytest.mark.asyncio
@pytest.mark.parametrize('fmt,mime', [('PNG', 'image/png'), ('JPEG', 'image/jpeg'), ('WEBP', 'image/webp')])
async def test_real_normalization_and_thumbnail(tmp_path, fmt, mime):
    original = encoded(fmt, (2400, 1200))
    result = await images.ingest_image(upload_root=tmp_path, data=original, source_mime=mime)
    assert result.source == 'upload'
    for url, meta, size in [(result.image_url, result.metadata['normalization'], (2048, 1024)), (result.thumbnail_url, result.metadata['thumbnail'], (512, 256))]:
        body = (tmp_path / url.removeprefix('/uploads/')).read_bytes()
        with Image.open(io.BytesIO(body)) as image:
            assert image.format == 'WEBP' and image.size == size
            assert not image.getexif() and 'icc_profile' not in image.info
            if fmt != 'JPEG':
                assert image.getpixel((0, 0))[3] == 128
        assert meta['mime'] == 'image/webp' and meta['quality'] == 80
        assert meta['size'] == len(body) and meta['sha256'] == hashlib.sha256(body).hexdigest()


@pytest.mark.asyncio
async def test_orientation_and_explicit_static_derivatives():
    exif = Image.Exif(); exif[274] = 6; exif[270] = 'private metadata'
    body, w, h = await images.transform_image(encoded('JPEG', (80, 40), exif=exif), 'image/jpeg')
    assert (w, h) == (40, 80)
    with Image.open(io.BytesIO(body)) as image:
        assert not image.getexif()
    for target, fmt in [('image/jpeg', 'JPEG'), ('image/png', 'PNG'), ('image/webp', 'WEBP')]:
        body, _, _ = await images.transform_image(animation(), 'image/gif', target_mime=target, preserve_animation=False)
        with Image.open(io.BytesIO(body)) as image:
            assert image.format == fmt and not getattr(image, 'is_animated', False)
            assert image.convert('RGB').getpixel((0, 0))[0] > 200


@pytest.mark.asyncio
@pytest.mark.parametrize('fmt,mime', [('GIF', 'image/gif'), ('WEBP', 'image/webp')])
async def test_animation_is_preserved_canonical_and_thumbnail_static(tmp_path, fmt, mime):
    result = await images.ingest_image(upload_root=tmp_path, data=animation(fmt), source_mime=mime)
    with Image.open(tmp_path / result.image_url.removeprefix('/uploads/')) as image:
        assert image.is_animated and image.n_frames == 2 and image.info['loop'] == 2
    with Image.open(tmp_path / result.thumbnail_url.removeprefix('/uploads/')) as image:
        assert not getattr(image, 'is_animated', False)
    with pytest.raises(ValueError):
        await images.transform_image(animation(), 'image/gif', target_mime='image/jpeg', preserve_animation=True)


@pytest.mark.asyncio
async def test_invalid_size_mime_bomb_and_busy():
    header = struct.pack('>IIBBBBB', 100000, 100000, 8, 6, 0, 0, 0)
    bomb = b'\x89PNG\r\n\x1a\n' + struct.pack('>I', len(header)) + b'IHDR' + header + struct.pack('>I', zlib.crc32(b'IHDR' + header))
    for body, mime in [(b'invalid', 'image/png'), (b'x' * (images.MAX_ENCODED_BYTES + 1), 'image/png'), (encoded(), 'image/jpeg'), (bomb, 'image/png')]:
        with pytest.raises(ValueError):
            await images.transform_image(body, mime)
    assert images._DECODE_SLOTS.acquire(False) and images._DECODE_SLOTS.acquire(False)
    try:
        with pytest.raises(images.ImageDecoderBusy):
            await images.transform_image(encoded(), 'image/png')
    finally:
        images._DECODE_SLOTS.release(); images._DECODE_SLOTS.release()


@pytest.mark.asyncio
async def test_timeout_and_cancel_reap(monkeypatch):
    children = []
    spawn = asyncio.create_subprocess_exec
    async def slow(*args, **kwargs):
        child = await spawn(args[0], '-I', '-c', 'import time; time.sleep(30)', **kwargs)
        children.append(child)
        return child
    monkeypatch.setattr(asyncio, 'create_subprocess_exec', slow)
    monkeypatch.setattr(images, 'TRANSFORM_TIMEOUT_SECONDS', .1)
    with pytest.raises(ValueError, match='budget'):
        await images.transform_image(encoded(), 'image/png')
    monkeypatch.setattr(images, 'TRANSFORM_TIMEOUT_SECONDS', 10)
    task = asyncio.create_task(images.transform_image(encoded(), 'image/png'))
    while len(children) < 2:
        await asyncio.sleep(.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert all(child.returncode is not None for child in children)
    assert images._DECODE_SLOTS.acquire(False) and images._DECODE_SLOTS.acquire(False)
    images._DECODE_SLOTS.release(); images._DECODE_SLOTS.release()


def test_local_read_containment_missing_symlinks_fifo_and_caps(tmp_path):
    (tmp_path / 'ok.png').write_bytes(encoded())
    assert images.read_local_image('/uploads/ok.png', tmp_path) == encoded()
    (tmp_path / 'link.png').symlink_to(tmp_path / 'ok.png')
    (tmp_path / 'folder').symlink_to(tmp_path, target_is_directory=True)
    import os
    os.mkfifo(tmp_path / 'fifo')
    for url in ['/uploads/missing.png', '/uploads/../ok.png', '/uploads/%2e%2e/ok.png', '/uploads/link.png', '/uploads/folder/ok.png', '/uploads/fifo', '/etc/passwd', '/uploads/ok.png?secret=x']:
        with pytest.raises(ValueError): images.read_local_image(url, tmp_path)
    with (tmp_path / 'large').open('wb') as output:
        output.truncate(images.MAX_ENCODED_BYTES + 1)
    with pytest.raises(ValueError): images.read_local_image('/uploads/large', tmp_path)


@pytest.fixture
def api(tmp_path, monkeypatch):
    from app.api import assets
    from app.db.session import get_session
    from app.db.models import Character
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(Character(id='c', name='Test', persona='Fixture')); session.commit()
    def dependency():
        with Session(engine) as session: yield session
    app = FastAPI()
    app.include_router(assets.router)
    from app.core.upload_files import UploadStaticFiles
    app.mount('/uploads', UploadStaticFiles(tmp_path), name='uploads')
    app.dependency_overrides[get_session] = dependency
    monkeypatch.setattr(assets, 'UPLOAD_ROOT', tmp_path)
    monkeypatch.setattr(assets, 'get_settings', lambda: SimpleNamespace(shared_upload_root=None))
    with TestClient(app) as client:
        yield client, tmp_path, engine
    engine.dispose()


def test_upload_actual_response_bytes_and_local_registration(api):
    client, root, engine = api
    response = client.post('/characters/c/assets/upload', data={'label': 'photo'}, files={'file': ('a.png', encoded(), 'image/png')})
    assert response.status_code == 200, response.text
    record = response.json()
    assert record['source'] == 'upload'
    assert client.get(record['image_url']).content == (root / record['image_url'].removeprefix('/uploads/')).read_bytes()
    assert client.get(record['image_url']).headers['content-type'] == 'image/webp'
    (root / 'original.png').write_bytes(encoded())
    response = client.post('/characters/c/assets', json={'label': 'local', 'image_url': '/uploads/original.png'})
    assert response.status_code == 200, response.text
    assert response.json()['image_url'] != '/uploads/original.png'
    assert response.json()['source'] == 'url_import' and (root / 'original.png').exists()
    assert client.post('/characters/c/assets', json={'label': 'missing', 'image_url': '/uploads/missing.png'}).status_code == 422


def test_metadata_validation_before_ingestion_and_legacy_edit(api, monkeypatch):
    from app.db.models import CharacterAsset
    client, root, engine = api
    async def forbidden(**kwargs): raise AssertionError('metadata edit attempted ingestion')
    monkeypatch.setattr(images, 'ingest_image', forbidden)
    with Session(engine) as session:
        session.add(CharacterAsset(id='legacy', character_id='c', label='legacy', image_url='https://legacy.invalid/private?secret=sentinel'))
        session.commit()
    assert client.patch('/character-assets/legacy', json={'label': 'changed'}).status_code == 200
    assert client.patch('/character-assets/legacy', json={'image_url': 'https://legacy.invalid/private?secret=sentinel', 'label': 'same'}).status_code == 200
    assert client.post('/characters/c/assets', json={'label': '', 'image_url': 'https://example.invalid/x'}).status_code == 422
    assert client.post('/characters/c/assets/upload', data={'label': '   '}, files={'file': ('a.png', encoded(), 'image/png')}).status_code == 422
    assert list(root.iterdir()) == []


def test_failure_cleanup_and_postcommit_retention(api, monkeypatch):
    from app.services import asset_service
    client, root, engine = api
    def fail(*args, **kwargs): raise RuntimeError('injected')
    original = asset_service.create_asset
    monkeypatch.setattr(asset_service, 'create_asset', fail)
    with pytest.raises(RuntimeError):
        client.post('/characters/c/assets/upload', data={'label': 'photo'}, files={'file': ('a.png', encoded(), 'image/png')})
    assert list((root / 'character-assets').iterdir()) == []
    def commit_then_fail(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError('after commit')
    monkeypatch.setattr(asset_service, 'create_asset', commit_then_fail)
    with pytest.raises(RuntimeError):
        client.post('/characters/c/assets/upload', data={'label': 'photo'}, files={'file': ('a.png', encoded(), 'image/png')})
    assert len(list((root / 'character-assets').iterdir())) == 2


def test_replacement_and_external_failure_are_atomic(api, monkeypatch):
    client, root, engine = api
    (root / 'a.png').write_bytes(encoded())
    result = client.post('/characters/c/assets', json={'label': 'a', 'image_url': '/uploads/a.png'}).json()
    async def fail(url): raise ValueError('Image URL import failed or exceeds safety budget')
    monkeypatch.setattr(images, 'download_image', fail)
    response = client.patch('/character-assets/' + result['id'], json={'image_url': 'https://example.invalid/x?secret=test'})
    assert response.status_code == 422 and 'secret' not in response.text
    assert client.get('/character-assets/' + result['id']).json()['image_url'] == result['image_url']
    (root / 'b.png').write_bytes(encoded(color=(0, 0, 255, 255)))
    response = client.patch('/character-assets/' + result['id'], json={'image_url': '/uploads/b.png', 'metadata': None})
    assert response.status_code == 200, response.text
    assert response.json()['image_url'] != result['image_url']
    assert (root / result['image_url'].removeprefix('/uploads/')).exists()


class Response:
    def __init__(self, status=200, headers=None, chunks=None):
        self.status = status
        self.headers = headers or [(b'content-type', b'image/png')]
        self.chunks = chunks or [encoded()]
    async def aiter_stream(self):
        for chunk in self.chunks: yield chunk


def fake_remote(monkeypatch, responses, addresses=None):
    requests, resolutions = [], []
    async def resolve(self, host, port, **kwargs):
        resolutions.append(host)
        ips = addresses or (['127.0.0.1'] if host == 'private.test' else ['93.184.216.34'])
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', (ip, port)) for ip in ips]
    monkeypatch.setattr(asyncio.BaseEventLoop, 'getaddrinfo', resolve)
    class Pool:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        @asynccontextmanager
        async def stream(self, method, url, **kwargs):
            requests.append((method, url, kwargs))
            yield responses[len(requests) - 1]
    monkeypatch.setattr(remote.httpcore, 'AsyncConnectionPool', Pool)
    return requests, resolutions


@pytest.mark.asyncio
async def test_url_dns_pinning_host_sni_and_private_redirect(monkeypatch):
    requests, resolutions = fake_remote(monkeypatch, [Response()])
    assert (await remote.download_image('https://public.test/p.png?secret=sentinel'))[0] == encoded()
    assert resolutions == ['public.test']
    assert requests[0][1] == 'https://93.184.216.34:443/p.png?secret=sentinel'
    assert requests[0][2]['headers']['Host'] == 'public.test'
    assert requests[0][2]['extensions']['sni_hostname'] == 'public.test'
    requests, resolutions = fake_remote(monkeypatch, [Response(302, [(b'location', b'http://private.test/secret')])])
    with pytest.raises(ValueError, match='Image URL import failed'):
        await remote.download_image('https://public.test/a')
    assert len(requests) == 1 and resolutions == ['public.test', 'private.test']


@pytest.mark.asyncio
async def test_url_all_answers_caps_redirect_limit_and_safe_errors(monkeypatch, caplog):
    requests, _ = fake_remote(monkeypatch, [Response()], ['93.184.216.34', '127.0.0.1'])
    with pytest.raises(ValueError): await remote.download_image('https://public.test/a')
    assert not requests
    for response in [Response(chunks=[b'a' * remote.MAX_BYTES, b'b']), Response(headers=[(b'content-length', str(remote.MAX_BYTES + 1).encode())])]:
        fake_remote(monkeypatch, [response])
        with pytest.raises(ValueError): await remote.download_image('https://public.test/a?secret=sentinel')
    requests, _ = fake_remote(monkeypatch, [Response(302, [(b'location', b'/again')])] * 4)
    with pytest.raises(ValueError): await remote.download_image('https://public.test/a')
    assert len(requests) == 4
    for url in ['file:///etc/passwd', 'https://user:sentinel@public.test/a', '//public.test/a', 'http://public.test/\nsecret']:
        with pytest.raises(ValueError) as error: await remote.download_image(url)
        assert 'sentinel' not in str(error.value)
    assert 'sentinel' not in caplog.text


@pytest.mark.asyncio
async def test_actual_httpcore_transport_pins_connect_and_tls(monkeypatch, caplog):
    import httpcore
    import logging
    connects, tls, writes, resolutions = [], [], [], []
    async def resolve(self, host, port, **kwargs):
        resolutions.append(host)
        # A second lookup would be a rebinding attack. It must never happen.
        ip = '93.184.216.34' if len(resolutions) == 1 else '127.0.0.1'
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', (ip, port))]
    monkeypatch.setattr(asyncio.BaseEventLoop, 'getaddrinfo', resolve)
    body = encoded()
    class Stream:
        sent = False
        async def read(self, max_bytes, timeout=None):
            if self.sent: return b''
            self.sent = True
            return b'HTTP/1.1 200 OK\r\nContent-Type: image/png\r\nX-Private: sentinel\r\nContent-Length: ' + str(len(body)).encode() + b'\r\n\r\n' + body
        async def write(self, buffer, timeout=None): writes.append(buffer)
        async def aclose(self): pass
        async def start_tls(self, ssl_context, server_hostname=None, timeout=None):
            tls.append(server_hostname)
            assert ssl_context.check_hostname
            return self
        def get_extra_info(self, info): return None
    class Backend:
        async def connect_tcp(self, host, port, **kwargs):
            connects.append((host, port)); return Stream()
    pool = httpcore.AsyncConnectionPool
    monkeypatch.setattr(remote.httpcore, 'AsyncConnectionPool', lambda **kwargs: pool(network_backend=Backend(), **kwargs))
    caplog.set_level(logging.DEBUG)
    monkeypatch.setenv('HTTPS_PROXY', 'http://127.0.0.1:1')
    data, mime = await remote.download_image('https://public.test/image?secret=sentinel')
    assert data == body and mime == 'image/png'
    assert connects == [('93.184.216.34', 443)] and tls == ['public.test'] and resolutions == ['public.test']
    assert b'Host: public.test\r\n' in b''.join(writes)
    assert 'sentinel' not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize('target', ['image/jpeg', 'image/png'])
async def test_decision_derivative_pads_minimum_without_cropping_or_upscaling(target):
    body, width, height = await images.transform_image(encoded(size=(40, 800), color=(255, 0, 0, 255)), 'image/png', target_mime=target, max_edge=512, min_edge=280)
    assert (width, height) == (280, 512)
    with Image.open(io.BytesIO(body)) as image:
        assert image.size == (280, 512)
        assert image.convert('RGB').getpixel((140, 256))[0] > 200
        if target == 'image/jpeg':
            assert min(image.getpixel((0, 0))) > 245
        else:
            assert image.getpixel((0, 0))[3] == 0
    body, width, height = await images.transform_image(encoded(size=(40, 20)), 'image/png', target_mime=target, max_edge=512, min_edge=280)
    assert (width, height) == (280, 280)


@pytest.mark.asyncio
async def test_import_total_deadline_includes_dns(monkeypatch):
    async def slow(self, *args, **kwargs): await asyncio.sleep(30)
    monkeypatch.setattr(asyncio.BaseEventLoop, 'getaddrinfo', slow)
    monkeypatch.setattr(remote, 'DEADLINE', .01)
    with pytest.raises(ValueError, match='Image URL import failed'):
        await remote.download_image('https://public.test/a')


@pytest.mark.asyncio
async def test_unsafe_animation_and_shared_destination_rejected(tmp_path):
    # APNG is not admitted by the existing strict structural policy.
    with pytest.raises(ValueError):
        await images.transform_image(animation('PNG'), 'image/png', preserve_animation=True)
    with pytest.raises(ValueError, match='Private image storage'):
        await images.ingest_image(upload_root=tmp_path, shared_root=str(tmp_path), data=encoded(), source_mime='image/png')
    assert list(tmp_path.iterdir()) == []
    shared = tmp_path / 'shared'; shared.mkdir()
    private = tmp_path / 'private'; private.mkdir()
    (private / 'character-assets').symlink_to(shared, target_is_directory=True)
    with pytest.raises(OSError):
        await images.ingest_image(upload_root=private, data=encoded(), source_mime='image/png')
    assert list(shared.iterdir()) == []


def test_external_registration_owned_and_metadata_trust(api, monkeypatch):
    client, root, engine = api
    async def fetched(url): return encoded(), 'image/png'
    monkeypatch.setattr(images, 'download_image', fetched)
    response = client.post('/characters/c/assets', json={'label': 'url', 'image_url': 'https://public.test/a?secret=sentinel', 'metadata': {'normalization': {'owned': False}}})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result['source'] == 'url_import' and 'sentinel' not in response.text
    assert result['metadata']['normalization']['owned'] is True
    updated = client.patch('/character-assets/' + result['id'], json={'metadata': {'normalization': {'owned': False}}})
    assert updated.json()['metadata']['normalization'] == result['metadata']['normalization']
    assert client.patch('/character-assets/' + result['id'], json={'thumbnail_url': 'https://private.test/a'}).status_code == 422


@pytest.mark.parametrize('fmt,mime', [('JPEG', 'image/jpeg'), ('WEBP', 'image/webp'), ('GIF', 'image/gif')])
def test_upload_route_other_admitted_formats(api, fmt, mime):
    client, root, engine = api
    body = animation() if fmt == 'GIF' else encoded(fmt)
    response = client.post('/characters/c/assets/upload', data={'label': 'photo'}, files={'file': ('input', body, mime)})
    assert response.status_code == 200, response.text
    result = response.json()
    with Image.open(io.BytesIO(client.get(result['image_url']).content)) as image:
        assert image.format == 'WEBP'
        assert bool(getattr(image, 'is_animated', False)) == (fmt == 'GIF')
    with Image.open(io.BytesIO(client.get(result['thumbnail_url']).content)) as image:
        assert image.format == 'WEBP' and not getattr(image, 'is_animated', False)


def test_patch_failure_cleans_only_new_request_files(api, monkeypatch):
    from app.services import asset_service
    client, root, engine = api
    (root / 'a.png').write_bytes(encoded())
    result = client.post('/characters/c/assets', json={'label': 'a', 'image_url': '/uploads/a.png'}).json()
    before = {path.name for path in (root / 'character-assets').iterdir()}
    (root / 'b.png').write_bytes(encoded(color=(0, 0, 255, 255)))
    def failure(*args, **kwargs): raise RuntimeError('before commit')
    monkeypatch.setattr(asset_service, 'update_asset', failure)
    with pytest.raises(RuntimeError):
        client.patch('/character-assets/' + result['id'], json={'image_url': '/uploads/b.png'})
    assert {path.name for path in (root / 'character-assets').iterdir()} == before
    assert (root / 'a.png').exists() and (root / 'b.png').exists()


def test_real_refresh_error_retains_committed_files(api, monkeypatch):
    client, root, engine = api
    def broken_refresh(self, *args, **kwargs): raise RuntimeError('refresh failed')
    monkeypatch.setattr(Session, 'refresh', broken_refresh)
    with pytest.raises(RuntimeError, match='refresh failed'):
        client.post('/characters/c/assets/upload', data={'label': 'photo'}, files={'file': ('a.png', encoded(), 'image/png')})
    assert len(list((root / 'character-assets').iterdir())) == 2
    from app.db.models import CharacterAsset
    with Session(engine) as session:
        assert session.exec(select(CharacterAsset)).first() is not None
