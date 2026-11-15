import hashlib
from uuid import uuid4


def test_agent_routes_are_registered_and_default_off(client):
    response = client.get('/lore-agent/settings')
    assert response.status_code == 200
    settings = response.json()
    assert settings['enabled'] is False
    assert settings['mcp_enabled'] is False
    assert 'owner_configured' in settings
    assert 'llm_options' in settings and 'image_models' in settings
    assert 'api_key' not in settings and 'token' not in settings
    assert client.get('/mcp').status_code == 503
    assert client.get('/mcp/mcp').status_code == 404


def test_agent_owner_controls_are_not_anonymous(client, monkeypatch):
    monkeypatch.delenv('LORE_AGENT_OWNER_KEY_SHA256', raising=False)
    body = {'enabled': True, 'llm_model_option_key': None, 'image_provider_account_id': None, 'image_model_id': None, 'mcp_enabled': False}
    assert client.put('/lore-agent/settings', json=body).status_code in (401, 403, 503)
    assert client.post('/lore-agent/sessions', json={'request_id': str(uuid4())}).status_code in (401, 403, 503)
    assert client.get('/lore-agent/settings').json()['enabled'] is False


def test_agent_owner_session_is_restored_without_model_call(client, monkeypatch):
    owner_key = 'synthetic-owner-integration-key'
    monkeypatch.setenv('LORE_AGENT_OWNER_KEY_SHA256', hashlib.sha256(owner_key.encode()).hexdigest())
    headers = {'X-Lore-Owner-Key': owner_key}
    body = {'enabled': True, 'llm_model_option_key': None, 'image_provider_account_id': None, 'image_model_id': None, 'mcp_enabled': False}
    settings = client.put('/lore-agent/settings', json=body, headers=headers)
    assert settings.status_code == 200
    request_id = str(uuid4())
    created = client.post('/lore-agent/sessions', json={'request_id': request_id}, headers=headers)
    assert created.status_code in (200, 201)
    session = created.json()
    assert session['id']
    replay = client.post('/lore-agent/sessions', json={'request_id': request_id}, headers=headers)
    assert replay.status_code in (200, 201)
    assert replay.json()['id'] == session['id']
    restored = client.get('/lore-agent/sessions/' + session['id'], headers=headers)
    assert restored.status_code == 200
    assert restored.json()['messages'] == []
    assert restored.json()['busy'] is False
    assert client.get('/lore-agent/sessions/' + session['id']).status_code in (401, 403)


def test_agent_mount_preserves_existing_api_routes(client):
    assert client.get('/health').json() == {'ok': True}
    assert client.get('/ready').json()['ready'] is True
    assert client.get('/characters').status_code == 200
    assert client.get('/world-settings').status_code == 200
