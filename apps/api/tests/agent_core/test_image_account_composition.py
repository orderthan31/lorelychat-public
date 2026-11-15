"""Real composition root for existing Google/Vertex image accounts; no network."""
import json

import pytest
from sqlmodel import Session


@pytest.fixture
def image_accounts(service, monkeypatch):
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives.serialization import Encoding, PrivateFormat, NoEncryption
    from app.db.models import ModelProviderAccount
    from app.services import provider_secret_service
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    account_json = json.dumps({'type': 'service_account', 'project_id': 'synthetic-project',
        'private_key_id': 'synthetic-key-id',
        'private_key': key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()).decode(),
        'client_email': 'fixture@synthetic-project.iam.gserviceaccount.com',
        'client_id': '12345678', 'token_uri': 'https://oauth2.googleapis.com/token'})
    secrets = {'google-secret': 'synthetic-google-image-key', 'vertex-secret': account_json}
    monkeypatch.setattr(provider_secret_service, 'get_secret', lambda ref: secrets.get(ref))
    with Session(service.engine) as session:
        for account in [
            ModelProviderAccount(id='google-image',provider_type='google',alias='Google fixture',enabled=True,api_key_secret_ref='google-secret'),
            ModelProviderAccount(id='vertex-image',provider_type='vertex_ai',alias='Vertex fixture',enabled=True,api_key_secret_ref='vertex-secret',vertex_project='synthetic-project',vertex_location='global',vertex_auth_mode='service_account'),
            ModelProviderAccount(id='vertex-regional',provider_type='vertex_ai',alias='Regional fixture',enabled=True,api_key_secret_ref='vertex-secret',vertex_project='synthetic-project',vertex_location='us-central1',vertex_auth_mode='service_account'),
        ]: session.add(account)
        session.commit()
    return secrets


def test_settings_catalog_is_account_filtered_and_does_not_select(service, image_accounts):
    view = service.settings_view()
    accounts = {account['id']: account for account in view['image_accounts']}
    assert 'gemini-nano-banana-2.1' in accounts['google-image']['image_model_ids']
    assert 'gemini-nano-banana-2.1' in accounts['vertex-image']['image_model_ids']
    assert accounts['vertex-regional']['image_model_ids'] == ['gemini-2.5-flash-image']
    assert view['image_provider_account_id'] is None and view['image_model_id'] is None
    serialized = json.dumps(view)
    assert 'private_key' not in serialized and 'synthetic-google-image-key' not in serialized
    assert {model['provider_type'] for model in view['image_models']} == {'google', 'vertex_ai'}


@pytest.mark.parametrize('account,provider,sdk_provider', [('google-image','google','gemini'),('vertex-image','vertex_ai','vertex_ai')])
def test_explicit_settings_selection_resolves_real_registered_auth(service, owner, image_accounts, account, provider, sdk_provider):
    from app.agent.contracts import SettingsUpdate
    service.update_settings(SettingsUpdate(enabled=True,image_provider_account_id=account,image_model_id='gemini-nano-banana-2.1'))
    route = service.resolve_image_route(owner, account, 'gemini-nano-banana-2.1')
    assert route.provider == provider and route.sdk_provider == sdk_provider
    assert route.model == 'gemini-nano-banana-2.1'
    assert route.api_key == image_accounts['vertex-secret' if provider=='vertex_ai' else 'google-secret']
    if provider == 'vertex_ai':
        assert route.vertex_project == 'synthetic-project' and route.vertex_location == 'global'
        assert route.vertex_auth_mode == 'service_account'
    view = service.settings_view()
    assert view['image_provider_account_id'] == account and view['image_model_id'] == route.model


def test_unsupported_region_and_changed_saved_selection_fail_closed(service, owner, image_accounts):
    from app.agent.contracts import AgentError, SettingsUpdate
    with pytest.raises(AgentError):
        service.update_settings(SettingsUpdate(enabled=True,image_provider_account_id='vertex-regional',image_model_id='gemini-nano-banana-2.1'))
    assert service.settings_view()['image_model_id'] is None
    service.update_settings(SettingsUpdate(enabled=True,image_provider_account_id='google-image',image_model_id='gemini-nano-banana-2.1'))
    with pytest.raises(AgentError, match='selection changed'):
        service.resolve_image_route(owner,'vertex-image','gemini-nano-banana-2.1')
