"""Real installed SDK + google-auth exercised with synthetic HTTP transports."""
import asyncio
import base64
import json
import warnings
from dataclasses import replace
from types import SimpleNamespace

import httpx
import pytest
from app.engine import litellm_transport as transport, vertex_auth
from app.engine.llm_error_types import LLMAuthenticationError, LLMPolicyError


@pytest.fixture
def credential():
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives import serialization
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return dict(type='service_account', project_id='test-project', private_key_id='synthetic-key-id',
        private_key=key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption()).decode(), client_email='test@test-project.iam.gserviceaccount.com',
        client_id='123456789', token_uri=vertex_auth.TOKEN_URI)


def route(credential, **overrides):
    values = dict(provider_account_id='account-a', api_key=json.dumps(credential), vertex_project='test-project',
        vertex_location='us-central1', vertex_auth_mode='service_account')
    values.update(overrides)
    return transport.resolve_route(SimpleNamespace(provider=lambda: 'vertex_ai', model=lambda:'gemini-2.5-flash', overrides=values))


def native(usage=None):
    return {'candidates':[{'content':{'role':'model','parts':[{'text':'Original prose'}]},'finishReason':'STOP'}],
        'usageMetadata': usage, 'modelVersion':'gemini-2.5-flash-001'}


def mock_auth(monkeypatch):
    requests = []
    def exchange(request):
        from urllib.parse import parse_qs
        assert str(request.url) == vertex_auth.TOKEN_URI
        assert request.method == 'POST'
        body = parse_qs(request.content.decode())
        jwt = body['assertion'][0]
        claims = json.loads(base64.urlsafe_b64decode(jwt.split('.')[1] + '=='))
        assert claims['scope'] == vertex_auth.SCOPES[0]
        requests.append(claims)
        return httpx.Response(200, json={'access_token': 'synthetic-token-'+claims['iss'], 'expires_in':3600, 'token_type':'Bearer'})
    monkeypatch.setattr(vertex_auth, 'new_auth_transport', lambda: httpx.MockTransport(exchange))
    return requests


@pytest.mark.asyncio
@pytest.mark.parametrize('location',['us-central1','global'])
@pytest.mark.parametrize('schema',[False,True])
async def test_real_sdk_vertex_wire(monkeypatch, credential, location, schema):
    exchanges = mock_auth(monkeypatch)
    requests=[];reserved=[]
    async def send(request):
        requests.append(request)
        return httpx.Response(200,json=native({'promptTokenCount':7,'candidatesTokenCount':2,'totalTokenCount':9}))
    monkeypatch.setattr(transport,'new_http_transport',lambda:httpx.MockTransport(send))
    r=route(credential, vertex_location=location)
    params={'max_tokens':123,'temperature':0.0,'top_p':0.0,'top_k':1,
        'thinkingConfig':{'thinkingBudget':0},
        'safety_settings':[{'category':'HARM_CATEGORY_HARASSMENT','threshold':'BLOCK_NONE'}]}
    if schema:
        params['response_format']=transport.response_format_copy({'type':'json_object','schema':{
            'type':'object','properties':{'text':{'type':'string'}},'required':['text'],'additionalProperties':False}},strict=False)
    response=await transport.completion(r,messages=[{'role':'system','content':'System only'},
        {'role':'user','content':'User only'}], parameters=params,timeout=10,reserve=lambda:reserved.append(1))
    assert len(requests)==len(reserved)==len(exchanges)==1
    assert response.extensions['lorechat_sdk_response_status'] == 'native_validated'
    request=requests[0]
    host='aiplatform.googleapis.com' if location=='global' else location+'-aiplatform.googleapis.com'
    assert str(request.url)==f'https://{host}/v1/projects/test-project/locations/{location}/publishers/google/models/gemini-2.5-flash:generateContent'
    assert request.headers['authorization']=='Bearer synthetic-token-'+credential['client_email']
    assert 'x-goog-api-key' not in request.headers
    body=json.loads(request.content)
    assert body['contents']==[{'role':'user','parts':[{'text':'User only'}]}]
    assert body['system_instruction']['parts']==[{'text':'System only'}]
    assert body['safetySettings']==params['safety_settings']
    config=body['generationConfig']
    assert config['max_output_tokens']==123 and config['temperature']==0.0
    assert config['top_p']==0.0 and config['top_k']==1
    assert config['thinkingConfig']['thinkingBudget']==0
    assert ('response_json_schema' in config or 'response_schema' in config) is schema
    observed,_=transport.native_observation(response,r)
    assert observed['content']=='Original prose' and observed['finish_reason']=='STOP'
    assert observed['prompt_tokens']==7 and observed['total_tokens']==9


@pytest.mark.asyncio
async def test_refresh_and_two_account_isolation(monkeypatch, credential):
    monkeypatch.setenv('VERTEXAI_CREDENTIALS', '/not-an-account-credential.json')
    monkeypatch.setenv('GEMINI_API_KEY', 'wrong-ambient-key')
    monkeypatch.setenv('VERTEXAI_PROJECT', 'wrong-project')
    monkeypatch.setenv('VERTEXAI_LOCATION', 'wrong-location')
    exchanges=mock_auth(monkeypatch); sent=[]
    async def send(request):
        sent.append((str(request.url),request.headers['authorization']))
        return httpx.Response(200,json=native())
    monkeypatch.setattr(transport,'new_http_transport',lambda:httpx.MockTransport(send))
    a=route(credential)
    b=route({**credential,'client_email':'other@other-project.iam.gserviceaccount.com'},
        vertex_project='other-project',vertex_location='global',provider_account_id='account-b')
    async def call(r):
        return await transport.completion(r,messages=[{'role':'user','content':'test'}],parameters={},timeout=10,reserve=lambda:None)
    await asyncio.gather(call(a),call(b))
    await call(a) # Fresh Google refresh each attempt; cannot reuse an expired token.
    assert len(exchanges)==len(sent)==3
    assert sum('other@' in header for _,header in sent)==1
    assert all(('other-project/locations' in url)==('other@' in header) for url,header in sent)


@pytest.mark.asyncio
@pytest.mark.parametrize('usage',[None, [], 'sensitive-usage-sentinel', {'promptTokenCount':'sensitive-usage-sentinel','candidatesTokenCount':0}])
async def test_unknown_usage_and_diagnostic_privacy(monkeypatch, credential, usage, capsys, caplog):
    mock_auth(monkeypatch)
    monkeypatch.setattr(transport,'new_http_transport',lambda:httpx.MockTransport(lambda req:httpx.Response(200,json=native(usage))))
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter('always')
        r=route(credential)
        response=await transport.completion(r,messages=[{'role':'user','content':'private-input-sentinel'}],parameters={},timeout=10,reserve=lambda:None)
    observation,_=transport.native_observation(response,r)
    assert observation['content']=='Original prose' and observation['prompt_tokens'] is None
    assert observation['total_tokens'] is None
    if isinstance(usage,dict): assert observation['completion_tokens']==0
    output=capsys.readouterr(); diagnostics=output.out+output.err+caplog.text+' '.join(str(w.message) for w in caught)
    assert 'sensitive-usage-sentinel' not in diagnostics and 'private-input-sentinel' not in diagnostics
    assert credential['private_key'] not in diagnostics and 'synthetic-token-' not in diagnostics


@pytest.mark.asyncio
async def test_auth_failure_safe_zero_inference(monkeypatch, credential, caplog):
    calls=[]
    monkeypatch.setattr(vertex_auth,'new_auth_transport',lambda:httpx.MockTransport(lambda r:httpx.Response(400,json={'error':'invalid_grant','error_description':'credential-echo-sentinel'})))
    monkeypatch.setattr(transport,'new_http_transport',lambda:httpx.MockTransport(lambda r:calls.append(r)))
    with pytest.raises(LLMAuthenticationError) as error:
        await transport.completion(route(credential),messages=[{'role':'user','content':'x'}],parameters={},timeout=10,reserve=lambda:None)
    assert not calls and 'credential-echo-sentinel' not in str(error.value)+caplog.text


@pytest.mark.asyncio
async def test_adc_does_not_use_another_account(monkeypatch, credential):
    import google.auth
    from google.oauth2.credentials import Credentials
    discovery=[]; refresh=[]
    def default(**kwargs):
        discovery.append(kwargs)
        return Credentials(token=None,refresh_token='server-refresh',token_uri=vertex_auth.TOKEN_URI,
            client_id='server-id',client_secret='server-secret'), 'server-project'
    def exchange(req):
        refresh.append(req.content)
        return httpx.Response(200,json={'access_token':'server-adc-token','expires_in':3600,'token_type':'Bearer'})
    monkeypatch.setattr(google.auth,'default',default)
    monkeypatch.setattr(vertex_auth,'new_auth_transport',lambda:httpx.MockTransport(exchange))
    r=route(credential,api_key=None,vertex_auth_mode='adc')
    token=await vertex_auth.acquire_token(r,10)
    assert token=='server-adc-token' and len(discovery)==len(refresh)==1
    assert b'server-refresh' in refresh[0] and b'assertion' not in refresh[0]
    with pytest.raises(transport.TransportConfigurationError):
        route(credential,vertex_auth_mode='adc')


def test_service_account_crud_masks_all_credential_data(client, session, credential):
    from app.db.models import ModelProviderAccount
    from app.services import provider_secret_service
    response = client.post('/model-provider-accounts',json=dict(provider_type='vertex_ai',alias='Vertex',
        vertex_project='test-project',vertex_location='global',api_key=json.dumps(credential)))
    assert response.status_code==200
    account=response.json(); account_id=account['id']
    assert account['vertex_auth_mode']=='service_account' and account['api_key_hint'] is None
    assert account['configured'] and account['api_key_status']=='set'
    stored=session.get(ModelProviderAccount, account_id)
    ref=stored.api_key_secret_ref
    assert provider_secret_service.get_secret(ref)==json.dumps(credential)
    assert stored.api_key_hint is None
    for read in (response,client.get('/model-provider-accounts')):
        assert credential['private_key'] not in read.text and credential['client_email'] not in read.text
        assert 'private_key' not in read.text and ref not in read.text
    changed=client.patch('/model-provider-accounts/'+account_id,json={'vertex_auth_mode':'adc'}).json()
    assert changed['configured'] and changed['api_key_hint'] is None and changed['api_key_status']=='missing'
    assert provider_secret_service.get_secret(ref) is None
    changed=client.patch('/model-provider-accounts/'+account_id,json={'vertex_auth_mode':'service_account'}).json()
    assert not changed['configured']
    bad=client.patch('/model-provider-accounts/'+account_id,json={'api_key':'private-credential-sentinel'})
    assert bad.status_code in (400,404) and 'private-credential-sentinel' not in bad.text


@pytest.mark.parametrize('field,value', [('type','external_account'),('token_uri','https://evil.invalid/token'),
    ('universe_domain','evil.invalid'),('private_key','invalid-secret-sentinel'),('client_email','bad@example.com'),
    ('credential_source',{'url':'http://127.0.0.1'}),('project_id','../path')])
def test_strict_service_account_validation(credential, field, value):
    with pytest.raises(ValueError,match='Invalid Vertex') as caught:
        vertex_auth.service_account_info(json.dumps({**credential,field:value}))
    assert 'sentinel' not in str(caught.value) and 'evil.invalid' not in str(caught.value)


@pytest.mark.asyncio
@pytest.mark.parametrize('status',[400,401,403,429,500,503])
async def test_inference_failure_one_sdk_send(monkeypatch, credential, status):
    mock_auth(monkeypatch); calls=[]
    def send(request):
        calls.append(request)
        return httpx.Response(status,json={'error':{'message':'private-error-sentinel'}},headers={'retry-after':'1'})
    monkeypatch.setattr(transport,'new_http_transport',lambda:httpx.MockTransport(send))
    with pytest.raises(httpx.HTTPStatusError) as caught:
        await transport.completion(route(credential),messages=[{'role':'user','content':'x'}],parameters={},timeout=10,reserve=lambda:None)
    assert len(calls)==1 and caught.value.response.headers['retry-after']=='1'


@pytest.mark.asyncio
async def test_application_retry_and_circuit_use_real_vertex_driver(monkeypatch, credential):
    from app.core.config import Settings
    from app.engine.llm_client import LLMClient, LLMUnavailableError, CircuitOpenError
    mock_auth(monkeypatch); calls=[]
    def send(request):
        calls.append(request)
        return httpx.Response(503,json={'error':{'message':'private-error-sentinel'}})
    monkeypatch.setattr(transport,'new_http_transport',lambda:httpx.MockTransport(send))
    client=LLMClient(Settings(llm_mock=False,llm_transport='litellm'),overrides={
        'provider':'vertex_ai','provider_account_id':'circuit-test','model':'gemini-2.5-flash',
        'vertex_project':'test-project','vertex_location':'global','vertex_auth_mode':'service_account','api_key':json.dumps(credential)})
    monkeypatch.setattr(client,'transport_retry_attempts',lambda:2)
    monkeypatch.setattr(client,'retry_delay_seconds',lambda *a,**kw:0)
    monkeypatch.setattr(client,'circuit_failure_threshold',lambda:1)
    monkeypatch.setattr(client,'_record_transport_failure',lambda *a,**kw:None)
    with pytest.raises(LLMUnavailableError) as caught:
        await client.chat([{'role':'user','content':'x'}])
    assert len(calls)==2 and caught.value.transport_attempts==2
    assert 'private-error-sentinel' not in str(caught.value)
    with pytest.raises(CircuitOpenError):
        await client.chat([{'role':'user','content':'x'}])
    assert len(calls)==2


@pytest.mark.asyncio
async def test_auth_availability_no_hidden_retry(monkeypatch, credential):
    calls=[]
    def send(request):
        calls.append(request)
        return httpx.Response(503,json={'error':'sensitive-token-endpoint-sentinel'})
    monkeypatch.setattr(vertex_auth,'new_auth_transport',lambda:httpx.MockTransport(send))
    with pytest.raises(httpx.HTTPStatusError) as caught:
        await vertex_auth.acquire_token(route(credential),10)
    assert len(calls)==1 and caught.value.response.content==b''
    assert 'sensitive-token-endpoint-sentinel' not in str(caught.value)


def test_invalid_api_key_object_never_echoed_by_validation(client):
    for malformed in ([{'api_key': {'private_key':'sensitive-json-sentinel'}}], 'sensitive-json-sentinel'):
        rejected=client.post('/model-provider-accounts',json=malformed)
        assert rejected.status_code==422 and 'sensitive-json-sentinel' not in rejected.text
    response=client.post('/model-provider-accounts',json=dict(provider_type='vertex_ai',alias='Vertex',
        vertex_project='test-project',vertex_location='global',api_key={'private_key':'sensitive-json-sentinel'}))
    assert response.status_code==422
    assert 'sensitive-json-sentinel' not in response.text and 'private_key' not in response.text


@pytest.mark.asyncio
async def test_real_server_adc_file_discovery(monkeypatch, tmp_path, credential):
    path=tmp_path/'server-adc.json'
    path.write_text(json.dumps(dict(type='authorized_user',client_id='server-id',client_secret='server-secret',refresh_token='server-refresh')))
    monkeypatch.setenv('GOOGLE_APPLICATION_CREDENTIALS',str(path))
    monkeypatch.setenv('GOOGLE_CLOUD_PROJECT','test-project')
    monkeypatch.setenv('VERTEXAI_CREDENTIALS',json.dumps(credential))
    seen=[]
    def exchange(request):
        seen.append(request.content)
        return httpx.Response(200,json={'access_token':'server-token','expires_in':3600,'token_type':'Bearer'})
    monkeypatch.setattr(vertex_auth,'new_auth_transport',lambda:httpx.MockTransport(exchange))
    assert await vertex_auth.acquire_token(route(credential,api_key=None,vertex_auth_mode='adc'),10)=='server-token'
    assert len(seen)==1 and b'server-refresh' in seen[0] and b'assertion' not in seen[0]


@pytest.mark.asyncio
async def test_vertex_native_transport_fails_closed(monkeypatch, credential):
    from app.core.config import Settings
    from app.engine.llm_client import LLMClient
    from app.engine.llm_error_types import LLMConfigurationError
    client=LLMClient(Settings(llm_mock=False,llm_transport='legacy_http'),overrides={
        'provider':'vertex_ai','provider_account_id':'native-test','model':'gemini-2.5-flash',
        'vertex_project':'test-project','vertex_location':'global','api_key':json.dumps(credential)})
    monkeypatch.setattr(client,'_record_transport_failure',lambda *a,**kw:None)
    with pytest.raises(LLMConfigurationError,match='LiteLLM'):
        await client.chat([{'role':'user','content':'x'}])


@pytest.mark.asyncio
async def test_attached_server_adc_uses_only_owned_metadata_transport(monkeypatch, credential):
    import google.auth
    from google.auth import compute_engine
    monkeypatch.setattr(google.auth,'default',lambda **kw:(compute_engine.Credentials(scopes=vertex_auth.SCOPES),'test-project'))
    calls=[]
    def exchange(request):
        calls.append(str(request.url))
        headers={'metadata-flavor':'Google'}
        if request.url.path.endswith('/universe/universe-domain'):
            return httpx.Response(200,text='googleapis.com',headers=headers)
        if request.url.path.endswith('/token'):
            return httpx.Response(200,json={'access_token':'metadata-token','expires_in':3600,'token_type':'Bearer'},headers=headers)
        return httpx.Response(200,json={'email':'attached@test-project.iam.gserviceaccount.com','scopes':vertex_auth.SCOPES},headers=headers)
    monkeypatch.setattr(vertex_auth,'new_auth_transport',lambda:httpx.MockTransport(exchange))
    assert await vertex_auth.acquire_token(route(credential,api_key=None,vertex_auth_mode='adc'),10)=='metadata-token'
    assert len(calls)==3 and all(url.startswith('http://metadata.google.internal/') for url in calls)
