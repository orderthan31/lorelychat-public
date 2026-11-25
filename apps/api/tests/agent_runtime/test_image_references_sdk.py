"""Real maintained SDK, fake wire only; no provider credentials or requests."""
import asyncio
import base64
from dataclasses import replace
from datetime import datetime, timezone
from email.parser import BytesParser
from email.policy import default
import hashlib
from io import BytesIO
import json
import logging
import warnings

import httpx
from PIL import Image
import pytest

from app.agent import image_generation as images
from app.engine import litellm_transport as transport

MODELS = ('gpt-image-2.5-sunburst', 'gpt-image-1.5', 'gpt-image-1-mini')


@pytest.fixture(autouse=True)
def admission_date(monkeypatch):
    # Wire parity is pinned to admission time; the cutoff test overrides this
    # clock explicitly to prove routes are removed at their real shutdown date.
    class AdmissionDate(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 11, 15, tzinfo=tz or timezone.utc)
    monkeypatch.setattr(images, 'datetime', AdmissionDate)


def fixture(fmt='PNG'):
    buf = BytesIO()
    Image.new('RGB', (8, 8), 'blue').save(buf, format=fmt)
    raw = buf.getvalue()
    ref = {'data': raw, 'mime_type': Image.MIME[fmt], 'sha256': hashlib.sha256(raw).hexdigest()}
    plan = {'provider_account_id': 'fixture', 'model_id': MODELS[0],
            'prompt': 'private-prompt-sentinel', 'count': 1, 'options': {}, 'generation_mode': 'reference',
            'reference': {'snapshot_id': 'snapshot', 'source_id': 'asset:fixture',
                          'sha256': ref['sha256'], 'mime_type': ref['mime_type']}}
    route = transport.Route('openai', 'openai', plan['model_id'],
                            'https://api.openai.com/v1', 'private-key-sentinel')
    return plan, route, ref


@pytest.mark.parametrize('model', MODELS)
@pytest.mark.parametrize('mode', ['new', 'reference'])
@pytest.mark.parametrize('fmt', ['PNG', 'JPEG', 'WEBP'])
def test_actual_sdk_wire(monkeypatch, model, mode, fmt, capsys, caplog):
    caplog.set_level(logging.DEBUG)
    plan, route, ref = fixture(fmt)
    plan.update(model_id=model, generation_mode=mode, count=2, options={'size': '1024x1024', 'quality': 'low'})
    if mode == 'new': plan['reference'] = None
    sends, callbacks, closed = [], [], []
    class Wire(httpx.MockTransport):
        async def aclose(self): closed.append(1)
    def wire(request):
        sends.append(request)
        assert request.method == 'POST'
        assert request.url.host == 'api.openai.com'
        assert request.url.path == '/v1/images/' + ('edits' if mode == 'reference' else 'generations')
        if mode == 'reference':
            message = BytesParser(policy=default).parsebytes(
                b'Content-Type: ' + request.headers['content-type'].encode() + b'\r\n\r\n' + request.content)
            fields = {p.get_param('name', header='content-disposition'): p for p in message.iter_parts()}
            assert fields['image'].get_payload(decode=True) == ref['data']
            assert fields['image'].get_content_type() == ref['mime_type']
            assert fields['model'].get_payload(decode=True).decode() == model
            assert fields['n'].get_payload(decode=True) == b'2'
            assert fields['quality'].get_payload(decode=True) == b'low'
            assert fields['size'].get_payload(decode=True) == b'1024x1024'
        else:
            payload = json.loads(request.content)
            assert payload['model'] == model and payload['n'] == 2
            assert 'image' not in payload
        return httpx.Response(200, json={'created': 1, 'data': [{'b64_json': base64.b64encode(ref['data']).decode()}] * 2})
    monkeypatch.setattr(transport, 'new_http_transport', lambda: Wire(wire))
    with warnings.catch_warnings(record=True) as seen:
        result = asyncio.run(images.image_completion(replace(route, model=model), plan,
            reference=ref if mode == 'reference' else None, before_send=lambda: callbacks.append(1)))
    assert len(sends) == 1 and callbacks == [1] and closed == [1]
    assert len(result) == 2 and result[0]['data'] == ref['data']
    assert result[0]['sha256'] == ref['sha256']
    output = capsys.readouterr()
    observed = output.out + output.err + caplog.text + str([str(w.message) for w in seen])
    assert not seen
    for secret in ('private-prompt-sentinel', 'private-key-sentinel', base64.b64encode(ref['data']).decode()):
        assert secret not in observed


@pytest.mark.parametrize('status', [429, 503, 307])
def test_edit_no_retry_redirect_or_fallback(monkeypatch, status, capsys, caplog):
    plan, route, ref = fixture()
    sends, callbacks = [], []
    def wire(request):
        sends.append(request.url.path)
        return httpx.Response(status, headers={'location': 'https://other.invalid'},
            json={'error': {'message': 'private-key-sentinel private-prompt-sentinel'}})
    monkeypatch.setattr(transport, 'new_http_transport', lambda: httpx.MockTransport(wire))
    with warnings.catch_warnings(record=True) as seen, pytest.raises(images.ImagePlanError) as error:
        asyncio.run(images.image_completion(route, plan, reference=ref, before_send=lambda: callbacks.append(1)))
    assert sends == ['/v1/images/edits'] and callbacks == [1]
    output = capsys.readouterr()
    observed = output.out + output.err + caplog.text + str(error.value) + str(seen)
    assert 'private-key-sentinel' not in observed and 'private-prompt-sentinel' not in observed


def test_capabilities_lifecycle_and_exact_model():
    models = {m['id']: m for m in images.list_image_models('openai')}
    assert set(models) == set(MODELS)
    assert all(m['supports_reference'] for m in models.values())
    for model in MODELS[1:]:
        assert models[model]['deprecated'] is True
        assert models[model]['shutdown_date'] == '2026-12-01'
    assert models[MODELS[0]]['deprecated'] is False
    xai = images.list_image_models('xai')
    assert len(xai) == 1 and xai[0]['id'] == 'grok-imagine-image'
    assert not xai[0]['supports_reference'] and xai[0]['reference_unavailable_reason']
    plan, route, _ = fixture()
    with pytest.raises(images.ImagePlanError): images.validate_plan(plan, replace(route, model='gpt-image-1'))


@pytest.mark.parametrize('change', [
    {'generation_mode': 'undecided'}, {'generation_mode': 'bad'}, {'model_id': 'gpt-image-1'},
    {'reference': None}, {'reference_images': ['private.png']}, {'reference_output_id': 'old'},
])
def test_invalid_plan_zero_client(monkeypatch, change):
    plan, route, ref = fixture()
    plan.update(change)
    monkeypatch.setattr(transport, 'new_http_transport', lambda: pytest.fail('Created transport for invalid plan'))
    with pytest.raises(images.ImagePlanError):
        asyncio.run(images.image_completion(route, plan, reference=ref, before_send=lambda: pytest.fail('callback')))


@pytest.mark.parametrize('kind', ['missing', 'mime', 'digest', 'badbytes', 'oversized', 'new-with-reference', 'pixels', 'public-digest'])
def test_readonly_reference_guard(monkeypatch, kind):
    plan, route, ref = fixture()
    if kind == 'missing': ref = None
    elif kind == 'mime': ref = {**ref, 'mime_type': 'image/jpeg'}
    elif kind == 'digest': ref = {**ref, 'sha256': '0' * 64}
    elif kind == 'badbytes': ref = {**ref, 'data': b'not-an-image'}
    elif kind == 'oversized': ref = {**ref, 'data': b'x' * (images.MAX_IMAGE_BYTES + 1)}
    elif kind == 'new-with-reference': plan['generation_mode'] = 'new'
    elif kind == 'public-digest': plan['reference']['sha256'] = '0' * 64
    elif kind == 'pixels': monkeypatch.setattr(images, 'MAX_PIXELS', 63)
    monkeypatch.setattr(transport, 'new_http_transport', lambda: pytest.fail('Created transport for invalid bytes'))
    with pytest.raises(images.ImagePlanError):
        asyncio.run(images.image_completion(route, plan, reference=ref, before_send=lambda: pytest.fail('callback')))


def test_xai_reference_rejected_without_send(monkeypatch):
    plan, route, ref = fixture()
    plan['model_id'] = 'grok-imagine-image'
    route = transport.Route('xai', 'xai', plan['model_id'], 'https://api.x.ai/v1', 'synthetic')
    monkeypatch.setattr(transport, 'new_http_transport', lambda: pytest.fail('unsupported edit client'))
    with pytest.raises(images.ImagePlanError, match='Reference'):
        asyncio.run(images.image_completion(route, plan, reference=ref, before_send=lambda: None))


def test_claim_reference_load_and_replay(monkeypatch):
    plan, route, ref = fixture()
    events = []
    class Service:
        claimed = False
        def claim_image_generation(self, *args):
            if self.claimed: return {'claimed': False, 'operation': self.operation}
            self.claimed = True
            events.append('claim')
            return {'claimed': True, 'plan': plan, 'job_id': 'job'}
        def require_enabled(self, *args): pass
        def resolve_image_route(self, *args): return route
        def load_image_reference(self, *args):
            events.append('load'); return ref
        def complete_image_job(self, principal, job_id, outputs, **kwargs):
            self.operation = {'count': len(outputs), **kwargs}; return self.operation
    async def completion(route, plan, *, before_send, reference=None):
        assert events == ['claim', 'load'] and reference is ref
        events.append('sdk')
        return [ref]
    monkeypatch.setattr(images, 'image_completion', completion)
    service = Service()
    async def run():
        one = await images.execute_image_plan(service, None, 'd', 'r', 1, 'h')
        two = await images.execute_image_plan(service, None, 'd', 'r', 1, 'h')
        assert one == two == {'count': 1}
    asyncio.run(run())
    assert events == ['claim', 'load', 'sdk']


def test_before_send_revocation_closes_without_send(monkeypatch):
    plan, route, ref = fixture()
    events = []
    class Wire(httpx.MockTransport):
        async def aclose(self): events.append('closed')
    monkeypatch.setattr(transport, 'new_http_transport', lambda: Wire(lambda request: pytest.fail('sent')))
    def revoke():
        events.append('check')
        raise ValueError('private-prompt-sentinel')
    with pytest.raises(images.ImagePlanError):
        asyncio.run(images.image_completion(route, plan, reference=ref, before_send=revoke))
    assert events == ['check', 'closed']


@pytest.mark.parametrize('payload', [
    {'created': 1, 'data': [{'url': 'https://not-fetched.invalid/private.png'}]},
    {'created': 1, 'data': [{'b64_json': 'malformed'}]},
    {'created': 1, 'data': []},
])
def test_edit_malformed_outputs_never_retained_or_refetched(monkeypatch, payload):
    plan, route, ref = fixture()
    calls = []
    def wire(request):
        calls.append(request.url.path)
        return httpx.Response(200, json=payload)
    monkeypatch.setattr(transport, 'new_http_transport', lambda: httpx.MockTransport(wire))
    with pytest.raises(images.ImagePlanError):
        asyncio.run(images.image_completion(route, plan, reference=ref, before_send=lambda: None))
    assert calls == ['/v1/images/edits']


@pytest.mark.parametrize('failure', ['load', 'invalid'])
def test_load_reference_rejected_before_sdk(monkeypatch, failure):
    plan, route, ref = fixture()
    class Service:
        def claim_image_generation(self, *args): return {'claimed': True, 'plan': plan, 'job_id': 'job'}
        def require_enabled(self, *args): pass
        def resolve_image_route(self, *args): return route
        def load_image_reference(self, *args):
            if failure == 'load': raise ValueError('private-key-sentinel')
            return {**ref, 'data': b'corrupted'}
        def complete_image_job(self, principal, job_id, outputs, **kwargs):
            assert not outputs
            return kwargs
    async def forbidden(*args, **kwargs): pytest.fail('SDK reached')
    monkeypatch.setattr(images, 'image_completion', forbidden)
    result = asyncio.run(images.execute_image_plan(Service(), None, 'd', 'r', 1, 'h'))
    assert result == {'status': 'failed', 'error': 'IMAGE_PREFLIGHT_FAILED'}


def test_legacy_no_mode_new_generation_and_xai_exact_route(monkeypatch):
    plan, _, ref = fixture()
    plan.pop('generation_mode')
    plan.pop('reference')
    plan['model_id'] = 'grok-imagine-image'
    route = transport.Route('xai', 'xai', plan['model_id'], 'https://api.x.ai/v1', 'synthetic')
    calls, checks = [], []
    def wire(request):
        calls.append(json.loads(request.content))
        assert request.url == 'https://api.x.ai/v1/images/generations'
        return httpx.Response(200, json={'created': 1, 'data': [{'b64_json': base64.b64encode(ref['data']).decode()}]})
    monkeypatch.setattr(transport, 'new_http_transport', lambda: httpx.MockTransport(wire))
    outputs = asyncio.run(images.image_completion(route, plan, before_send=lambda: checks.append(1)))
    assert len(calls) == 1 and checks == [1]
    assert calls[0]['model'] == 'grok-imagine-image' and calls[0]['response_format'] == 'b64_json'
    assert outputs[0]['data'] == ref['data']


def test_shutdown_date_excludes_without_rewriting(monkeypatch):
    from datetime import datetime, timezone
    class Clock:
        @staticmethod
        def now(tz): return datetime(2026, 12, 1, tzinfo=timezone.utc)
    monkeypatch.setattr(images, 'datetime', Clock)
    assert [m['id'] for m in images.list_image_models('openai')] == [MODELS[0]]


def test_checker_keeps_original_bytes_and_rejects_actual_pixel_bomb():
    plan, _, ref = fixture()
    result = images.verified_reference(plan, ref)
    assert result is not None
    assert result['data'] is ref['data'] and result == ref
    # A valid, compressed image over the real pixel ceiling, not a mocked decoder.
    buf = BytesIO()
    Image.new('1', (4097, 4096)).save(buf, format='PNG')
    raw = buf.getvalue()
    bad = {**ref, 'data': raw, 'sha256': hashlib.sha256(raw).hexdigest()}
    plan['reference']['sha256'] = bad['sha256']
    with pytest.raises(images.ImagePlanError): images.verified_reference(plan, bad)
