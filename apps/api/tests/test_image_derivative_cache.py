"""Provider-free real-codec cache contract tests (isolated runner only)."""
import asyncio
import base64
from dataclasses import FrozenInstanceError
import io
import json
import multiprocessing
from pathlib import Path

import pytest
from PIL import Image

from app.services import image_derivative_cache as cache
from app.services import asset_image_service as images


def png(color='red', size=(80, 40)):
    buf = io.BytesIO()
    Image.new('RGB', size, color).save(buf, format='PNG')
    return buf.getvalue()


@pytest.fixture
def root(tmp_path):
    private = tmp_path / 'private'
    private.mkdir()
    (private / 'a.png').write_bytes(png())
    return private


def run(root, spec=None, source='/uploads/a.png', **kwargs):
    return asyncio.run(cache.get_derivative(source, spec or cache.DerivativeSpec(), upload_root=root, **kwargs))


def test_real_codec_warm_byte_and_option_invalidation(root, monkeypatch):
    calls = []
    original = cache.transform_image
    async def counted(*args, **kwargs):
        calls.append(1)
        return await original(*args, **kwargs)
    monkeypatch.setattr(cache, 'transform_image', counted)
    first = run(root)
    assert first == run(root)
    assert len(calls) == 1
    assert first.mime_type == 'image/webp'
    with Image.open(io.BytesIO(first.data)) as im:
        assert im.format == 'WEBP' and im.size == (80, 40)
    assert cache.read_cached(root, first.source_key, first.filename) == first
    assert 'RIFF' not in repr(first)
    with pytest.raises(FrozenInstanceError):
        first.width = 1
    (root / 'a.png').write_bytes(png('blue'))
    changed = run(root)
    assert changed.filename != first.filename
    assert not (root / '.image-cache/v1' / first.source_key / first.filename).exists()
    for spec in [cache.DerivativeSpec(max_edge=32), cache.DerivativeSpec(quality=55), cache.DerivativeSpec(version=2)]:
        assert run(root, spec).filename != changed.filename
    assert len(calls) == 5


def test_source_partition_budget_supports_a_thousand_image_library(root):
    # Lock anchors are persistent; a normal large library must not exhaust a
    # lifetime limit after only a few hundred distinct image paths.
    for index in range(1025):
        with cache.source_lock(root, f'/uploads/synthetic-{index}.png'):
            pass
    assert len([entry for entry in (root / '.image-cache/v1').iterdir() if entry.is_dir()]) == 1025


def test_source_key_normalization_and_invalid_sources(root):
    assert cache.source_key_for('/uploads/a.png') == cache.source_key_for('/uploads/%61.png')
    for source in ['/uploads/../a.png', '/uploads/%2e%2e/a.png', '/uploads/a.png?x=secret', '/uploads/.image-cache/v1/x', 'file:///secret', '/uploads/a\\b']:
        with pytest.raises(ValueError) as error:
            run(root, source=source)
        assert 'secret' not in str(error.value)


@pytest.mark.parametrize('spec', [cache.DerivativeSpec(max_edge=0), cache.DerivativeSpec(max_edge=2049), cache.DerivativeSpec(quality=True), cache.DerivativeSpec(version=0), cache.DerivativeSpec(mime_type='image/svg+xml'), cache.DerivativeSpec(min_edge=1), cache.DerivativeSpec(preserve_animation=1)])
def test_invalid_specs(root, spec):
    with pytest.raises(ValueError):
        run(root, spec)


def test_mime_padding_and_static_animation(root):
    padded = run(root, cache.DerivativeSpec(mime_type='image/png', max_edge=64, min_edge=48))
    assert (padded.width, padded.height) == (64, 48)
    jpg = run(root, cache.DerivativeSpec(mime_type='image/jpeg', max_edge=64))
    assert jpg.mime_type == 'image/jpeg' and jpg.data.startswith(b'\xff\xd8')
    out = io.BytesIO()
    Image.new('RGB', (40, 40), 'red').save(out, format='GIF', save_all=True, append_images=[Image.new('RGB', (40, 40), 'blue')], duration=100, loop=0)
    (root / 'a.png').write_bytes(out.getvalue())
    still = run(root)
    animated = run(root, cache.DerivativeSpec(preserve_animation=True))
    with Image.open(io.BytesIO(still.data)) as im:
        assert not getattr(im, 'is_animated', False)
    with Image.open(io.BytesIO(animated.data)) as im:
        assert im.is_animated and im.n_frames == 2


def test_twenty_concurrent_singleflight_and_busy_retry(root, monkeypatch):
    calls = []
    original = cache.transform_image
    async def counted(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise images.ImageDecoderBusy('Image decoder busy')
        return await original(*args, **kwargs)
    monkeypatch.setattr(cache, 'transform_image', counted)
    async def scenario():
        return await asyncio.gather(*(cache.get_derivative('/uploads/a.png', cache.DerivativeSpec(), upload_root=root) for _ in range(20)))
    results = asyncio.run(scenario())
    assert len({r.filename for r in results}) == 1 and len(calls) == 2


def test_corruption_regenerates_and_opaque_reads(root, monkeypatch):
    first = run(root)
    folder = root / '.image-cache/v1' / first.source_key
    (folder / first.filename).write_bytes(b'bad')
    assert run(root) == first
    meta = folder / (first.filename + '.json')
    metadata = json.loads(meta.read_text())
    metadata['width'] += 1
    meta.write_text(json.dumps(metadata))
    assert run(root) == first
    for name in ['.lock', first.filename + '.json', '../a.png', 'snapshot.bin', '/a.png']:
        with pytest.raises(ValueError):
            cache.read_cached(root, first.source_key, name)
    for key in ['..', first.source_key + '/..', 'not-a-key']:
        with pytest.raises(ValueError):
            cache.read_cached(root, key, first.filename)


def test_symlink_and_shared_readonly(root, tmp_path):
    shared = tmp_path / 'shared'
    shared.mkdir()
    (shared / 'b.png').write_bytes(png())
    before = list(shared.iterdir())
    result = run(root, source='/uploads/b.png', shared_root=str(shared))
    assert result.data and list(shared.iterdir()) == before
    # UI can warm a shared image; private-only Decision must still read its input
    # before considering that cache entry, rather than inherit UI read authority.
    with pytest.raises(ValueError):
        run(root, source='/uploads/b.png')
    with pytest.raises(ValueError):
        run(shared, source='/uploads/b.png', shared_root=str(shared))
    (root / 'evil.png').symlink_to(shared / 'b.png')
    with pytest.raises(ValueError):
        run(root, source='/uploads/evil.png')
    other = tmp_path / 'other'
    other.mkdir()
    (other / '.image-cache').symlink_to(shared, target_is_directory=True)
    with pytest.raises(ValueError):
        run(other)
    assert list(shared.iterdir()) == before
    folder = root / '.image-cache/v1' / result.source_key
    (folder / result.filename).unlink()
    (folder / result.filename).symlink_to(shared / 'b.png')
    with pytest.raises(ValueError):
        cache.read_cached(root, result.source_key, result.filename)
    assert run(root, source='/uploads/b.png', shared_root=str(shared)).data
    assert (shared / 'b.png').read_bytes() == png()


def test_cancel_waiter_and_producer_releases_lock(root, monkeypatch):
    async def scenario():
        with cache.source_lock(root, '/uploads/a.png'):
            waiter = asyncio.create_task(cache.get_derivative('/uploads/a.png', cache.DerivativeSpec(), upload_root=root))
            await asyncio.sleep(.08)
            waiter.cancel()
            with pytest.raises(asyncio.CancelledError):
                await waiter
        entered = asyncio.Event()
        async def blocked(*args, **kwargs):
            entered.set()
            await asyncio.sleep(10)
        monkeypatch.setattr(cache, 'transform_image', blocked)
        producer = asyncio.create_task(cache.get_derivative('/uploads/a.png', cache.DerivativeSpec(), upload_root=root))
        await entered.wait()
        producer.cancel()
        with pytest.raises(asyncio.CancelledError):
            await producer
        with cache.source_lock(root, '/uploads/a.png', timeout=.1):
            cache.purge_source_locked(root, '/uploads/a.png')
    asyncio.run(scenario())


def test_delete_coordinates_before_input_and_retains_anchor(root):
    first = run(root)
    folder = root / '.image-cache/v1' / first.source_key
    anchor = (folder / '.lock').stat().st_ino
    with pytest.raises(ValueError):
        cache.purge_source_locked(root, '/uploads/a.png')
    async def scenario():
        with cache.source_lock(root, '/uploads/a.png'):
            producer = asyncio.create_task(cache.get_derivative('/uploads/a.png', cache.DerivativeSpec(), upload_root=root))
            await asyncio.sleep(.06)
            (root / 'a.png').unlink()
            cache.purge_source_locked(root, '/uploads/a.png')
        with pytest.raises(ValueError):
            await producer
    asyncio.run(scenario())
    assert (folder / '.lock').stat().st_ino == anchor
    assert [p.name for p in folder.iterdir()] == ['.lock']


def test_remote_ttl_and_explicit_permission(root, monkeypatch):
    calls = []
    clock = [1000.0]
    async def download(source):
        calls.append(1)
        return png('red' if len(calls) == 1 else 'blue'), 'image/png'
    monkeypatch.setattr(cache, 'download_image', download)
    monkeypatch.setattr(cache, '_now', lambda: clock[0])
    source = 'https://example.invalid/private.png?token=secret'
    with pytest.raises(ValueError):
        run(root, source=source)
    assert not calls
    first = run(root, source=source, allow_remote=True)
    assert run(root, source=source, allow_remote=True) == first and len(calls) == 1
    clock[0] += cache.REMOTE_TTL_SECONDS + 1
    assert run(root, source=source, allow_remote=True).filename != first.filename
    assert len(calls) == 2
    for item in (root / '.image-cache').rglob('*'):
        if item.is_file():
            assert b'secret' not in item.read_bytes() and b'example.invalid' not in item.read_bytes()
    with pytest.raises(ValueError):
        run(root, source=source)
    assert len(calls) == 2


def test_data_urls_strict_and_bounded(root, monkeypatch):
    source = 'data:image/png;base64,' + base64.b64encode(png()).decode()
    assert run(root, source=source, allow_remote=True).mime_type == 'image/webp'
    for invalid in [source, 'data:image/svg+xml;base64,PHN2Zz4=', 'data:image/png;base64,***', 'data:image/jpeg;base64,' + base64.b64encode(png()).decode(), 'ftp://example.invalid/a.png']:
        with pytest.raises(ValueError):
            run(root, source=invalid, allow_remote=invalid != source)


def test_budget_refuses_and_variant_cap(root, monkeypatch):
    monkeypatch.setattr(cache, 'MAX_CACHE_BYTES', 10)
    with pytest.raises(ValueError):
        run(root)
    assert not list((root / '.image-cache').rglob('*.webp'))
    monkeypatch.setattr(cache, 'MAX_CACHE_BYTES', 256 * 1024 * 1024)
    monkeypatch.setattr(cache, 'MAX_VARIANTS', 2)
    run(root)
    run(root, cache.DerivativeSpec(quality=70))
    with pytest.raises(ValueError):
        run(root, cache.DerivativeSpec(quality=60))


def test_cache_parent_permissions_and_deep_metadata(root):
    first = run(root)
    parent = root / '.image-cache'
    parent.chmod(0o777)
    try:
        with pytest.raises(ValueError):
            run(root)
    finally:
        parent.chmod(0o700)
    folder = parent / 'v1' / first.source_key
    (folder / (first.filename + '.json')).write_bytes(b'[' * 1100 + b']' * 1100)
    assert run(root) == first


def test_corrupt_remote_snapshot_symlink_is_replaced(root, tmp_path, monkeypatch):
    async def download(source):
        return png(), 'image/png'
    monkeypatch.setattr(cache, 'download_image', download)
    source = 'https://example.invalid/x'
    first = run(root, source=source, allow_remote=True)
    outside = tmp_path / 'outside'
    outside.write_bytes(b'do not touch')
    snapshot = root / '.image-cache/v1' / first.source_key / 'snapshot.bin'
    snapshot.unlink()
    snapshot.symlink_to(outside)
    assert run(root, source=source, allow_remote=True) == first
    assert not snapshot.is_symlink()
    assert outside.read_bytes() == b'do not touch'


def test_twenty_distinct_sources_real_decoder_queue(root):
    for index in range(20):
        (root / f'{index}.png').write_bytes(png())
    async def scenario():
        return await asyncio.gather(*(cache.get_derivative(f'/uploads/{i}.png', cache.DerivativeSpec(), upload_root=root) for i in range(20)))
    results = asyncio.run(scenario())
    assert len(results) == 20
    assert len({item.source_key for item in results}) == 20
    assert all(item.mime_type == 'image/webp' for item in results)


def test_source_count_size_caps_and_atomic_failure(root, monkeypatch):
    monkeypatch.setattr(cache, 'MAX_SOURCE_BYTES', 10)
    with pytest.raises(ValueError):
        run(root)
    monkeypatch.setattr(cache, 'MAX_SOURCE_BYTES', 24 * 1024 * 1024)
    monkeypatch.setattr(cache, 'MAX_SOURCES', 1)
    (root / 'b.png').write_bytes(png())
    with pytest.raises(ValueError):
        run(root, source='/uploads/b.png')
    original = cache.os.replace
    def fail(*args, **kwargs):
        raise OSError('sensitive/path/token')
    monkeypatch.setattr(cache.os, 'replace', fail)
    with pytest.raises(ValueError) as error:
        run(root)
    assert 'sensitive' not in str(error.value)
    assert not list((root / '.image-cache').rglob('.tmp-*'))
    monkeypatch.setattr(cache.os, 'replace', original)
    assert run(root).data
    (root / 'a.png').write_bytes(b'x' * (cache.MAX_ENCODED_BYTES + 1))
    with pytest.raises(ValueError):
        run(root)


def test_producer_then_delete_and_inherited_context_cannot_purge(root, monkeypatch):
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        original = cache.transform_image
        async def paused(*args, **kwargs):
            entered.set()
            await release.wait()
            return await original(*args, **kwargs)
        monkeypatch.setattr(cache, 'transform_image', paused)
        producer = asyncio.create_task(cache.get_derivative('/uploads/a.png', cache.DerivativeSpec(), upload_root=root))
        await entered.wait()
        with pytest.raises(ValueError):
            with cache.source_lock(root, '/uploads/a.png', timeout=.03):
                pass
        release.set()
        result = await producer
        with cache.source_lock(root, '/uploads/a.png'):
            async def child():
                with pytest.raises(ValueError):
                    cache.purge_source_locked(root, '/uploads/a.png')
            await asyncio.create_task(child())
            (root / 'a.png').unlink()
            cache.purge_source_locked(root, '/uploads/a.png')
        with pytest.raises(ValueError):
            cache.read_cached(root, result.source_key, result.filename)
    asyncio.run(scenario())


def _produce_in_process(root, start, results):
    original = cache.transform_image
    async def counted(*args, **kwargs):
        with (Path(root) / 'encode-count').open('ab') as handle:
            handle.write(b'1')
        return await original(*args, **kwargs)
    cache.transform_image = counted
    start.wait(5)
    try:
        results.put(run(Path(root)).filename)
    except Exception as error:
        results.put(type(error).__name__)


def test_cross_process_singleflight_real_encoder(root):
    ctx = multiprocessing.get_context('fork')
    start, results = ctx.Event(), ctx.Queue()
    processes = [ctx.Process(target=_produce_in_process, args=(str(root), start, results)) for _ in range(3)]
    for process in processes:
        process.start()
    start.set()
    try:
        names = [results.get(timeout=20) for _ in processes]
        assert len(set(names)) == 1 and names[0].endswith('.webp')
        assert (root / 'encode-count').read_bytes() == b'1'
    finally:
        for process in processes:
            process.join(5)
            if process.is_alive():
                process.kill()
                process.join()
        results.close()
    assert all(process.exitcode == 0 for process in processes)


def _hold_in_process(root, ready, release):
    with cache.source_lock(Path(root), '/uploads/a.png'):
        ready.set()
        release.wait(5)


def test_flock_separate_process(root):
    ctx = multiprocessing.get_context('fork')
    ready, release = ctx.Event(), ctx.Event()
    process = ctx.Process(target=_hold_in_process, args=(str(root), ready, release))
    process.start()
    try:
        assert ready.wait(3)
        with pytest.raises(ValueError):
            with cache.source_lock(root, '/uploads/a.png', timeout=.05):
                pass
    finally:
        release.set()
        process.join(5)
        if process.is_alive():
            process.kill()
            process.join()
    assert process.exitcode == 0
    assert run(root).data
