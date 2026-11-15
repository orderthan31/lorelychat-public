"""Actual installed LiteLLM/google-auth, synthetic httpx wires; no paid calls."""
import asyncio
import base64
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
from io import BytesIO
import json
import logging
import socket
from types import SimpleNamespace
from urllib.parse import parse_qs
import warnings

import httpx
from PIL import Image
import pytest

from app.agent import image_generation as images, image_google as google
from app.engine import litellm_transport as transport, vertex_auth

MODELS = tuple(model for model, _ in google.MODELS)


@pytest.fixture(autouse=True)
def isolated_network_and_clock(monkeypatch):
    # The suite conftest installs synthetic roots before application imports.
    for name in ('connect', 'connect_ex', 'sendto'):
        original = getattr(socket.socket, name)
        def deny(sock, *args, _original=original, **kwargs):
            if sock.family in (socket.AF_INET, socket.AF_INET6):
                raise AssertionError('Unmocked network forbidden')
            return _original(sock, *args, **kwargs)
        monkeypatch.setattr(socket.socket, name, deny)
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None): return datetime(2026, 11, 15, tzinfo=tz or timezone.utc)
    monkeypatch.setattr(google, 'datetime', Clock)
    # A fallback to ambient configuration must be observable, never useful.
    monkeypatch.setenv('GOOGLE_APPLICATION_CREDENTIALS', '/forbidden-adc-file')
    monkeypatch.setenv('GEMINI_API_KEY', 'ambient-key-never-use')
    monkeypatch.setenv('GEMINI_API_BASE', 'https://ambient.invalid')
    monkeypatch.setenv('VERTEXAI_PROJECT', 'ambient-project')
    monkeypatch.setenv('VERTEXAI_LOCATION', 'ambient-location')
    from google import auth as google_auth
    monkeypatch.setattr(google_auth, 'default', lambda *a, **k: pytest.fail('ADC invoked'))


@pytest.fixture(scope='module')
def accounts():
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives.serialization import Encoding, PrivateFormat, NoEncryption
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()).decode()
    def account(name):
        return json.dumps({'type': 'service_account', 'project_id': f'{name}-project',
            'private_key_id': f'{name}-id', 'private_key': pem,
            'client_email': f'fixture@{name}-project.iam.gserviceaccount.com',
            'client_id': '12345678', 'token_uri': vertex_auth.TOKEN_URI})
    return account('first'), account('second')


def fixture(provider='google', model=MODELS[0], fmt='PNG', mode='reference', key='private-key-sentinel', location='global'):
    buf = BytesIO()
    Image.new('RGB', (8, 8), 'blue').save(buf, format=fmt)
    raw = buf.getvalue()
    ref = {'data': raw, 'mime_type': Image.MIME[fmt], 'sha256': hashlib.sha256(raw).hexdigest()}
    plan = {'provider_account_id': 'registered-fixture', 'model_id': model, 'prompt': 'private-prompt-sentinel',
            'count': 1, 'options': {}, 'generation_mode': mode,
            'reference': {'snapshot_id': 'frozen-snapshot', 'source_id': 'asset:fixture',
                          'mime_type': ref['mime_type'], 'sha256': ref['sha256']} if mode == 'reference' else None}
    vertex = provider == 'vertex_ai'
    route = transport.Route(provider, 'vertex_ai' if vertex else 'gemini', model,
        f'https://{vertex_auth.endpoint_host(location)}/v1' if vertex else google.GEMINI_BASE,
        key, 'selected-project' if vertex else None, location if vertex else None,
        'service_account' if vertex else None)
    return plan, route, ref


def response(ref, **extra):
    return {'candidates': [{'finishReason': 'STOP', 'content': {'role': 'model', 'parts': [
        {'text': 'not retained'}, {'inlineData': {'mimeType': ref['mime_type'],
            'data': base64.b64encode(ref['data']).decode()}}]}}], **extra}


def token_wire(monkeypatch, calls, *, status=200, payload=None):
    def wire(request):
        assert request.method == 'POST' and str(request.url) == vertex_auth.TOKEN_URI
        fields = parse_qs(request.content.decode())
        assert fields['grant_type'] == ['urn:ietf:params:oauth:grant-type:jwt-bearer']
        claim = json.loads(base64.urlsafe_b64decode(fields['assertion'][0].split('.')[1] + '=='))
        assert claim['aud'] == vertex_auth.TOKEN_URI and claim['scope'] == vertex_auth.SCOPES[0]
        calls.append(claim['iss'])
        return httpx.Response(status, json=payload if payload is not None else {
            'access_token': 'synthetic-access-token', 'token_type': 'Bearer', 'expires_in': 3600})
    monkeypatch.setattr(vertex_auth, 'new_auth_transport', lambda: httpx.MockTransport(wire))


@pytest.mark.parametrize('provider', ['google', 'vertex_ai'])
@pytest.mark.parametrize('model', MODELS)
@pytest.mark.parametrize('mode', ['new', 'reference'])
@pytest.mark.parametrize('fmt', ['PNG', 'JPEG', 'WEBP'])
def test_actual_google_sdk_wire(monkeypatch, accounts, provider, model, mode, fmt, capsys, caplog):
    caplog.set_level(logging.DEBUG)
    plan, route, ref = fixture(provider, model, fmt, mode, accounts[0] if provider == 'vertex_ai' else 'private-key-sentinel')
    auth, sends, checks, closed = [], [], [], []
    token_wire(monkeypatch, auth)
    class Wire(httpx.MockTransport):
        async def aclose(self): closed.append(1)
    def wire(request):
        sends.append(request)
        assert request.method == 'POST' and not request.url.query
        expected = (f'{route.base_url}/projects/selected-project/locations/global/publishers/google/models/{model}:generateContent'
                    if provider == 'vertex_ai' else f'{route.base_url}/models/{model}:generateContent')
        assert str(request.url) == expected
        assert request.headers['content-type'] == 'application/json'
        if provider == 'vertex_ai':
            assert request.headers['authorization'] == 'Bearer synthetic-access-token'
            assert 'x-goog-api-key' not in request.headers
        else:
            assert request.headers['x-goog-api-key'] == 'private-key-sentinel'
            assert 'authorization' not in request.headers
        payload = json.loads(request.content)
        assert payload['generationConfig'] == {'response_modalities': ['IMAGE', 'TEXT'], 'candidateCount': 1}
        parts = payload['contents'][0]['parts']
        assert parts[-1] == {'text': plan['prompt']}
        assert len(parts) == (2 if mode == 'reference' else 1)
        if mode == 'reference':
            assert parts[0]['inlineData']['mimeType'] == ref['mime_type']
            assert base64.b64decode(parts[0]['inlineData']['data'], validate=True) == ref['data']
        return httpx.Response(200, json=response(ref))
    monkeypatch.setattr(transport, 'new_http_transport', lambda: Wire(wire))
    with warnings.catch_warnings(record=True) as seen:
        outputs = asyncio.run(images.image_completion(route, plan,
            reference=ref if mode == 'reference' else None, before_send=lambda: checks.append(1)))
    assert len(sends) == 1 and closed == [1]
    assert len(checks) == (2 if provider == 'vertex_ai' else 1)
    assert len(auth) == (1 if provider == 'vertex_ai' else 0)
    assert outputs[0]['data'] == ref['data'] and outputs[0]['sha256'] == ref['sha256']
    observed = capsys.readouterr(); text = observed.out + observed.err + caplog.text + str([str(w.message) for w in seen])
    # Pydantic lazily emits a static SDK TypedDict schema warning on first
    # GenericLiteLLMParams construction; it contains no request/provider data.
    assert all('TypedDict class' in str(w.message) for w in seen)
    for secret in (plan['prompt'], route.api_key, 'synthetic-access-token', base64.b64encode(ref['data']).decode()):
        assert secret not in text


def test_catalog_explicit_metadata_not_media_input_or_alias():
    for provider in ('google', 'gemini', 'vertex_ai'):
        choices = images.list_image_models(provider)
        assert {m['id'] for m in choices} == set(MODELS)
        assert all(m['supports_reference'] and m['max_count'] == 1 for m in choices)
        assert all(m['provider_type'] == provider for m in choices)
    fake = {f'gemini/{MODELS[0]}': {'mode': 'chat', 'litellm_provider': 'gemini', 'supports_vision': True}}
    assert google.catalog('google', fake) == []
    assert images.list_image_models('openrouter') == []


@pytest.mark.parametrize('provider', ['google', 'vertex_ai'])
@pytest.mark.parametrize('mode', ['new', 'reference'])
@pytest.mark.parametrize('status', [429, 503, 307])
def test_no_inference_retry_redirect_or_fallback(monkeypatch, accounts, provider, mode, status, capsys, caplog):
    plan, route, ref = fixture(provider, mode=mode, key=accounts[0] if provider == 'vertex_ai' else 'private-key-sentinel')
    auth, sends = [], []
    token_wire(monkeypatch, auth)
    def wire(request):
        sends.append(request.url.path)
        return httpx.Response(status, headers={'location': 'https://forbidden.invalid'},
            json={'error': {'message': 'private-prompt-sentinel private-key-sentinel'}})
    monkeypatch.setattr(transport, 'new_http_transport', lambda: httpx.MockTransport(wire))
    with warnings.catch_warnings(record=True) as seen, pytest.raises(images.ImagePlanError) as error:
        asyncio.run(images.image_completion(route, plan, reference=ref if mode == 'reference' else None, before_send=lambda: None))
    assert len(sends) == 1 and len(auth) == (1 if provider == 'vertex_ai' else 0)
    output = capsys.readouterr()
    observed = output.out + output.err + caplog.text + str(error.value) + str([str(w.message) for w in seen])
    assert 'private-prompt-sentinel' not in observed and 'private-key-sentinel' not in observed


@pytest.mark.parametrize('bad', ['count', 'options', 'mime', 'digest', 'bytes', 'oversize', 'new-ref', 'model', 'route', 'key', 'adc', 'location'])
def test_invalid_google_preflight_zero_auth_or_wire(monkeypatch, accounts, bad):
    plan, route, ref = fixture('vertex_ai', key=accounts[0])
    if bad == 'count': plan['count'] = 2
    elif bad == 'options': plan['options'] = {'size': '1024x1024'}
    elif bad == 'mime': ref['mime_type'] = 'image/jpeg'
    elif bad == 'digest': ref['sha256'] = '0' * 64
    elif bad == 'bytes': ref['data'] = b'bad'
    elif bad == 'oversize': ref['data'] = b'x' * (images.MAX_IMAGE_BYTES + 1)
    elif bad == 'new-ref': plan['generation_mode'] = 'new'
    elif bad == 'model': plan['model_id'] = route.model + '-preview'
    elif bad == 'route': route = replace(route, base_url='https://forbidden.invalid')
    elif bad == 'key': route = replace(route, api_key='')
    elif bad == 'adc': route = replace(route, vertex_auth_mode='adc')
    elif bad == 'location': route = replace(route, vertex_location='us-central1')
    monkeypatch.setattr(vertex_auth, 'new_auth_transport', lambda: pytest.fail('Auth for invalid input'))
    monkeypatch.setattr(transport, 'new_http_transport', lambda: pytest.fail('Wire for invalid input'))
    with pytest.raises(images.ImagePlanError):
        asyncio.run(images.image_completion(route, plan, reference=ref, before_send=lambda: pytest.fail('dispatch')))


@pytest.mark.parametrize('bad', ['url', 'url-plus-image', 'mime', 'badbytes', 'noimage', 'blocked', 'partial', 'two', 'oversize', 'pixels'])
def test_output_rejected_without_url_fetch(monkeypatch, bad):
    plan, route, ref = fixture(mode='new')
    body = response(ref)
    part = body['candidates'][0]['content']['parts'][1]
    if bad == 'url': part.clear(); part['fileData'] = {'fileUri': 'https://forbidden.invalid/image'}
    elif bad == 'url-plus-image': part['fileData'] = {'fileUri': 'https://forbidden.invalid/image'}
    elif bad == 'mime': part['inlineData']['mimeType'] = 'image/jpeg'
    elif bad == 'badbytes': part['inlineData']['data'] = 'not-base64!'
    elif bad == 'noimage': body['candidates'][0]['content']['parts'] = [{'text': 'no image'}]
    elif bad == 'blocked': body['promptFeedback'] = {'blockReason': 'SAFETY'}
    elif bad == 'partial': body['candidates'][0]['finishReason'] = 'MAX_TOKENS'
    elif bad == 'two': body['candidates'][0]['content']['parts'].append(deepcopy(part))
    elif bad == 'oversize': part['inlineData']['data'] = 'a' * (images.MAX_IMAGE_BYTES * 4 // 3 + 65537)
    elif bad == 'pixels': monkeypatch.setattr(images, 'MAX_PIXELS', 63)
    sends = []
    def wire(request): sends.append(1); return httpx.Response(200, json=body)
    monkeypatch.setattr(transport, 'new_http_transport', lambda: httpx.MockTransport(wire))
    with pytest.raises(images.ImagePlanError):
        asyncio.run(images.image_completion(route, plan, reference=None, before_send=lambda: None))
    assert sends == [1]


@pytest.mark.parametrize('status', [429, 503, 307])
def test_auth_failure_bounded_separate_no_inference(monkeypatch, accounts, status):
    plan, route, ref = fixture('vertex_ai', key=accounts[0])
    auth = []
    token_wire(monkeypatch, auth, status=status, payload={'error': 'synthetic-auth-secret'})
    monkeypatch.setattr(transport, 'new_http_transport', lambda: pytest.fail('Inference after failed auth'))
    with pytest.raises(images.ImagePlanError):
        asyncio.run(images.image_completion(route, plan, reference=ref, before_send=lambda: None))
    assert len(auth) == 1


def test_request_owned_account_auth_and_canonical_overrides(monkeypatch, accounts):
    calls, auth, sent = [], [], []
    token_wire(monkeypatch, auth)
    account = SimpleNamespace(id='registered-fixture', provider_type='vertex_ai', api_key_secret_ref='key-ref',
        base_url=None, vertex_project='selected-project', vertex_location='global', vertex_auth_mode='service_account')
    for key in accounts:
        def secret(ref): calls.append(ref); return key
        values = images.build_image_overrides(account, SimpleNamespace(get_secret=secret))
        assert values['vertex_project'] == 'selected-project' and values['vertex_auth_mode'] == 'service_account'
        route = images.resolve_image_route_overrides({**values, 'model': MODELS[0]})
        plan, _, ref = fixture('vertex_ai', key=key)
        def wire(request): sent.append(request.url); return httpx.Response(200, json=response(ref))
        monkeypatch.setattr(transport, 'new_http_transport', lambda: httpx.MockTransport(wire))
        assert asyncio.run(images.image_completion(route, plan, reference=ref, before_send=lambda: None))[0]['data'] == ref['data']
    assert calls == ['key-ref', 'key-ref'] and len(sent) == 2
    assert auth == ['fixture@first-project.iam.gserviceaccount.com', 'fixture@second-project.iam.gserviceaccount.com']
    assert account.vertex_location == 'global'


@pytest.mark.parametrize('location, model', [('us', 'gemini-3.1-flash-image'), ('eu', 'gemini-3.1-flash-image'), ('us-central1', 'gemini-2.5-flash-image')])
def test_vertex_location_preserved_real_wire(monkeypatch, accounts, location, model):
    plan, route, ref = fixture('vertex_ai', model=model, key=accounts[0], location=location)
    auth, sends = [], []
    token_wire(monkeypatch, auth)
    def wire(request):
        sends.append(1)
        assert request.url.host == vertex_auth.endpoint_host(location)
        assert f'/locations/{location}/' in request.url.path
        return httpx.Response(200, json=response(ref))
    monkeypatch.setattr(transport, 'new_http_transport', lambda: httpx.MockTransport(wire))
    assert asyncio.run(images.image_completion(route, plan, reference=ref, before_send=lambda: None))[0]['data'] == ref['data']
    assert sends == [1]


def test_account_catalog_filters_location_and_adc_without_changes():
    account = SimpleNamespace(provider_type='vertex_ai', vertex_project='selected-project', vertex_location='global',
                              vertex_auth_mode='service_account', base_url=None)
    assert len(images.list_image_models_for_account(account)) == 5
    account.vertex_location = 'us'
    assert [m['id'] for m in images.list_image_models_for_account(account)] == ['gemini-3.1-flash-image']
    account.vertex_location = 'us-central1'
    assert [m['id'] for m in images.list_image_models_for_account(account)] == ['gemini-2.5-flash-image']
    account.vertex_auth_mode = 'adc'
    assert images.list_image_models_for_account(account) == []
    assert account.vertex_auth_mode == 'adc' and account.vertex_location == 'us-central1'


def test_google_provider_canonical_route_and_existing_auth():
    account = SimpleNamespace(id='registered-google', provider_type='google', api_key_secret_ref='key-ref', base_url=None)
    values = images.build_image_overrides(account, SimpleNamespace(get_secret=lambda ref: 'selected-key'))
    assert not any(key.startswith('vertex_') for key in values)
    route = images.resolve_image_route_overrides({**values, 'model': MODELS[0]})
    assert route.provider == 'google' and route.sdk_provider == 'gemini' and route.api_key == 'selected-key'


def test_shutdown_cutoff_no_implicit_replacement(monkeypatch):
    class Clock:
        @staticmethod
        def now(tz): return datetime(2027, 6, 28, tzinfo=tz)
    monkeypatch.setattr(google, 'datetime', Clock)
    assert [m['id'] for m in images.list_image_models('vertex_ai')] == [MODELS[0]]
    assert 'gemini-2.5-flash-image' not in {m['id'] for m in images.list_image_models('google')}


def test_single_send_and_revocation_guard(monkeypatch):
    plan, route, ref = fixture()
    sent, closed = [], []
    class Wire(httpx.MockTransport):
        async def aclose(self): closed.append(1)
    monkeypatch.setattr(transport, 'new_http_transport', lambda: Wire(lambda req: sent.append(1)))
    def revoke(): raise ValueError('private-prompt-sentinel')
    with pytest.raises(images.ImagePlanError):
        asyncio.run(images.image_completion(route, plan, reference=ref, before_send=revoke))
    assert sent == [] and closed == [1]


@pytest.mark.parametrize('kind', ['connect', 'protocol', 'timeout', 'cancel'])
def test_transport_failure_never_recreates_client(monkeypatch, kind):
    plan, route, ref = fixture()
    calls, created, closed = [], [], []
    class Wire(httpx.MockTransport):
        async def aclose(self): closed.append(1)
    def wire(request):
        calls.append(1)
        errors = {'connect': httpx.ConnectError, 'protocol': httpx.RemoteProtocolError,
                  'timeout': httpx.ReadTimeout, 'cancel': asyncio.CancelledError}
        raise errors[kind]('private-prompt-sentinel')
    def factory(): created.append(1); return Wire(wire)
    monkeypatch.setattr(transport, 'new_http_transport', factory)
    transport.load_sdk()
    from litellm.llms.custom_httpx.http_handler import AsyncHTTPHandler
    original = AsyncHTTPHandler.create_client
    builds = []
    def create(self, *args, **kwargs): builds.append(1); return original(self, *args, **kwargs)
    monkeypatch.setattr(AsyncHTTPHandler, 'create_client', create)
    with pytest.raises(asyncio.CancelledError if kind == 'cancel' else images.ImagePlanError):
        asyncio.run(images.image_completion(route, plan, reference=ref, before_send=lambda: None))
    assert calls == created == closed == builds == [1]


def test_duplicate_transport_send_cannot_reach_provider():
    plan, route, ref = fixture(mode='new')
    sends, checks = [], []
    endpoint = f'{route.base_url}/models/{route.model}:generateContent'
    def wire(request): sends.append(1); return httpx.Response(200, json=response(ref))
    guard = google.GoogleImageSendTransport(httpx.MockTransport(wire), lambda: checks.append(1),
        endpoint=endpoint, plan=plan, reference=None)
    payload = {'contents': [{'parts': [{'text': plan['prompt']}]}],
               'generationConfig': {'response_modalities': ['IMAGE', 'TEXT'], 'candidateCount': 1}}
    async def run():
        async with httpx.AsyncClient(transport=guard, trust_env=False) as client:
            await client.post(endpoint, json=payload)
            with pytest.raises(transport.AdditionalSendDenied): await client.post(endpoint, json=payload)
    asyncio.run(run())
    assert guard.denied and sends == checks == [1]


def test_vertex_revoke_after_oauth_zero_inference(monkeypatch, accounts):
    plan, route, ref = fixture('vertex_ai', key=accounts[0])
    auth, checks, closed = [], [], []
    token_wire(monkeypatch, auth)
    class Wire(httpx.MockTransport):
        async def aclose(self): closed.append(1)
    monkeypatch.setattr(transport, 'new_http_transport', lambda: Wire(lambda req: pytest.fail('revoked send')))
    def check():
        checks.append(1)
        if len(checks) == 2: raise ValueError('revoked')
    with pytest.raises(images.ImagePlanError):
        asyncio.run(images.image_completion(route, plan, reference=ref, before_send=check))
    assert len(auth) == 1 and len(checks) == 2 and closed == [1]


def test_credential_url_is_fixed_before_any_auth(monkeypatch, accounts):
    plan, route, ref = fixture('vertex_ai', key=accounts[0])
    info = json.loads(accounts[0]); info['token_uri'] = 'https://forbidden.invalid/token'
    route = replace(route, api_key=json.dumps(info))
    monkeypatch.setattr(vertex_auth, 'new_auth_transport', lambda: pytest.fail('unvalidated credential transport'))
    with pytest.raises(images.ImagePlanError):
        asyncio.run(images.image_completion(route, plan, reference=ref, before_send=lambda: None))


def test_sdk_global_vertex_auth_is_not_used(monkeypatch, accounts):
    transport.load_sdk()
    from litellm.llms.vertex_ai.gemini.vertex_and_google_ai_studio_gemini import VertexLLM
    monkeypatch.setattr(VertexLLM, '_ensure_access_token', lambda *a, **k: pytest.fail('SDK credential cache'))
    monkeypatch.setattr(VertexLLM, '_ensure_access_token_async', lambda *a, **k: pytest.fail('SDK credential cache'))
    plan, route, ref = fixture('vertex_ai', key=accounts[0])
    auth = []
    token_wire(monkeypatch, auth)
    monkeypatch.setattr(transport, 'new_http_transport', lambda: httpx.MockTransport(lambda req: httpx.Response(200, json=response(ref))))
    assert asyncio.run(images.image_completion(route, plan, reference=ref, before_send=lambda: None))[0]['data'] == ref['data']
    assert len(auth) == 1


@pytest.mark.parametrize('usage', [None, 'private-metadata-sentinel', [], {'totalTokenCount': 'private-metadata-sentinel'}])
def test_unknown_metadata_never_sdk_serialized_or_logged(monkeypatch, usage, capsys, caplog):
    caplog.set_level(logging.DEBUG)
    plan, route, ref = fixture()
    body = response(ref, usageMetadata=usage, debug={'private-metadata-sentinel': 'private-key-sentinel'})
    # Thinking image parts are verified but never mistaken for final output.
    thought = deepcopy(body['candidates'][0]['content']['parts'][1]); thought['thought'] = True
    body['candidates'][0]['content']['parts'].insert(0, thought)
    monkeypatch.setattr(transport, 'new_http_transport', lambda: httpx.MockTransport(lambda req: httpx.Response(200, json=body)))
    with warnings.catch_warnings(record=True) as seen:
        result = asyncio.run(images.image_completion(route, plan, reference=ref, before_send=lambda: None))
    assert len(result) == 1
    out = capsys.readouterr()
    evidence = out.out + out.err + caplog.text + str([str(w.message) for w in seen])
    assert 'private-metadata-sentinel' not in evidence and 'private-key-sentinel' not in evidence
