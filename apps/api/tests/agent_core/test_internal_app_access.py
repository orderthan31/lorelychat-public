"""Internal trusted-app access needs no extra owner password."""
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.core.config import get_settings


def app_for(service):
    from app.api.lore_agent import router
    app = FastAPI()
    app.state.agent_service = service
    app.include_router(router)
    return app


def test_settings_and_sessions_use_existing_app_access_without_owner_key(service, monkeypatch):
    monkeypatch.delenv('LORE_AGENT_OWNER_KEY_SHA256', raising=False)
    with TestClient(app_for(service)) as client:
        settings = client.get('/lore-agent/settings')
        assert settings.status_code == 200
        assert 'owner_configured' not in settings.json()
        payload = {k: settings.json()[k] for k in ('enabled', 'llm_model_option_key', 'image_provider_account_id', 'image_model_id', 'mcp_enabled')}
        assert client.put('/lore-agent/settings', json=payload).status_code == 200
        session = client.post('/lore-agent/sessions', json={'request_id': 'plain-app-session'})
        assert session.status_code == 200
        assert client.get('/lore-agent/sessions/' + session.json()['id']).status_code == 200
        # Settings/credential administration also works before Agent is enabled.
        payload['enabled'] = False
        assert client.put('/lore-agent/settings', json=payload).status_code == 200
        assert client.get('/lore-agent/mcp/clients').status_code == 200


def test_mcp_bearer_does_not_become_internal_approval_authority(service):
    token = service.create_client('external-reader', ['lore:read'])['token']
    with TestClient(app_for(service)) as client:
        for credential in (token, 'invalid-token'):
            headers = {'Authorization': 'Bearer ' + credential}
            assert client.put('/lore-agent/settings', json={'enabled': True}, headers=headers).status_code == 403
            assert client.post('/lore-agent/drafts/not-present/approve', json={'expected_version': 1, 'preview_hash': 'a'*64}, headers=headers).status_code == 403
        assert client.get('/lore-agent/mcp/clients').status_code == 200


def test_foreign_browser_origin_cannot_control_agent(service, monkeypatch):
    monkeypatch.setenv('CORS_ORIGINS', 'http://localhost:5174')
    get_settings.cache_clear()
    try:
        with TestClient(app_for(service)) as client:
            for origin in ('https://untrusted.invalid', 'null'):
                assert client.put('/lore-agent/settings', json={'enabled': True}, headers={'Origin': origin}).status_code == 403
            assert client.put('/lore-agent/settings', json={'enabled': True}, headers={'Origin': 'http://localhost:5174'}).status_code == 200
            assert client.put('/lore-agent/settings', json={'enabled': True}, headers={'Origin': 'http://testserver'}).status_code == 200
    finally:
        get_settings.cache_clear()
