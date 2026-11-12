"""Actual native resize/transcode for provider-specific Decision media."""
import asyncio
from io import BytesIO
from types import SimpleNamespace

from PIL import Image
import pytest

from app.services import decision_asset_media as media
from app.services import image_derivative_cache as cache
from app.services.decision_transport import DecisionRoute


def route(*, model='gemini-decision-1', modalities=('text','image'), mimes=('image/webp',)):
    return DecisionRoute('google',model,'synthetic',input_modalities=modalities,image_mime_types=mimes)


def encoded(size=(1200,800), fmt='PNG'):
    buffer=BytesIO(); Image.new('RGB',size,(80,130,170)).save(buffer,format=fmt)
    return buffer.getvalue()


@pytest.mark.parametrize('modalities,mimes', [((),()), (('text',),('image/webp',)), (('text','image'),())])
def test_unknown_or_text_only_media_never_reads_files(monkeypatch, modalities, mimes):
    monkeypatch.setattr(cache,'get_derivative',lambda *_:pytest.fail('unsupported route read image'))
    assert asyncio.run(media.prepare_images(route(modalities=modalities,mimes=mimes),[{'id':'a','image_url':'/uploads/a.png'}])) == []


def test_large_owned_source_becomes_small_webp_without_replacing_original(tmp_path, monkeypatch):
    source=encoded((2400,1600));(tmp_path/'a.png').write_bytes(source)
    monkeypatch.setattr(media,'get_settings',lambda:SimpleNamespace(upload_root=str(tmp_path)))
    result=asyncio.run(media.prepare_images(route(),[{'id':'a','image_url':'/uploads/a.png'}]))
    assert len(result)==1 and result[0].mime_type=='image/webp'
    with Image.open(BytesIO(result[0].data)) as image:
        assert image.format=='WEBP' and max(image.size)<=512
    assert len(result[0].data)<=media.MAX_IMAGE_BYTES
    assert (tmp_path/'a.png').read_bytes()==source


def test_jev_without_webp_gets_bounded_baseline_jpeg_with_minimum_dimensions(tmp_path, monkeypatch):
    (tmp_path/'narrow.webp').write_bytes(encoded((256,1024),'WEBP'))
    monkeypatch.setattr(media,'get_settings',lambda:SimpleNamespace(upload_root=str(tmp_path)))
    selected=route(model='typesafe/jev-1.13',mimes=('image/png','image/jpeg'))
    result=asyncio.run(media.prepare_images(selected,[{'id':'a','image_url':'/uploads/narrow.webp'}]))
    assert len(result)==1 and result[0].mime_type=='image/jpeg'
    with Image.open(BytesIO(result[0].data)) as image:
        assert image.format=='JPEG' and min(image.size)>=280 and max(image.size)<=512
        assert not image.info.get('progressive')
    assert (tmp_path/'narrow.webp').read_bytes().startswith(b'RIFF')


@pytest.mark.parametrize('url',['https://external.invalid/a.webp','file:///etc/passwd','/uploads/../secret.webp','/uploads/a.webp?key=secret'])
def test_unowned_or_remote_images_are_not_fetched(tmp_path,monkeypatch,url):
    monkeypatch.setattr(media,'get_settings',lambda:SimpleNamespace(upload_root=str(tmp_path/'private')))
    assert asyncio.run(media.prepare_images(route(),[{'id':'a','image_url':url}]))==[]


def test_total_candidate_cap_is_checked_before_file_read(monkeypatch):
    monkeypatch.setattr(cache,'get_derivative',lambda *_:pytest.fail('too many images should be text-only'))
    assert asyncio.run(media.prepare_images(route(),[{'id':str(i),'image_url':'/uploads/a.webp'} for i in range(9)]))==[]


def test_repeated_decision_calls_reuse_one_derivative_and_changed_source_invalidates(tmp_path, monkeypatch):
    from app.services import asset_image_service
    original = asset_image_service.transform_image
    calls = []
    async def counted(*args, **kwargs):
        calls.append(kwargs)
        return await original(*args, **kwargs)
    monkeypatch.setattr(cache, 'transform_image', counted)
    # The historical implementation imports this seam inside prepare_images.
    monkeypatch.setattr(asset_image_service, 'transform_image', counted)
    monkeypatch.setattr(media, 'get_settings', lambda: SimpleNamespace(upload_root=str(tmp_path)))
    (tmp_path / 'source.png').write_bytes(encoded((800, 600)))
    candidates = [{'id': 'a', 'image_url': '/uploads/source.png'}]
    first = asyncio.run(media.prepare_images(route(), candidates))
    second = asyncio.run(media.prepare_images(route(), candidates))
    assert len(first) == len(second) == 1 and first[0].data == second[0].data
    assert len(calls) == 1
    (tmp_path / 'source.png').write_bytes(encoded((900, 400)))
    changed = asyncio.run(media.prepare_images(route(), candidates))
    assert len(changed) == 1 and changed[0].data != first[0].data and len(calls) == 2


def test_provider_mime_policy_reuses_its_own_cached_variant(tmp_path, monkeypatch):
    from app.services import asset_image_service
    original = asset_image_service.transform_image
    calls = []
    async def counted(*args, **kwargs):
        calls.append(kwargs['target_mime'])
        return await original(*args, **kwargs)
    monkeypatch.setattr(cache, 'transform_image', counted)
    # The historical implementation imports this seam inside prepare_images.
    monkeypatch.setattr(asset_image_service, 'transform_image', counted)
    monkeypatch.setattr(media, 'get_settings', lambda: SimpleNamespace(upload_root=str(tmp_path)))
    (tmp_path / 'source.png').write_bytes(encoded((800, 600)))
    candidates = [{'id': 'a', 'image_url': '/uploads/source.png'}]
    google = asyncio.run(media.prepare_images(route(), candidates))
    jev = route(model='typesafe/jev-1.14', mimes=('image/png', 'image/jpeg'))
    jpeg = asyncio.run(media.prepare_images(jev, candidates))
    again = asyncio.run(media.prepare_images(jev, candidates))
    assert google[0].mime_type == 'image/webp' and jpeg[0].mime_type == 'image/jpeg'
    assert jpeg[0].data == again[0].data and calls == ['image/webp', 'image/jpeg']
