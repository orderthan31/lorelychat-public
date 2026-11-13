"""Offline Cloud TTS execution, dynamic selection and atomic audio publication."""
import base64
import json
import wave
from types import SimpleNamespace

import httpx
import pytest
from app.engine import vertex_auth
from app.services import tts_service as tts, runtime_settings_service as runtime
from app.db.models import Character, Message, ModelOption, ModelProviderAccount, RuntimeSetting
from test_vertex_transport import credential, mock_auth


MODELS = ['gemini-2.5-flash-tts', 'gemini-2.5-pro-tts',
          'gemini-2.5-flash-lite-preview-tts', 'gemini-3.1-flash-tts-preview',
          'gemini-3.8-flash-tts', 'gemini-3.8-flash-lite-tts']


def test_google_dynamic_tts_keeps_legacy_gemini_provider_identity(monkeypatch):
    from app.services import model_provider_service
    values = {'provider': 'google', 'model': 'gemini-2.5-flash-tts', 'api_key': 'synthetic-key'}
    monkeypatch.setattr(model_provider_service, 'option_by_key', lambda *_: object())
    monkeypatch.setattr(runtime, '_dynamic_llm_overrides', lambda *_: values)
    assert tts.normalize_tts_provider('google') == 'gemini'
    assert tts._tts_overrides(object(), 'gemini', 'google-option') is values


def test_auth_response_chunks_respect_deadline(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(vertex_auth.time, 'monotonic', lambda: clock[0])
    def send(_request):
        clock[0] = 2.0
        return httpx.Response(200, content=b'{}')
    monkeypatch.setattr(vertex_auth, 'new_auth_transport', lambda: httpx.MockTransport(send))
    request = vertex_auth.GoogleAuthRequest(1)
    try:
        with pytest.raises(TimeoutError):
            request(vertex_auth.TOKEN_URI, method='POST', body=b'synthetic')
    finally:
        request.close()


def option(session, credential, *, model=MODELS[0], location='global', suffix='a'):
    from app.services import provider_secret_service
    ref, _ = provider_secret_service.save_secret(json.dumps(credential))
    account = ModelProviderAccount(id='tts-account-'+suffix, provider_type='vertex_ai', alias='Cloud '+suffix,
        configured=True, enabled=True, api_key_secret_ref=ref, vertex_project='test-project',
        vertex_location=location, vertex_auth_mode='service_account')
    item = ModelOption(id='tts-id-'+suffix, key='tts-option-'+suffix, provider_account_id=account.id,
        provider_type='vertex_ai', model=model, label='Cloud voice', model_family='gemini',
        enabled=True, supports_chat=False, supports_compression=False, supports_tts=True)
    session.add(account); session.add(item); session.commit()
    return account, item


@pytest.mark.parametrize('model', MODELS)
def test_documented_tts_models_accepted(model):
    vertex_auth.validate_model(model)


@pytest.mark.parametrize('model', ['gemini-decision', 'gemini-3-image', 'gemini-live-audio',
                                 'gemini-embedding-001', 'gemini-unknown-tts'])
def test_unimplemented_modalities_rejected(model):
    with pytest.raises(ValueError):
        vertex_auth.validate_model(model)


@pytest.mark.parametrize('model,location', [(m,l) for m in MODELS for l in
    (['global','us','eu'] if m.startswith('gemini-3.8-') else ['global','us-central1'])])
def test_selected_account_reaches_cloud_and_publishes_wave(session, credential, monkeypatch, tmp_path, model, location):
    _, item = option(session, credential, model=model, location=location)
    auth_calls = mock_auth(monkeypatch)
    requests = []
    def send(request):
        requests.append(request)
        return httpx.Response(200, json={'candidates':[{'finishReason':'STOP','content':{'parts':[
            {'inlineData':{'mimeType':'audio/L16;codec=pcm;rate=24000',
                           'data':base64.b64encode(b'\x01\x00'*40).decode()}}]}}]})
    monkeypatch.setattr(tts, 'new_vertex_tts_transport', lambda: httpx.MockTransport(send))
    monkeypatch.setattr(tts, 'TTS_UPLOAD_DIR', tmp_path)
    monkeypatch.setenv('GEMINI_API_KEY', 'ambient-key-must-not-be-used')
    url = tts.synthesize_to_upload('Hello Cloud', provider='vertex_ai', model=item.key,
        voice_style='Kore', file_stem='sample', session=session)
    assert len(requests) == len(auth_calls) == 1
    request = requests[0]
    host = ('aiplatform.googleapis.com' if location == 'global' else
            f'aiplatform.{location}.rep.googleapis.com' if location in {'us','eu'} else location+'-aiplatform.googleapis.com')
    version = 'v1' if model.startswith('gemini-3.8-') else 'v1beta1'
    assert str(request.url) == (f'https://{host}/{version}/projects/test-project/locations/{location}'
                                f'/publishers/google/models/{model}:generateContent')
    assert request.headers['authorization'] == 'Bearer synthetic-token-'+credential['client_email']
    assert 'key=' not in str(request.url) and 'x-goog-api-key' not in request.headers
    body = json.loads(request.content)
    assert body['contents'] == [{'role':'user','parts':[{'text':'Hello Cloud'}]}]
    config = body['generationConfig']
    assert config['responseModalities'] == ['AUDIO']
    if model.startswith('gemini-3.8-'):
        assert config['speechConfig']['voiceConfig'] == {'voice':'Kore'}
        assert config['responseFormat'] == [{'audio':{'mimeType':'AUDIO_L16'}}]
    else:
        assert config['speechConfig']['voiceConfig'] == {'prebuiltVoiceConfig':{'voiceName':'Kore'}}
        assert 'responseFormat' not in config
    assert not ({'temperature','systemInstruction','topP','topK'} & body.keys())
    with wave.open(str(tmp_path / url.rsplit('/', 1)[-1])) as audio:
        assert (audio.getnchannels(),audio.getsampwidth(),audio.getframerate(),audio.getnframes()) == (1,2,24000,40)
    assert not list(tmp_path.glob('.partial-*'))
    assert tts.synthesize_to_upload('Hello Cloud',provider='vertex_ai',model=item.key,voice_style='Kore',
        file_stem='sample',session=session) == url
    assert len(requests) == 1


@pytest.mark.parametrize('response', [
    {'candidates':[]},
    {'candidates':[{'content':{'parts':[{'inlineData':{'mimeType':'image/png','data':'AAAA'}}]}}]},
    {'candidates':[{'content':{'parts':[{'inlineData':{'mimeType':'audio/L16;rate=24000','data':'!'}}]}}]},
    {'candidates':[{'content':{'parts':[{'inlineData':{'mimeType':'audio/L16;rate=24000','data':'AQ=='}}]}}]},
    {'candidates':[{'finishReason':'MAX_TOKENS','content':{'parts':[{'inlineData':{'mimeType':'audio/L16;rate=24000','data':'AAA='}}]}}]},
])
def test_invalid_audio_never_published(session, credential, monkeypatch, tmp_path, response):
    _, item = option(session, credential)
    mock_auth(monkeypatch)
    monkeypatch.setattr(tts,'new_vertex_tts_transport',lambda:httpx.MockTransport(lambda r:httpx.Response(200,json=response)))
    monkeypatch.setattr(tts,'TTS_UPLOAD_DIR',tmp_path)
    with pytest.raises(tts.TTSUnavailableError):
        tts.synthesize_to_upload('test',provider='vertex_ai',model=item.key,voice_style='Kore',file_stem='test',session=session)
    assert not list(tmp_path.iterdir())


def test_selection_default_and_disabled_account(session, credential, monkeypatch, tmp_path):
    account, item = option(session, credential)
    session.add(RuntimeSetting(id='global',default_tts_model_option_key=item.key));session.commit()
    assert runtime.tts_overrides_for_setting(session.get(RuntimeSetting,'global'),session)['provider_account_id'] == account.id
    assert item.key in {row['key'] for row in tts.tts_model_options(session)}
    called=[]
    def synth(text, **kwargs):
        called.append(kwargs); return '/uploads/tts/synthetic.wav'
    monkeypatch.setattr(tts,'synthesize_to_upload',synth)
    result=tts.synthesize_sample(Character(name='test',persona='test'),session=session)
    assert called[0]['model']==item.key and called[0]['provider']=='vertex_ai'
    assert result['tts_model']==item.key
    account.enabled=False;session.add(account);session.commit()
    with pytest.raises((ValueError,tts.TTSUnavailableError)):
        runtime.tts_overrides_for_setting(session.get(RuntimeSetting,'global'),session)
    assert item.key not in {row['key'] for row in tts.tts_model_options(session)}


def test_missing_or_raw_model_no_credential_guess(monkeypatch, session, tmp_path):
    monkeypatch.setattr(tts,'TTS_UPLOAD_DIR',tmp_path)
    def forbidden(*args, **kwargs):
        pytest.fail('must not acquire credentials')
    monkeypatch.setattr(vertex_auth,'_acquire_token',forbidden)
    for model in ('gemini-2.5-flash-tts', 'deleted-option'):
        with pytest.raises(tts.TTSUnavailableError):
            tts.synthesize_to_upload('test',provider='vertex_ai',model=model,voice_style='Kore',file_stem='test',session=session)


def test_provider_failure_does_not_echo_private_body(session, credential, monkeypatch, tmp_path):
    _, item=option(session,credential)
    mock_auth(monkeypatch)
    monkeypatch.setattr(tts,'TTS_UPLOAD_DIR',tmp_path)
    monkeypatch.setattr(tts,'new_vertex_tts_transport',lambda:httpx.MockTransport(lambda r:httpx.Response(403,json={'error':'PRIVATE_SENTINEL'})))
    with pytest.raises(tts.TTSUnavailableError) as caught:
        tts.synthesize_to_upload('PRIVATE_SENTINEL',provider='vertex_ai',model=item.key,voice_style='Kore',file_stem='test',session=session)
    assert 'PRIVATE_SENTINEL' not in str(caught.value)
    assert caught.value.__suppress_context__
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize('entrypoint', ['manual', 'durable', 'sample'])
def test_actual_entrypoints_use_selected_cloud_account(session, engine, credential, monkeypatch, tmp_path, entrypoint):
    import asyncio
    from datetime import datetime, timedelta, timezone
    from app.api.tts import synthesize_message_tts, synthesize_character_sample, list_tts_models
    from app.api.conversations import _execute_claimed_post_commit_task
    from app.schemas.tts import TTSSampleRequest
    from app.db.models import PostCommitTask
    _, item = option(session, credential, model='gemini-3.8-flash-tts')
    session.add(RuntimeSetting(id='global', default_tts_model_option_key=item.key))
    session.add(Character(id='c', name='C', persona='p'))
    session.add(Message(id='m', conversation_id='room', speaker_type='character',speaker_id='c',content='Hello'))
    task = PostCommitTask(id='task',unique_key='tts:m',task_type='tts',status='processing',
        lease_owner='owner',lease_expires_at=datetime.now(timezone.utc)+timedelta(minutes=1),payload_={'message_id':'m'})
    session.add(task); session.commit()
    mock_auth(monkeypatch); calls=[]
    def send(request):
        calls.append(request)
        return httpx.Response(200,json={'candidates':[{'content':{'parts':[{'inlineData':{
            'mimeType':'audio/l16','data':base64.b64encode(b'\x01\x00'*40).decode()}}]},'finishReason':'STOP'}]})
    monkeypatch.setattr(tts,'new_vertex_tts_transport',lambda:httpx.MockTransport(send))
    monkeypatch.setattr(tts,'TTS_UPLOAD_DIR',tmp_path)
    assert item.key in {row['key'] for row in list_tts_models(session=session)}
    if entrypoint == 'manual':
        response=synthesize_message_tts('m',session=session)
    elif entrypoint == 'sample':
        response=synthesize_character_sample('c',TTSSampleRequest(),session=session)
    else:
        asyncio.run(_execute_claimed_post_commit_task(session,task,lease_owner='owner'))
        session.commit(); session.refresh(task)
        assert task.status == 'completed'
        metadata=session.get(Message,'m').metadata_
        response={'tts_provider':metadata['tts_provider'],'tts_model':metadata['tts_model'],'audio_url':metadata['tts_audio_url']}
    assert len(calls) == 1
    assert response['tts_provider']=='vertex_ai' and response['tts_model']==item.key
    assert tts._cached_audio_valid(response['audio_url'])


@pytest.mark.parametrize('mutation', ['default', 'account', 'model', 'source'])
def test_inflight_change_cannot_attach_old_audio(session, engine, credential, monkeypatch, tmp_path, mutation):
    from sqlmodel import Session
    account, item=option(session,credential)
    session.add(RuntimeSetting(id='global',default_tts_model_option_key=item.key))
    session.add(Character(id='c',name='C',persona='p'))
    session.add(Message(id='m',conversation_id='room',speaker_type='character',speaker_id='c',content='Hello'))
    session.commit()
    def synth(*args, **kwargs):
        with Session(engine) as other:
            if mutation == 'default':
                row=other.get(RuntimeSetting,'global');row.default_tts_model_option_key=None
            elif mutation == 'account':
                row=other.get(ModelProviderAccount,account.id);row.enabled=False
            elif mutation == 'model':
                row=other.get(ModelOption,item.id);row.model='gemini-2.5-pro-tts'
            else:
                row=other.get(Message,'m');row.content='Edited'
            other.add(row);other.commit()
        return '/uploads/tts/obsolete.wav'
    monkeypatch.setattr(tts,'synthesize_to_upload',synth)
    tts.ensure_message_tts(session,session.get(Message,'m'))
    session.commit();session.expire_all()
    assert 'tts_audio_url' not in session.get(Message,'m').metadata_


@pytest.mark.parametrize('location,model', [('us-central1','gemini-3.8-flash-tts'),('eu','gemini-2.5-flash-tts')])
def test_incompatible_region_fails_without_auth(session,credential,monkeypatch,tmp_path,location,model):
    _, item=option(session,credential,location=location,model=model)
    monkeypatch.setattr(tts,'TTS_UPLOAD_DIR',tmp_path)
    def forbidden(*a,**kw):
        pytest.fail('region rejection must precede auth')
    monkeypatch.setattr(vertex_auth,'_acquire_token',forbidden)
    with pytest.raises(tts.TTSUnavailableError):
        tts.synthesize_to_upload('Hello',provider='vertex_ai',model=item.key,voice_style='Kore',file_stem='sample',session=session)
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize('status', [429, 500, 503])
def test_no_hidden_paid_retry(session,credential,monkeypatch,tmp_path,status):
    _,item=option(session,credential);auth=mock_auth(monkeypatch);calls=[]
    def send(request):
        calls.append(request);return httpx.Response(status,json={'error':'synthetic private body'})
    monkeypatch.setattr(tts,'new_vertex_tts_transport',lambda:httpx.MockTransport(send))
    monkeypatch.setattr(tts,'TTS_UPLOAD_DIR',tmp_path)
    with pytest.raises(tts.TTSUnavailableError):
        tts.synthesize_to_upload('Hello',provider='vertex_ai',model=item.key,voice_style='Kore',file_stem='sample',session=session)
    assert len(calls)==len(auth)==1
    assert not list(tmp_path.iterdir())
