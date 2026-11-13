"""Full credential requests must survive the common ingress budget first."""
import json

import pytest
from pydantic import ValidationError

from app.db.models import ModelProviderAccount
from app.schemas.input_policy import check_input_budget
from app.schemas.model_providers import ProviderAccountCreate
from app.services import model_provider_service, provider_secret_service


@pytest.fixture
def full_credential():
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    value = json.dumps({
        'type': 'service_account', 'project_id': 'fixture-project',
        'private_key_id': 'synthetic-key-id',
        'private_key': key.private_bytes(serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode(),
        'client_email': 'fixture@fixture-project.iam.gserviceaccount.com',
        'client_id': '123456789012345678901',
        'token_uri': 'https://oauth2.googleapis.com/token',
        'auth_uri': 'https://accounts.google.com/o/oauth2/auth',
        'auth_provider_x509_cert_url': 'https://www.googleapis.com/oauth2/v1/certs',
        'client_x509_cert_url': 'https://www.googleapis.com/robot/v1/metadata/x509/fixture',
        'universe_domain': 'googleapis.com',
    }, indent=2)
    assert 2048 < len(value) < 32768
    return value


def vertex_payload(secret):
    return dict(provider_type='vertex_ai', alias='Vertex ingress fixture',
        vertex_project='fixture-project', vertex_location='global',
        vertex_auth_mode='service_account', api_key=secret)


def test_standard_google_json_can_be_created_and_replaced(client, session, full_credential):
    created = client.post('/model-provider-accounts', json=vertex_payload(full_credential))
    assert created.status_code == 200
    account_id = created.json()['id']
    first_ref = session.get(ModelProviderAccount, account_id).api_key_secret_ref
    assert provider_secret_service.get_secret(first_ref) == full_credential
    changed = client.patch('/model-provider-accounts/' + account_id,
        json={'api_key': full_credential + '\n'})
    assert changed.status_code == 200
    session.expire_all()
    stored = session.get(ModelProviderAccount, account_id)
    assert stored.api_key_secret_ref != first_ref
    assert provider_secret_service.get_secret(first_ref) is None
    assert provider_secret_service.get_secret(stored.api_key_secret_ref)
    for response in (created, changed, client.get('/model-provider-accounts')):
        assert 'private_key' not in response.text
        assert 'fixture@fixture-project' not in response.text
        assert stored.api_key_secret_ref not in response.text
        assert response.status_code == 200


@pytest.mark.parametrize('size,expected', [(32768, 200), (32769, 422)])
def test_vertex_create_and_update_size_boundary(client, session, full_credential, size, expected):
    payload = full_credential.ljust(size)
    created = client.post('/model-provider-accounts', json=vertex_payload(payload))
    assert created.status_code == expected
    seed = client.post('/model-provider-accounts', json=vertex_payload(full_credential))
    assert seed.status_code == 200
    account_id = seed.json()['id']
    before_ref = session.get(ModelProviderAccount, account_id).api_key_secret_ref
    changed = client.patch('/model-provider-accounts/' + account_id, json={'api_key': payload})
    assert changed.status_code == expected
    if expected != 200:
        session.expire_all()
        assert session.get(ModelProviderAccount, account_id).api_key_secret_ref == before_ref
        assert provider_secret_service.get_secret(before_ref)
        assert 'private_key' not in created.text + changed.text
        assert 'fixture@fixture-project' not in created.text + changed.text


@pytest.mark.parametrize('provider', ['openai', 'google', 'anthropic', 'openrouter', 'xai', 'openai_compatible'])
def test_ordinary_keys_keep_existing_limits(client, session, monkeypatch, provider, full_credential):
    monkeypatch.setattr(model_provider_service, 'sync_models', lambda *_: [])
    base = dict(provider_type=provider, alias='ordinary', base_url='http://127.0.0.1:9')
    created = client.post('/model-provider-accounts', json={**base, 'api_key': 'x' * 2048})
    assert created.status_code == 200
    account_id = created.json()['id']
    original_ref = session.get(ModelProviderAccount, account_id).api_key_secret_ref
    for rejected in ['x' * 2049, full_credential]:
        assert client.post('/model-provider-accounts', json={**base, 'api_key': rejected}).status_code == 422
        response = client.patch('/model-provider-accounts/' + account_id,
            json={'api_key': rejected, 'alias': 'must-not-change'})
        assert response.status_code in (400, 404, 422)
        session.expire_all()
        stored = session.get(ModelProviderAccount, account_id)
        assert stored.alias == 'ordinary'
        assert stored.api_key_secret_ref == original_ref
        assert provider_secret_service.get_secret(original_ref) == 'x' * 2048
        assert 'private_key' not in response.text
    assert client.patch('/model-provider-accounts/' + account_id,
        json={'api_key': 'y' * 2048}).status_code == 200


def test_default_nested_and_aggregate_budgets_are_not_relaxed():
    with pytest.raises(ValueError, match='2048'):
        check_input_budget({'api_key': 'x' * 2049})
    with pytest.raises(ValueError, match='2048'):
        check_input_budget({'metadata': {'api_key': 'x' * 2049}},
            root_string_limits={'api_key': 32768})
    with pytest.raises(ValueError, match='aggregate'):
        check_input_budget({'api_key': 'x' * 32768, 'prose': ['y' * 262144] * 4},
            root_string_limits={'api_key': 32768})
    with pytest.raises(ValidationError):
        ProviderAccountCreate.model_validate({**vertex_payload('x' * 32769)})
