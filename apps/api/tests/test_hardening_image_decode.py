"""Full-decoder acceptance: generated synthetic pixels, real upload handlers."""
import io
import struct
import zlib

import pytest
from PIL import Image


def encoded(fmt, mode='RGB', size=(12, 9), **options):
    out = io.BytesIO()
    with Image.new(mode, size, 1) as image:
        image.save(out, format=fmt, **options)
    return out.getvalue()


def chunk(kind, body):
    return struct.pack('>I', len(body)) + kind + body + struct.pack('>I', zlib.crc32(kind + body))


def bad_image(fmt):
    if fmt == 'PNG':
        # Valid CRC/zlib/byte count, invalid scanline filter.
        return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', 1, 1, 8, 2, 0, 0, 0))
                + chunk(b'IDAT', zlib.compress(b'\xff\0\0\0')) + chunk(b'IEND', b''))
    if fmt == 'JPEG':
        data = encoded('JPEG')
        # Preserve SOF and SOS marker, omit scan header and entropy entirely.
        return data[:data.index(b'\xff\xda') + 2] + b'\xff\xd9'
    if fmt == 'GIF':
        # Structurally complete blocks, impossible LZW stream.
        return b'GIF89a\x01\0\x01\0\x80\0\0' + b'\0\0\0\xff\xff\xff' + b',\0\0\0\0\x01\0\x01\0\0\x02\x01\xff\0;'
    body = b'\x2f\0\0\0\0'  # Valid VP8L dimensions, no entropy.
    payload = b'WEBPVP8L' + struct.pack('<I', len(body)) + body + b'\0'
    return b'RIFF' + struct.pack('<I', len(payload)) + payload


@pytest.mark.parametrize('fmt', ['PNG', 'JPEG', 'GIF', 'WEBP'])
@pytest.mark.parametrize('route', ['avatars', 'conversation-thumbnails', 'world-thumbnails'])
def test_actual_upload_rejects_corrupt_pixel_body(client, fmt, route):
    from app.api.uploads import UPLOAD_ROOT
    before = set(UPLOAD_ROOT.rglob('*'))
    response = client.post('/uploads/' + route, files={'file': ('synthetic', bad_image(fmt), 'image/' + fmt.lower())})
    assert response.status_code == 422
    assert response.json()['detail'] == 'Invalid image or image exceeds safety budget'
    assert set(UPLOAD_ROOT.rglob('*')) == before


@pytest.mark.parametrize('fmt,mode,options', [
    ('PNG', '1', {}), ('PNG', 'L', {}), ('PNG', 'P', {}), ('PNG', 'LA', {}),
    ('PNG', 'RGB', {}), ('PNG', 'RGBA', {}), ('PNG', 'I;16', {}),
    ('JPEG', 'RGB', {}), ('JPEG', 'L', {}), ('JPEG', 'CMYK', {}),
    ('JPEG', 'RGB', {'progressive': True}), ('GIF', 'P', {}),
    ('WEBP', 'RGB', {}), ('WEBP', 'RGBA', {'lossless': True}),
])
def test_legitimate_modes_through_upload(client, fmt, mode, options):
    data = encoded(fmt, mode, **options)
    response = client.post('/uploads/avatars', files={'file': ('synthetic', data, 'image/' + fmt.lower())})
    assert response.status_code == 200, response.text
    assert client.get(response.json()['avatar_url']).content == data


def test_every_gif_frame_is_decoded(client):
    out = io.BytesIO()
    with Image.new('RGB', (8, 8), 'red') as a, Image.new('RGB', (8, 8), 'blue') as b:
        a.save(out, 'GIF', save_all=True, append_images=[b], duration=50, loop=0)
    response = client.post('/uploads/avatars', files={'file': ('synthetic', out.getvalue(), 'image/gif')})
    assert response.status_code == 200


@pytest.mark.parametrize('fmt', ['PNG', 'WEBP'])
def test_animation_policy_remains_reject(client, fmt):
    out = io.BytesIO()
    with Image.new('RGB', (8, 8), 'red') as a, Image.new('RGB', (8, 8), 'blue') as b:
        a.save(out, fmt, save_all=True, append_images=[b], duration=50, loop=0)
    response = client.post('/uploads/avatars', files={'file': ('synthetic', out.getvalue(), 'image/' + fmt.lower())})
    assert response.status_code == 422


@pytest.mark.parametrize('mode', ['RGB', 'RGBA'])
def test_24_megapixel_legitimate_image_fits_process_budget(client, mode):
    data = encoded('PNG', mode, (6000, 4000))
    response = client.post('/uploads/avatars', files={'file': ('synthetic', data, 'image/png')})
    assert response.status_code == 200, response.text


def forged_png(width, height, color=2, palette=None):
    data = b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, color, 0, 0, 0))
    if palette is not None:
        data += chunk(b'PLTE', palette)
    return data + chunk(b'IDAT', zlib.compress(b'\0\0' if color == 3 else b'\0\0\0\0')) + chunk(b'IEND', b'')


@pytest.mark.parametrize('data', [forged_png(6001, 4000), forged_png(16385, 1),
                                  forged_png(1, 1, 3), forged_png(1, 1, 3, b'\0')])
def test_forged_dimensions_and_malformed_palette_rejected(client, data):
    from app.api.uploads import UPLOAD_ROOT
    before = set(UPLOAD_ROOT.rglob('*'))
    assert client.post('/uploads/avatars', files={'file': ('synthetic', data, 'image/png')}).status_code == 422
    assert set(UPLOAD_ROOT.rglob('*')) == before


@pytest.mark.parametrize('frames,size', [(129, (2, 2)), (2, (4000, 3001))])
def test_gif_frame_and_aggregate_pixel_bombs(client, frames, size):
    # Repeat complete small legal frame blocks, with forged large canvas for
    # aggregate case: no allocation of huge decoded fixtures in the test.
    data = encoded('GIF', 'P', (2, 2))
    start = 13 + 3 * 2 ** ((data[10] & 7) + 1)
    header = bytearray(data[:start]); struct.pack_into('<HH', header, 6, *size)
    bomb = bytes(header) + data[start:-1] * frames + b';'
    assert client.post('/uploads/avatars', files={'file': ('synthetic', bomb, 'image/gif')}).status_code == 422


@pytest.mark.parametrize('fmt', ['PNG', 'JPEG', 'GIF', 'WEBP'])
def test_asset_decode_failure_has_no_rows_files_or_shared_damage(client, session, tmp_path, monkeypatch, fmt):
    from app.api import assets
    from app.db.models import Character, CharacterAsset
    from sqlmodel import select
    session.add(Character(id='decode-character', name='Synthetic', persona='Synthetic'))
    session.commit()
    monkeypatch.setattr(assets, 'UPLOAD_ROOT', tmp_path / 'private')
    monkeypatch.setattr(assets, 'ASSET_DIR', tmp_path / 'private' / 'character-assets')
    shared = tmp_path / 'shared'; shared.mkdir(); sentinel = shared / 'existing.png'
    sentinel.write_bytes(encoded('PNG'))
    before = sentinel.read_bytes()
    response = client.post('/characters/decode-character/assets/upload', files={'file': ('synthetic', bad_image(fmt), 'image/' + fmt.lower())})
    assert response.status_code == 422
    assert session.exec(select(CharacterAsset)).all() == []
    assert not list((tmp_path / 'private').rglob('*'))
    assert sentinel.read_bytes() == before


def test_concurrent_sync_requests_have_independent_decoder_state():
    from concurrent.futures import ThreadPoolExecutor
    from app.ops.image_validation import verify_image
    good, bad = encoded('PNG'), bad_image('GIF')
    def run(data, mime):
        try:
            return verify_image(data, mime)
        except ValueError:
            return 'rejected'
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda item: run(*item), [(good, 'image/png'), (bad, 'image/gif')]))
    assert results == ['.png', 'rejected']
    assert verify_image(good, 'image/png') == '.png'


@pytest.mark.asyncio
async def test_async_decoder_responsive_and_cancellation_reaps(monkeypatch):
    import asyncio
    import sys
    from app.ops import image_validation as validator
    children = []
    real_spawn = asyncio.create_subprocess_exec
    async def track(*args, **kwargs):
        child = await real_spawn(*args, **kwargs); children.append(child); return child
    monkeypatch.setattr(asyncio, 'create_subprocess_exec', track)
    monkeypatch.setattr(validator, '_worker_command', lambda mime: [sys.executable, '-I', '-c', 'import time;time.sleep(30)'])
    task = asyncio.create_task(validator.verify_image_async(encoded('PNG'), 'image/png'))
    for _ in range(100):
        if children:
            break
        await asyncio.sleep(.01)
    assert children
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert children[0].returncode is not None
    assert validator._DECODE_SLOTS.acquire(blocking=False)
    validator._DECODE_SLOTS.release()


@pytest.mark.asyncio
async def test_async_timeout_reaps_and_does_not_block_loop(monkeypatch):
    import asyncio
    import sys
    from app.ops import image_validation as validator
    monkeypatch.setattr(validator, '_worker_command', lambda mime: [sys.executable, '-I', '-c', 'import time;time.sleep(30)'])
    monkeypatch.setattr(validator, 'DECODE_TIMEOUT_SECONDS', .15)
    ticks = []
    async def ticker():
        for _ in range(6):
            await asyncio.sleep(.02); ticks.append(1)
    with pytest.raises(ValueError):
        await asyncio.gather(validator.verify_image_async(encoded('PNG'), 'image/png'), ticker())
    assert len(ticks) == 6


def test_timeout_is_fail_closed_for_actual_asset_upload(client, session, tmp_path, monkeypatch):
    import sys
    from app.ops import image_validation as validator
    from app.api import assets
    from app.db.models import Character, CharacterAsset
    from sqlmodel import select
    session.add(Character(id='timeout-character', name='Synthetic', persona='Synthetic')); session.commit()
    monkeypatch.setattr(assets, 'UPLOAD_ROOT', tmp_path)
    monkeypatch.setattr(assets, 'ASSET_DIR', tmp_path / 'character-assets')
    monkeypatch.setattr(validator, '_worker_command', lambda mime: [sys.executable, '-I', '-c', 'import time;time.sleep(30)'])
    monkeypatch.setattr(validator, 'DECODE_TIMEOUT_SECONDS', .15)
    response = client.post('/characters/timeout-character/assets/upload', files={'file': ('synthetic', encoded('PNG'), 'image/png')})
    assert response.status_code == 422
    assert session.exec(select(CharacterAsset)).all() == []
    assert not list(tmp_path.rglob('*'))


def test_missing_worker_and_capacity_fail_closed(client, monkeypatch):
    from app.ops import image_validation as validator
    monkeypatch.setattr(validator, '_worker_command', lambda mime: ['/nonexistent/synthetic-decoder'])
    assert client.post('/uploads/avatars', files={'file': ('synthetic', encoded('PNG'), 'image/png')}).status_code == 503
    assert validator._DECODE_SLOTS.acquire(blocking=False)
    assert validator._DECODE_SLOTS.acquire(blocking=False)
    try:
        assert client.post('/uploads/avatars', files={'file': ('synthetic', encoded('PNG'), 'image/png')}).status_code == 503
    finally:
        validator._DECODE_SLOTS.release(); validator._DECODE_SLOTS.release()


@pytest.mark.parametrize('failure', ['memory', 'cpu', 'native-warning'])
def test_real_worker_resource_limits_and_native_diagnostics(client, session, tmp_path, monkeypatch, failure):
    import sys
    from app.ops import image_validation as validator
    from app.api import assets
    from app.db.models import Character, CharacterAsset
    from sqlmodel import select
    session.add(Character(id='limit-character', name='Synthetic', persona='Synthetic')); session.commit()
    monkeypatch.setattr(assets, 'UPLOAD_ROOT', tmp_path)
    monkeypatch.setattr(assets, 'ASSET_DIR', tmp_path / 'character-assets')
    # Inject deterministic hostile codec behavior, not fake subprocess results:
    # real worker main still sets all OS resource caps before calling Image.open.
    action = {'memory': 'bytearray(2 * 1024**3)', 'cpu': 'exec("while True: pass")',
              'native-warning': 'os.write(2, b"synthetic corrupt entropy warning")'}[failure]
    script = (f'import runpy,sys,os;from PIL import Image;d=runpy.run_path({str(validator._WORKER)!r});'
              'original=Image.open;'
              f'Image.open=lambda *a,**kw: ({action},original(*a,**kw))[1];'
              'sys.argv=["worker","image/png"];sys.exit(d["main"]())')
    monkeypatch.setattr(validator, '_worker_command', lambda mime: [sys.executable, '-I', '-B', '-c', script])
    response = client.post('/characters/limit-character/assets/upload', files={'file': ('synthetic', encoded('PNG'), 'image/png')})
    assert response.status_code == 422
    assert response.json()['detail'] == 'Invalid image or image exceeds safety budget'
    assert session.exec(select(CharacterAsset)).all() == []
    assert not list(tmp_path.rglob('*'))


def test_corrupt_second_gif_frame_is_not_hidden_by_first_frame(client):
    data = encoded('GIF', 'P', (2, 2))
    start = 13 + 3 * 2 ** ((data[10] & 7) + 1)
    descriptor = data[start:start + 10]
    assert descriptor.startswith(b',') and not descriptor[9] & 128
    corrupt = data[:-1] + descriptor + b'\x02\x01\xff\0;'
    assert client.post('/uploads/avatars', files={'file': ('synthetic', corrupt, 'image/gif')}).status_code == 422


def test_decoder_rejects_entropy_truncation_with_jpeg_end_preserved(client):
    data = encoded('JPEG', size=(100, 100))
    scan = data.index(b'\xff\xda')
    header_end = scan + 2 + int.from_bytes(data[scan + 2:scan + 4], 'big')
    data = data[:header_end + 1] + b'\xff\xd9'
    assert client.post('/uploads/avatars', files={'file': ('synthetic', data, 'image/jpeg')}).status_code == 422


def test_decoded_byte_budget_applies_before_pixel_publication(client):
    # 24MP 16-bit RGBA is within pixel/side caps but over 128MiB inflated bytes.
    compressor = zlib.compressobj()
    row = b'\0' * (6000 * 8 + 1)
    payload = b''.join(compressor.compress(row) for _ in range(4000)) + compressor.flush()
    data = (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', 6000, 4000, 16, 6, 0, 0, 0))
            + chunk(b'IDAT', payload) + chunk(b'IEND', b''))
    assert client.post('/uploads/avatars', files={'file': ('synthetic', data, 'image/png')}).status_code == 422


@pytest.mark.asyncio
async def test_actual_upload_api_stays_responsive_and_sheds_load(client, monkeypatch):
    import asyncio
    import sys
    import httpx
    from app.main import app
    from app.ops import image_validation as validator
    children = []
    real_spawn = asyncio.create_subprocess_exec
    async def track(*args, **kwargs):
        child = await real_spawn(*args, **kwargs); children.append(child); return child
    monkeypatch.setattr(asyncio, 'create_subprocess_exec', track)
    monkeypatch.setattr(validator, '_worker_command', lambda mime: [sys.executable, '-I', '-c',
        'import sys,time;sys.stdin.buffer.read();time.sleep(.4);sys.stdout.buffer.write(b"OK")'])
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://testserver') as http:
        async def upload():
            return await http.post('/uploads/avatars', files={'file': ('synthetic', encoded('PNG'), 'image/png')})
        tasks = [asyncio.create_task(upload()) for _ in range(2)]
        for _ in range(100):
            if len(children) == 2:
                break
            await asyncio.sleep(.005)
        assert len(children) == 2
        busy = await upload()
        assert busy.status_code == 503 and busy.json()['detail'] == 'Image decoder busy'
        assert busy.headers['retry-after'] == '1'
        health = await http.get('/health')
        assert health.status_code == 200
        assert not any(task.done() for task in tasks)
        assert [response.status_code for response in await asyncio.gather(*tasks)] == [200, 200]
    assert all(child.returncode == 0 for child in children)


def test_async_admission_survives_separate_event_loops():
    import asyncio
    from app.ops.image_validation import verify_image_async
    for _ in range(4):
        assert asyncio.run(verify_image_async(encoded('PNG'), 'image/png')) == '.png'


@pytest.mark.asyncio
async def test_actual_cancelled_upload_reaps_without_publication(client, monkeypatch):
    import asyncio
    import sys
    import httpx
    from app.main import app
    from app.api.uploads import UPLOAD_ROOT
    from app.ops import image_validation as validator
    children = []
    real_spawn = asyncio.create_subprocess_exec
    async def track(*args, **kwargs):
        child = await real_spawn(*args, **kwargs); children.append(child); return child
    monkeypatch.setattr(asyncio, 'create_subprocess_exec', track)
    monkeypatch.setattr(validator, '_worker_command', lambda mime: [sys.executable, '-I', '-c', 'import time;time.sleep(30)'])
    before = set(UPLOAD_ROOT.rglob('*'))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://testserver') as http:
        task = asyncio.create_task(http.post('/uploads/avatars', files={'file': ('synthetic', encoded('PNG'), 'image/png')}))
        for _ in range(100):
            if children:
                break
            await asyncio.sleep(.005)
        assert children
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert all(child.returncode is not None for child in children)
    assert set(UPLOAD_ROOT.rglob('*')) == before


@pytest.mark.parametrize('fmt,mode,options', [('JPEG', 'RGB', {}), ('JPEG', 'CMYK', {}),
                                            ('JPEG', 'RGB', {'progressive': True}),
                                            ('WEBP', 'RGBA', {'lossless': True})])
def test_24_megapixel_other_codecs_fit_process_budget(client, fmt, mode, options):
    data = encoded(fmt, mode, (6000, 4000), **options)
    response = client.post('/uploads/avatars', files={'file': ('synthetic', data, 'image/' + fmt.lower())})
    assert response.status_code == 200, response.text



def test_failed_registration_keeps_existing_shared_references(client, session, monkeypatch):
    from app.api import assets
    from app.db.models import Character, CharacterAsset
    from sqlmodel import select
    session.add(Character(id='shared-character', name='Synthetic', persona='Synthetic')); session.commit()
    data = encoded('PNG')
    first = client.post('/characters/shared-character/assets/upload', files={'file': ('synthetic', data, 'image/png')})
    assert first.status_code == 200
    url = first.json()['image_url']
    second = client.post('/characters/shared-character/assets', json={'label': 'shared', 'image_url': url})
    assert second.status_code == 200
    before = set(assets.UPLOAD_ROOT.rglob('*'))
    def fail(*args, **kwargs):
        raise RuntimeError('synthetic registration failure')
    monkeypatch.setattr(assets.asset_service, 'create_asset', fail)
    with pytest.raises(RuntimeError, match='synthetic registration failure'):
        client.post('/characters/shared-character/assets/upload', files={'file': ('synthetic', data, 'image/png')})
    assert set(assets.UPLOAD_ROOT.rglob('*')) == before
    assert len(session.exec(select(CharacterAsset)).all()) == 2
    assert client.get(url).content == data


def test_postcommit_registration_failure_does_not_delete_committed_bytes(client, session, monkeypatch):
    from app.api import assets
    from app.db.models import Character, CharacterAsset
    from sqlmodel import select
    session.add(Character(id='committed-character', name='Synthetic', persona='Synthetic')); session.commit()
    original = assets.asset_service.create_asset
    def commit_then_fail(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError('synthetic postcommit failure')
    monkeypatch.setattr(assets.asset_service, 'create_asset', commit_then_fail)
    data = encoded('PNG')
    with pytest.raises(RuntimeError, match='synthetic postcommit failure'):
        client.post('/characters/committed-character/assets/upload', files={'file': ('synthetic', data, 'image/png')})
    rows = session.exec(select(CharacterAsset)).all()
    assert len(rows) == 1
    assert client.get(rows[0].image_url).content == data
