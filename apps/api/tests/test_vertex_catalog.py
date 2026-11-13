"""Synthetic Model Garden protocol fixtures; no entitlement or live-call claims."""
from types import SimpleNamespace

import httpx
import pytest

from app.engine import vertex_auth
from app.services import model_provider_service as service
from app.db.models import ModelProviderAccount, ModelOption, RuntimeSetting
from sqlmodel import select


def account(mode='adc', **kw):
    return ModelProviderAccount(id='vertex-' + mode, alias='Vertex', provider_type='vertex_ai',
        vertex_project='test-project', vertex_location='us-central1', vertex_auth_mode=mode, **kw)


def item(model, **kw):
    return dict(name='publishers/google/models/' + model, versionId='1', **kw)


def wire(monkeypatch, handler):
    from app.engine import vertex_catalog
    monkeypatch.setattr(vertex_catalog, 'new_catalog_transport', lambda: httpx.MockTransport(handler))
    monkeypatch.setattr(vertex_auth, '_acquire_token', lambda route, timeout: 'synthetic-token')
    return vertex_catalog


def test_paginated_fixed_endpoint_account_auth_and_normalization(monkeypatch):
    requests, routes = [], []
    def handler(req):
        requests.append(req)
        assert req.url.host == 'aiplatform.googleapis.com'
        assert req.url.path == '/v1beta1/publishers/google/models'
        assert req.headers['Authorization'] == 'Bearer synthetic-token'
        assert req.headers['x-goog-user-project'] == 'test-project'
        if len(requests) == 1:
            return httpx.Response(200, json={'publisherModels': [item('gemini-2.5-flash')], 'nextPageToken': 'next+/='})
        assert req.url.params['pageToken'] == 'next+/='
        return httpx.Response(200, json={'publisherModels': [item('gemini-2.5-pro-tts'), item('imagen-4.0-generate-001')]})
    wire(monkeypatch, handler)
    monkeypatch.setattr(vertex_auth, '_acquire_token', lambda route, timeout: routes.append(route) or 'synthetic-token')
    monkeypatch.setattr(service.provider_secret_service, 'get_secret', lambda ref: 'synthetic-json' if ref == 'ref' else None)
    fetched = service.fetch_provider_models(account('service_account', api_key_secret_ref='ref'))
    assert len(fetched) == 3 and len(requests) == 2
    assert routes[0].api_key == 'synthetic-json' and routes[0].vertex_auth_mode == 'service_account'
    assert fetched[0].family == 'gemini' and fetched[0].supports_chat
    assert fetched[1].supports_tts and not fetched[1].supports_chat
    assert not fetched[2].supports_chat and not fetched[2].supports_json


@pytest.mark.parametrize('model,chat,tts', [('gemini-2.5-flash',True,False),
    ('gemini-4-argon',True,False), ('gemini-4-carbon',True,False), ('gemini-4-hydrogen',True,False),
    ('gemini-2.5-pro-tts',False,True), ('gemini-3.8-flash-lite-tts',False,True), ('gemini-2.5-flash-image',False,False),
    ('gemini-decision-1',False,False), ('gemini-live-2.5-flash',False,False),
    ('text-embedding-005',False,False), ('gemma-3-27b-it',False,False)])
def test_truthful_capabilities(model,chat,tts):
    row = service._provider_model(model, None, 'vertex_ai', {})
    assert (row.supports_chat,row.supports_compression,row.supports_tts,row.supports_decision) == (chat,chat,tts,False)
    assert row.family == ('gemini' if model.startswith('gemini-') else 'custom')
    assert row.context_window_tokens is None


@pytest.mark.parametrize('response', [httpx.Response(403, text='credential-sentinel'),
    httpx.Response(302, headers={'location':'https://evil.invalid/credential-sentinel'}),
    httpx.Response(200, text='credential-sentinel'), httpx.Response(200,json={'publisherModels':'bad'}),
    httpx.Response(200,json={'publisherModels':[{'name':'https://evil.invalid'}]})])
def test_errors_are_safe_and_preserve_catalog(session,monkeypatch,response):
    wire(monkeypatch,lambda req: response)
    acct = account(); session.add(acct)
    old = ModelOption(id='old', key='old', provider_account_id=acct.id, provider_type='vertex_ai',model='gemini-old',label='Old',source='fetched',enabled=True)
    session.add(old); session.commit(); session.refresh(old)
    before = old.model_dump()
    with pytest.raises(ValueError) as caught:
        service.sync_models(session,acct.id)
    assert 'sentinel' not in str(caught.value)
    session.refresh(old)
    assert old.model_dump() == before


def test_empty_and_partial_failure_preserve_all_rows(session,monkeypatch):
    catalog = wire(monkeypatch,lambda req: httpx.Response(200,json={}))
    acct = account(); session.add(acct); session.commit()
    with pytest.raises(ValueError,match='no models'):
        service.sync_models(session,acct.id)
    monkeypatch.setattr(catalog,'new_catalog_transport',lambda: httpx.MockTransport(lambda req: httpx.Response(200,
        json={'publisherModels':[item('gemini-2.5-flash')],'nextPageToken':'same'})))
    with pytest.raises(ValueError):
        service.sync_models(session,acct.id)
    assert service.list_options(session,provider_account_id=acct.id) == []


def test_sync_preserves_manual_enabled_settings_and_other_accounts(session,monkeypatch):
    wire(monkeypatch, lambda req: httpx.Response(200,json={'publisherModels':[
        item('gemini-2.5-flash'),item('gemini-2.5-pro'),item('gemini-2.5-pro-tts')]}))
    acct = account(); session.add(acct)
    for model,source in [('gemini-2.5-flash','manual'),('gemini-2.5-pro','fetched')]:
        session.add(ModelOption(id=model,key=model,model=model,label='Custom',provider_type='vertex_ai',
            provider_account_id=acct.id,enabled=True,source=source,context_window_tokens=12345))
    session.commit()
    settings = [r.model_dump() for r in session.exec(select(RuntimeSetting)).all()]
    service.sync_models(session,acct.id)
    rows = {r.model:r for r in service.list_options(session,provider_account_id=acct.id)}
    assert rows['gemini-2.5-flash'].label == 'Custom' and rows['gemini-2.5-flash'].enabled
    assert rows['gemini-2.5-pro'].enabled and rows['gemini-2.5-pro'].context_window_tokens == 12345
    assert not rows['gemini-2.5-pro-tts'].enabled
    assert [r.model_dump() for r in session.exec(select(RuntimeSetting)).all()] == settings


def test_bounded_bytes_pages_time_and_no_proxy_redirects(monkeypatch):
    catalog = wire(monkeypatch,lambda req: httpx.Response(200,json={'publisherModels':[item('gemini-2.5-flash')]}))
    monkeypatch.setattr(catalog, 'MAX_RESPONSE_BYTES', 10)
    with pytest.raises(ValueError): service.fetch_provider_models(account())
    monkeypatch.setattr(catalog, 'MAX_RESPONSE_BYTES', 2_000_000)
    monkeypatch.setattr(catalog, 'MAX_PAGES', 1)
    monkeypatch.setattr(catalog,'new_catalog_transport',lambda: httpx.MockTransport(lambda req: httpx.Response(200,
        json={'publisherModels':[item('gemini-2.5-flash')],'nextPageToken':'next'})))
    with pytest.raises(ValueError): service.fetch_provider_models(account())


def test_adc_does_not_reuse_uploaded_credentials(monkeypatch):
    wire(monkeypatch,lambda req: httpx.Response(200,json={}))
    routes=[]
    monkeypatch.setattr(vertex_auth,'_acquire_token',lambda route, timeout: routes.append(route) or 'synthetic-token')
    monkeypatch.setattr(service.provider_secret_service,'get_secret',lambda ref: 'not-for-adc')
    service.fetch_provider_models(account('adc',api_key_secret_ref='stale'))
    assert routes[0].api_key is None


@pytest.mark.parametrize('model', ['gemini-omni-1.1', 'gemini-omni-1.2', 'gemini-3.5-transcribe-preview',
    'gemini-3.8-native-audio', 'gemini-3.8-live-translate', 'gemini-nano-banana-2.1', 'gemini-unknown'])
def test_catalog_prefix_is_not_text_capability(model):
    row = service._provider_model(model, None, 'vertex_ai')
    assert not any((row.supports_chat,row.supports_compression,row.supports_tts,row.supports_decision))


def test_live_observed_shape_is_not_entitlement_or_context(monkeypatch):
    # Shape verified by a separate metadata-only probe; synthetic values only.
    shape = {'name':'publishers/google/models/gemini-2.5-flash', 'versionId':'default',
        'launchStage':'GA', 'publisherModelTemplate':'projects/{project}/locations/{location}/publishers/google/models/gemini-2.5-flash',
        'supportedActions':{'openGenerationAiStudio':{'references':{'global':{'uri':'https://example.invalid/not-followed'}}}}}
    wire(monkeypatch,lambda req: httpx.Response(200,json={'publisherModels':[shape]}))
    row, = service.fetch_provider_models(account())
    assert row.model == 'gemini-2.5-flash' and row.context_window_tokens is None and row.max_output_tokens is None
    assert row.input_modalities == ['text','image'] and row.capability_source == 'official_registry'


def test_timeout_auth_failure_account_isolation_and_client_policy(monkeypatch):
    catalog = wire(monkeypatch,lambda req: httpx.Response(200,json={}))
    routes=[]
    monkeypatch.setattr(vertex_auth,'_acquire_token',lambda route, timeout: routes.append(route) or 'synthetic-token')
    monkeypatch.setattr(service.provider_secret_service,'get_secret',lambda ref: {'one':'cred-one','two':'cred-two'}[ref])
    service.fetch_provider_models(account('service_account',api_key_secret_ref='one'))
    service.fetch_provider_models(account('service_account',api_key_secret_ref='two'))
    assert [r.api_key for r in routes] == ['cred-one','cred-two'] and routes[0] is not routes[1]
    real_client = httpx.Client
    policies=[]
    def client(**kw):
        policies.append(kw)
        return real_client(**kw)
    monkeypatch.setattr(catalog.httpx,'Client',client)
    service.fetch_provider_models(account())
    assert policies[0]['trust_env'] is False and policies[0]['follow_redirects'] is False
    ticks=iter([0.,0.,61.])
    monkeypatch.setattr(catalog.time,'monotonic',lambda:next(ticks))
    with pytest.raises(ValueError): service.fetch_provider_models(account())
    monkeypatch.undo()
    wire(monkeypatch,lambda req: pytest.fail('No catalog request after auth failure'))
    def failed_auth(route,timeout): raise RuntimeError('private-credential-sentinel')
    monkeypatch.setattr(vertex_auth,'_acquire_token',failed_auth)
    with pytest.raises(ValueError) as caught: service.fetch_provider_models(account())
    assert 'sentinel' not in str(caught.value) and caught.value.__suppress_context__


def test_tts_and_unsupported_model_enable_controls(session,monkeypatch):
    from app.schemas.model_providers import ModelOptionManualCreate, ModelOptionUpdate
    acct = account(); session.add(acct); session.commit()
    tts = service.create_manual_option(session,ModelOptionManualCreate(provider_account_id=acct.id,
        model='gemini-2.5-flash-tts',supports_chat=False,supports_compression=False,supports_tts=True))
    assert service.option_is_active_for_capability(session,tts,'tts')
    assert tts.id in [x.id for x in service.active_options_for_runtime(session,'tts')]
    with pytest.raises(ValueError): service.update_option(session,tts.id,ModelOptionUpdate(supports_chat=True))
    unsupported = ModelOption(id='image',key='image',provider_account_id=acct.id,provider_type='vertex_ai',
        model='gemini-3.1-flash-image',label='Image',enabled=False,source='fetched',supports_chat=False,supports_compression=False)
    session.add(unsupported);session.commit()
    with pytest.raises(ValueError): service.update_option(session,'image',ModelOptionUpdate(enabled=True))
    service.update_option(session,'image',ModelOptionUpdate(enabled=False))


def test_deadline_checked_between_stream_chunks(monkeypatch):
    clock = [0.0]
    class SlowStream(httpx.SyncByteStream):
        def __iter__(self):
            yield b'{'  # Tiny chunks must not be buffered until the whole page.
            clock[0] = 61.0
            yield b'}'
    catalog = wire(monkeypatch,lambda req:httpx.Response(200,stream=SlowStream()))
    monkeypatch.setattr(catalog.time,'monotonic',lambda:clock[0])
    with pytest.raises(ValueError): service.fetch_provider_models(account())


def test_total_bytes_entry_limit_and_missing_fetched_rows(session,monkeypatch):
    catalog = wire(monkeypatch,lambda req:httpx.Response(200,json={'publisherModels':[item('gemini-2.5-flash')]}))
    monkeypatch.setattr(catalog,'MAX_TOTAL_BYTES',10)
    with pytest.raises(ValueError): service.fetch_provider_models(account())
    monkeypatch.setattr(catalog,'MAX_TOTAL_BYTES',8_000_000)
    monkeypatch.setattr(catalog,'MAX_MODELS',0)
    with pytest.raises(ValueError): service.fetch_provider_models(account())
    monkeypatch.setattr(catalog,'MAX_MODELS',2000)
    acct = account(); other = account('service_account',api_key_secret_ref='other')
    session.add(acct);session.add(other)
    for ident,owner,source in [('missing',acct.id,'fetched'),('manual',acct.id,'manual'),('other',other.id,'fetched')]:
        session.add(ModelOption(id=ident,key=ident,model='gemini-old-'+ident,label=ident,
            provider_account_id=owner,provider_type='vertex_ai',source=source,enabled=True))
    session.commit()
    service.sync_models(session,acct.id)
    assert not session.get(ModelOption,'missing').enabled
    assert session.get(ModelOption,'manual').enabled and session.get(ModelOption,'other').enabled
