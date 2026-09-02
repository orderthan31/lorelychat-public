from pathlib import Path

import httpx

from app.services import runtime_settings_service
from app.services import provider_secret_service, model_provider_service


def _fake_provider_models(account):
    if account.provider_type == "google":
        return [
            model_provider_service.ProviderModel("gemini-2.5-flash", "Gemini 2.5 Flash", "gemini"),
            model_provider_service.ProviderModel("gemini-2.5-pro", "Gemini 2.5 Pro", "gemini"),
            model_provider_service.ProviderModel("gemini-2.5-flash-tts", "Gemini 2.5 Flash TTS", "gemini", supports_chat=False, supports_compression=False, supports_tts=True),
        ]
    if account.provider_type == "openai":
        return [model_provider_service.ProviderModel("gpt-4.1-mini", "GPT-4.1 Mini", "gpt")]
    return [model_provider_service.ProviderModel("local-model", "Local Model", "local")]


def _mixed_google_provider_models(account):
    return [
        model_provider_service._provider_model("gemini-2.5-flash", "Gemini 2.5 Flash", "google", {"supportedGenerationMethods": ["generateContent"]}),
        model_provider_service._provider_model("gemini-3-pro-preview", "Gemini 3 Pro Preview", "google", {"supportedGenerationMethods": ["generateContent"]}),
        model_provider_service._provider_model("gemini-2.5-flash-preview-tts", "Gemini 2.5 Flash Preview TTS", "google", {"supportedGenerationMethods": ["generateContent"]}),
        model_provider_service._provider_model("gemini-2.5-flash-image", "Gemini 2.5 Flash Image", "google", {"supportedGenerationMethods": ["generateContent"]}),
        model_provider_service._provider_model("nano-banana-pro-preview", "Nano Banana Pro", "google", {"supportedGenerationMethods": ["generateContent"]}),
        model_provider_service._provider_model("veo-3.1-generate-preview", "Veo 3.1", "google", {"supportedGenerationMethods": ["generateContent"]}),
        model_provider_service._provider_model("lyria-3-pro-preview", "Lyria 3 Pro", "google", {"supportedGenerationMethods": ["generateContent"]}),
        model_provider_service._provider_model("gemini-embedding-001", "Gemini Embedding", "google", {"supportedGenerationMethods": ["embedContent"]}),
        model_provider_service._provider_model("deep-research-preview-04-2026", "Deep Research", "google", {"supportedGenerationMethods": ["generateContent"]}),
        model_provider_service._provider_model("gemini-2.5-computer-use-preview-10-2025", "Computer Use", "google", {"supportedGenerationMethods": ["generateContent"]}),
    ]


def _secret_path(name: str) -> Path:
    root = Path("tmp") / "test-provider-secrets"
    root.mkdir(parents=True, exist_ok=True)
    path = root / name
    if path.exists():
        path.unlink()
    return path


def _enable_option(client, option):
    response = client.patch(f"/model-options/{option['id']}", json={"enabled": True})
    assert response.status_code == 200
    return response.json()


def test_runtime_setting_accepts_only_a_distinct_active_fallback_model(client, monkeypatch):
    secret_file = _secret_path("provider_secrets_fallback.json")
    monkeypatch.setattr(provider_secret_service, "SECRET_ROOT", secret_file.parent)
    monkeypatch.setattr(provider_secret_service, "SECRET_FILE", secret_file)
    monkeypatch.setattr(model_provider_service, "fetch_provider_models", _fake_provider_models)

    account = client.post(
        "/model-provider-accounts",
        json={"provider_type": "google", "alias": "Fallback Gemini", "api_key": "gkey-fallback-test-0001"},
    ).json()
    options = client.post(f"/model-provider-accounts/{account['id']}/sync-models").json()
    chat_options = [_enable_option(client, option) for option in options if option["supports_chat"]]
    primary, fallback = chat_options[:2]

    response = client.patch(
        "/runtime-settings/default",
        json={"model_key": primary["key"], "fallback_model_key": fallback["key"]},
    )

    assert response.status_code == 200
    assert response.json()["model_key"] == primary["key"]
    assert response.json()["fallback_model_key"] == fallback["key"]

    same_model = client.patch(
        "/runtime-settings/default",
        json={"fallback_model_key": primary["key"]},
    )
    assert same_model.status_code == 400
    assert "must differ" in same_model.json()["detail"]

    cleared = client.patch("/runtime-settings/default", json={"fallback_model_key": None})
    assert cleared.status_code == 200
    assert cleared.json()["fallback_model_key"] is None


def test_compression_fallback_inherits_chat_fallback_and_allows_dedicated_override(client, monkeypatch):
    secret_file = _secret_path("provider_secrets_compression_fallback.json")
    monkeypatch.setattr(provider_secret_service, "SECRET_ROOT", secret_file.parent)
    monkeypatch.setattr(provider_secret_service, "SECRET_FILE", secret_file)
    monkeypatch.setattr(model_provider_service, "fetch_provider_models", _fake_provider_models)

    account = client.post(
        "/model-provider-accounts",
        json={"provider_type": "google", "alias": "Compression Fallback", "api_key": "test-key"},
    ).json()
    options = client.post(f"/model-provider-accounts/{account['id']}/sync-models").json()
    capable = [
        _enable_option(client, option)
        for option in options
        if option["supports_chat"] and option["supports_compression"]
    ]
    primary, fallback = capable[:2]

    inherited = client.patch(
        "/runtime-settings/default",
        json={
            "model_key": primary["key"],
            "fallback_model_key": fallback["key"],
            "compression_model_key": primary["key"],
        },
    )
    assert inherited.status_code == 200, inherited.text
    assert inherited.json()["compression_fallback_model_key"] is None
    assert inherited.json()["effective_compression_fallback_model_key"] == fallback["key"]
    assert inherited.json()["compression_fallback_source"] == "chat_fallback"

    dedicated = client.patch(
        "/runtime-settings/default",
        json={"compression_fallback_model_key": fallback["key"]},
    )
    assert dedicated.status_code == 200, dedicated.text
    assert dedicated.json()["compression_fallback_model_key"] == fallback["key"]
    assert dedicated.json()["effective_compression_fallback_model_key"] == fallback["key"]
    assert dedicated.json()["compression_fallback_source"] == "dedicated"

    same_as_primary = client.patch(
        "/runtime-settings/default",
        json={"compression_fallback_model_key": primary["key"]},
    )
    assert same_as_primary.status_code == 400
    assert "compression" in same_as_primary.json()["detail"].lower()
    assert "differ" in same_as_primary.json()["detail"].lower()

    cleared = client.patch(
        "/runtime-settings/default",
        json={"compression_fallback_model_key": None},
    )
    assert cleared.status_code == 200
    assert cleared.json()["compression_fallback_model_key"] is None
    assert cleared.json()["effective_compression_fallback_model_key"] == fallback["key"]
    assert cleared.json()["compression_fallback_source"] == "chat_fallback"



def test_provider_account_masks_api_key_and_auto_syncs_models_as_inactive(client, monkeypatch):
    secret_file = _secret_path("provider_secrets_1.json")
    monkeypatch.setattr(provider_secret_service, "SECRET_ROOT", secret_file.parent)
    monkeypatch.setattr(provider_secret_service, "SECRET_FILE", secret_file)
    monkeypatch.setattr(model_provider_service, "fetch_provider_models", _fake_provider_models)

    response = client.post(
        "/model-provider-accounts",
        json={"provider_type": "google", "alias": "개인 Gemini", "api_key": "gkey-secret-test-1234"},
    )
    assert response.status_code == 200
    account = response.json()
    assert account["api_key_status"] == "set"
    assert account["api_key_hint"] == "gkey…1234"
    assert "api_key" not in account
    assert "secret" not in str(account).lower()

    options_response = client.get("/model-options")
    assert options_response.status_code == 200
    options = options_response.json()
    assert any(option["supports_chat"] for option in options)
    assert any(option["supports_tts"] and option["model_family"] == "gemini" for option in options)
    assert all(option["enabled"] is False for option in options)
    assert client.get("/runtime-settings/default").json()["options"] == []


def test_xai_provider_auto_syncs_only_grok_text_models_as_runtime_capable(client, session, monkeypatch):
    secret_file = _secret_path("provider_secrets_xai.json")
    monkeypatch.setattr(provider_secret_service, "SECRET_ROOT", secret_file.parent)
    monkeypatch.setattr(provider_secret_service, "SECRET_FILE", secret_file)
    requests = []

    def fake_get(url, *, headers, timeout):
        requests.append({"url": url, "headers": headers, "timeout": timeout})
        return httpx.Response(
            200,
            request=httpx.Request("GET", url),
            json={
                "data": [
                    {"id": "grok-4.5", "object": "model", "owned_by": "xai"},
                    {"id": "grok-4.20-0309-reasoning", "object": "model", "owned_by": "xai"},
                    {"id": "grok-imagine-image-quality", "object": "model", "owned_by": "xai"},
                    {"id": "grok-imagine-video", "object": "model", "owned_by": "xai"},
                ]
            },
        )

    monkeypatch.setattr(model_provider_service.httpx, "get", fake_get)

    response = client.post(
        "/model-provider-accounts",
        json={"provider_type": "xai", "alias": "개인 Grok", "api_key": "test-xai-key"},
    )

    assert response.status_code == 200
    account = response.json()
    assert account["provider_type"] == "xai"
    assert account["base_url"] == "https://api.x.ai/v1"
    assert account["configured"] is True
    assert account["api_key_status"] == "set"
    assert account["api_key_hint"] == "test…-key"
    assert requests == [{
        "url": "https://api.x.ai/v1/models",
        "headers": {"Authorization": "Bearer test-xai-key"},
        "timeout": 30.0,
    }]

    options = {option["model"]: option for option in client.get("/model-options").json()}
    for model in ("grok-4.5", "grok-4.20-0309-reasoning"):
        assert options[model]["provider_type"] == "xai"
        assert options[model]["model_family"] == "grok"
        assert options[model]["supports_chat"] is True
        assert options[model]["supports_compression"] is True
        assert options[model]["supports_tts"] is False
        assert options[model]["supports_json"] is True
        assert options[model]["enabled"] is False
    for model in ("grok-imagine-image-quality", "grok-imagine-video"):
        assert options[model]["model_family"] == "grok"
        assert options[model]["supports_chat"] is False
        assert options[model]["supports_compression"] is False
        assert options[model]["supports_tts"] is False
        assert options[model]["enabled"] is False

    enabled = _enable_option(client, options["grok-4.5"])
    setting_response = client.patch("/runtime-settings/default", json={"model_key": enabled["key"]})
    assert setting_response.status_code == 200
    setting = runtime_settings_service.get_global_setting(session)
    overrides = runtime_settings_service.llm_overrides_for_setting(setting, session=session)
    assert overrides["provider"] == "xai"
    assert overrides["provider_type"] == "xai"
    assert overrides["provider_account_id"] == account["id"]
    assert overrides["model"] == "grok-4.5"
    assert overrides["base_url"] == "https://api.x.ai/v1"
    assert overrides["api_key"] == "test-xai-key"


def test_provider_account_api_key_update_replaces_secret_without_raw_response(client, session, monkeypatch):
    secret_file = _secret_path("provider_secrets_update.json")
    monkeypatch.setattr(provider_secret_service, "SECRET_ROOT", secret_file.parent)
    monkeypatch.setattr(provider_secret_service, "SECRET_FILE", secret_file)
    monkeypatch.setattr(model_provider_service, "fetch_provider_models", _fake_provider_models)

    account = client.post(
        "/model-provider-accounts",
        json={"provider_type": "google", "alias": "수정 Gemini", "api_key": "gkey-old-secret-0001"},
    ).json()
    original = model_provider_service.account_by_id(session, account["id"])
    original_ref = original.api_key_secret_ref

    response = client.patch(f"/model-provider-accounts/{account['id']}", json={"api_key": "gkey-new-secret-9999"})

    assert response.status_code == 200
    updated = response.json()
    assert updated["api_key_status"] == "set"
    assert updated["api_key_hint"] == "gkey…9999"
    assert updated["configured"] is True
    assert "api_key" not in updated
    assert "gkey-new-secret-9999" not in str(updated)
    session.expire_all()
    stored = model_provider_service.account_by_id(session, account["id"])
    assert stored.api_key_secret_ref == original_ref
    assert provider_secret_service.get_secret(stored.api_key_secret_ref) == "gkey-new-secret-9999"


def test_provider_account_clear_api_key_disables_only_its_models_and_runtime_options(client, session, monkeypatch):
    secret_file = _secret_path("provider_secrets_clear.json")
    monkeypatch.setattr(provider_secret_service, "SECRET_ROOT", secret_file.parent)
    monkeypatch.setattr(provider_secret_service, "SECRET_FILE", secret_file)
    monkeypatch.setattr(model_provider_service, "fetch_provider_models", _fake_provider_models)

    account = client.post(
        "/model-provider-accounts",
        json={"provider_type": "google", "alias": "해제 Gemini", "api_key": "gkey-clear-secret-0001"},
    ).json()
    options = client.post(f"/model-provider-accounts/{account['id']}/sync-models").json()
    chat_option = _enable_option(client, next(option for option in options if option["supports_chat"]))
    client.patch("/runtime-settings/default", json={"model_key": chat_option["key"], "compression_model_key": chat_option["key"]})

    response = client.patch(f"/model-provider-accounts/{account['id']}", json={"clear_api_key": True})

    assert response.status_code == 200
    cleared = response.json()
    assert cleared["id"] == account["id"]
    assert cleared["api_key_status"] == "missing"
    assert cleared["api_key_hint"] is None
    assert cleared["configured"] is False
    session.expire_all()
    stored = model_provider_service.account_by_id(session, account["id"])
    assert stored.api_key_secret_ref is None
    assert stored.api_key_hint is None
    assert stored.alias == "해제 Gemini"
    assert all(not option["enabled"] for option in client.get("/model-options").json() if option["provider_account_id"] == account["id"])
    runtime = client.get("/runtime-settings/default").json()
    assert chat_option["key"] not in {option["key"] for option in runtime["options"]}
    assert chat_option["key"] not in {option["key"] for option in runtime["compression_options"]}
    assert runtime["model_key"] is None
    assert runtime["compression_model_key"] is None


def test_clear_api_key_preserves_other_accounts_with_same_provider_type(client, monkeypatch):
    secret_file = _secret_path("provider_secrets_same_provider.json")
    monkeypatch.setattr(provider_secret_service, "SECRET_ROOT", secret_file.parent)
    monkeypatch.setattr(provider_secret_service, "SECRET_FILE", secret_file)
    monkeypatch.setattr(model_provider_service, "fetch_provider_models", _fake_provider_models)

    account_a = client.post(
        "/model-provider-accounts",
        json={"provider_type": "google", "alias": "Provider A", "api_key": "gkey-provider-a-0001"},
    ).json()
    account_b = client.post(
        "/model-provider-accounts",
        json={"provider_type": "google", "alias": "Provider B", "api_key": "gkey-provider-b-0002"},
    ).json()
    option_a = _enable_option(client, next(option for option in client.post(f"/model-provider-accounts/{account_a['id']}/sync-models").json() if option["supports_chat"]))
    option_b = _enable_option(client, next(option for option in client.post(f"/model-provider-accounts/{account_b['id']}/sync-models").json() if option["supports_chat"]))

    response = client.patch(f"/model-provider-accounts/{account_a['id']}", json={"clear_api_key": True})

    assert response.status_code == 200
    options = client.get("/model-options").json()
    by_id = {option["id"]: option for option in options}
    assert by_id[option_a["id"]]["enabled"] is False
    assert by_id[option_b["id"]]["enabled"] is True
    runtime_chat_keys = {option["key"] for option in client.get("/runtime-settings/default").json()["options"]}
    assert option_a["key"] not in runtime_chat_keys
    assert option_b["key"] in runtime_chat_keys


def test_provider_account_rejects_api_key_and_clear_api_key_together_without_changes(client, session, monkeypatch):
    secret_file = _secret_path("provider_secrets_conflict.json")
    monkeypatch.setattr(provider_secret_service, "SECRET_ROOT", secret_file.parent)
    monkeypatch.setattr(provider_secret_service, "SECRET_FILE", secret_file)
    monkeypatch.setattr(model_provider_service, "fetch_provider_models", _fake_provider_models)

    account = client.post(
        "/model-provider-accounts",
        json={"provider_type": "google", "alias": "충돌 Gemini", "api_key": "gkey-original-secret"},
    ).json()
    option = _enable_option(client, next(option for option in client.post(f"/model-provider-accounts/{account['id']}/sync-models").json() if option["supports_chat"]))
    session.expire_all()
    before = model_provider_service.account_by_id(session, account["id"])
    before_ref = before.api_key_secret_ref
    before_hint = before.api_key_hint
    before_secret = provider_secret_service.get_secret(before_ref)

    response = client.patch(f"/model-provider-accounts/{account['id']}", json={"api_key": "gkey-new-secret", "clear_api_key": True})

    assert response.status_code == 400
    assert response.json()["detail"] == "api_key and clear_api_key cannot be used together"
    session.expire_all()
    after = model_provider_service.account_by_id(session, account["id"])
    assert after.api_key_secret_ref == before_ref
    assert after.api_key_hint == before_hint
    assert after.configured is True
    assert provider_secret_service.get_secret(before_ref) == before_secret
    assert next(item for item in client.get("/model-options").json() if item["id"] == option["id"])["enabled"] is True


def test_runtime_settings_uses_active_model_options_and_gemini_tts_only(client, monkeypatch):
    secret_file = _secret_path("provider_secrets_2.json")
    monkeypatch.setattr(provider_secret_service, "SECRET_ROOT", secret_file.parent)
    monkeypatch.setattr(provider_secret_service, "SECRET_FILE", secret_file)
    monkeypatch.setattr(model_provider_service, "fetch_provider_models", _fake_provider_models)

    google = client.post(
        "/model-provider-accounts",
        json={"provider_type": "google", "alias": "업무용 Gemini", "api_key": "gkey-secret-test-5678"},
    ).json()
    openai = client.post(
        "/model-provider-accounts",
        json={"provider_type": "openai", "alias": "개인 OpenAI", "api_key": "test-openai-key"},
    ).json()
    google_options = client.post(f"/model-provider-accounts/{google['id']}/sync-models").json()
    client.post(f"/model-provider-accounts/{openai['id']}/sync-models")

    tts_option = next(option for option in google_options if option["supports_tts"])
    _enable_option(client, tts_option)
    _enable_option(client, next(option for option in google_options if option["supports_chat"]))
    patch_response = client.patch(
        "/runtime-settings/default",
        json={"default_tts_model_option_key": tts_option["key"], "safety_preset": "low"},
    )
    assert patch_response.status_code == 200
    setting = patch_response.json()
    assert setting["default_tts_model_option_key"] == tts_option["key"]
    assert setting["safety_preset"] == "low"
    assert setting["options"]
    assert setting["compression_options"]
    assert setting["tts_options"]
    assert all(option["provider"] == "google" for option in setting["tts_options"])


def test_dynamic_google_model_option_builds_chat_llm_overrides(client, session, monkeypatch):
    secret_file = _secret_path("provider_secrets_3.json")
    monkeypatch.setattr(provider_secret_service, "SECRET_ROOT", secret_file.parent)
    monkeypatch.setattr(provider_secret_service, "SECRET_FILE", secret_file)
    monkeypatch.setattr(model_provider_service, "fetch_provider_models", _fake_provider_models)

    account = client.post(
        "/model-provider-accounts",
        json={"provider_type": "google", "alias": "실호출 Gemini", "api_key": "test-key"},
    ).json()
    options = client.post(f"/model-provider-accounts/{account['id']}/sync-models").json()
    chat_option = next(option for option in options if option["supports_chat"])
    chat_option = _enable_option(client, chat_option)

    response = client.patch("/runtime-settings/default", json={"model_key": chat_option["key"]})
    assert response.status_code == 200
    setting = runtime_settings_service.get_global_setting(session)
    overrides = runtime_settings_service.llm_overrides_for_setting(setting, session=session)

    assert overrides["provider"] == "gemini"
    assert overrides["provider_type"] == "google"
    assert overrides["provider_account_id"] == account["id"]
    assert overrides["model_option_key"] == chat_option["key"]
    assert overrides["model"] == chat_option["model"]
    assert overrides["api_key"] == "test-key"
    assert overrides["gemini_api_key"] == "test-key"


def test_dynamic_openai_compatible_manual_model_builds_compression_overrides(client, session):
    account = client.post(
        "/model-provider-accounts",
        json={"provider_type": "openai_compatible", "alias": "로컬 Gemma", "base_url": "http://127.0.0.1:1234/v1", "preset": "local_gemma"},
    ).json()
    option = client.post(
        "/model-options/manual",
        json={
            "provider_account_id": account["id"],
            "model": "gemma-local",
            "label": "Local Gemma",
            "model_family": "local",
            "supports_chat": True,
            "supports_compression": True,
        },
    ).json()

    response = client.patch("/runtime-settings/default", json={"compression_model_key": option["key"]})
    assert response.status_code == 200
    setting = runtime_settings_service.get_global_setting(session)
    overrides = runtime_settings_service.compression_llm_overrides_for_setting(setting, session=session)

    assert overrides["provider"] == "openai_compatible"
    assert overrides["provider_type"] == "openai_compatible"
    assert overrides["provider_account_id"] == account["id"]
    assert overrides["model_option_key"] == option["key"]
    assert overrides["model"] == "gemma-local"
    assert overrides["base_url"] == "http://127.0.0.1:1234/v1"


def test_runtime_update_rejects_inactive_or_wrong_capability_options(client, monkeypatch):
    secret_file = _secret_path("provider_secrets_4.json")
    monkeypatch.setattr(provider_secret_service, "SECRET_ROOT", secret_file.parent)
    monkeypatch.setattr(provider_secret_service, "SECRET_FILE", secret_file)
    monkeypatch.setattr(model_provider_service, "fetch_provider_models", _fake_provider_models)

    google = client.post(
        "/model-provider-accounts",
        json={"provider_type": "google", "alias": "검증 Gemini", "api_key": "gkey-secret-test-9999"},
    ).json()
    options = client.post(f"/model-provider-accounts/{google['id']}/sync-models").json()
    tts_option = next(option for option in options if option["supports_tts"])
    chat_option = next(option for option in options if option["supports_chat"])
    tts_option = _enable_option(client, tts_option)
    chat_option = _enable_option(client, chat_option)

    chat_as_tts = client.patch("/runtime-settings/default", json={"default_tts_model_option_key": chat_option["key"]})
    assert chat_as_tts.status_code == 400

    tts_as_chat = client.patch("/runtime-settings/default", json={"model_key": tts_option["key"]})
    assert tts_as_chat.status_code == 400

    client.patch(f"/model-options/{chat_option['id']}", json={"enabled": False})
    inactive_chat = client.patch("/runtime-settings/default", json={"model_key": chat_option["key"]})
    assert inactive_chat.status_code == 400


def test_conversation_runtime_setting_inherits_new_global_fields(client, monkeypatch):
    secret_file = _secret_path("provider_secrets_5.json")
    monkeypatch.setattr(provider_secret_service, "SECRET_ROOT", secret_file.parent)
    monkeypatch.setattr(provider_secret_service, "SECRET_FILE", secret_file)
    monkeypatch.setattr(model_provider_service, "fetch_provider_models", _fake_provider_models)

    google = client.post(
        "/model-provider-accounts",
        json={"provider_type": "google", "alias": "상속 Gemini", "api_key": "gkey-secret-test-1111"},
    ).json()
    options = client.post(f"/model-provider-accounts/{google['id']}/sync-models").json()
    tts_option = next(option for option in options if option["supports_tts"])
    tts_option = _enable_option(client, tts_option)
    client.patch("/runtime-settings/default", json={"default_tts_model_option_key": tts_option["key"], "safety_preset": "low"})

    character = client.post("/characters", json={"name": "상속테스트", "persona": "상속 테스트용"}).json()
    conversation = client.post(
        "/conversations",
        json={
            "mode": "user_character",
            "participants": [
                {"type": "user", "id": "user_001"},
                {"type": "character", "id": character["id"]},
            ],
            "scene": {"opening_scene": "상속 테스트 장면"},
        },
    ).json()
    setting = client.get(f"/runtime-settings/conversations/{conversation['id']}").json()

    assert setting["default_tts_model_option_key"] == tts_option["key"]
    assert setting["safety_preset"] == "low"


def test_google_model_classification_filters_runtime_capabilities():
    text_model = model_provider_service._provider_model("gemini-2.5-flash", "Gemini 2.5 Flash", "google", {"supportedGenerationMethods": ["generateContent"]})
    tts_model = model_provider_service._provider_model("gemini-2.5-flash-preview-tts", "Gemini TTS", "google", {"supportedGenerationMethods": ["generateContent"]})
    image_model = model_provider_service._provider_model("gemini-2.5-flash-image", "Gemini Image", "google", {"supportedGenerationMethods": ["generateContent"]})
    research_model = model_provider_service._provider_model("deep-research-preview-04-2026", "Deep Research", "google", {"supportedGenerationMethods": ["generateContent"]})
    embedding_model = model_provider_service._provider_model("gemini-embedding-001", "Gemini Embedding", "google", {"supportedGenerationMethods": ["embedContent"]})

    assert text_model.supports_chat is True
    assert text_model.supports_compression is True
    assert text_model.supports_tts is False
    assert tts_model.supports_chat is False
    assert tts_model.supports_compression is False
    assert tts_model.supports_tts is True
    for excluded in (image_model, research_model, embedding_model):
        assert excluded.supports_chat is False
        assert excluded.supports_compression is False
        assert excluded.supports_tts is False


def test_manual_model_context_metadata_round_trips_and_can_be_cleared(client):
    account = client.post(
        "/model-provider-accounts",
        json={
            "provider_type": "openai_compatible",
            "alias": "Context Local",
            "base_url": "http://127.0.0.1:1234/v1",
            "preset": "local_gemma",
        },
    ).json()

    created_response = client.post(
        "/model-options/manual",
        json={
            "provider_account_id": account["id"],
            "model": "context-local-model",
            "context_window_tokens": 32_768,
            "max_output_tokens": 4_096,
        },
    )

    assert created_response.status_code == 200, created_response.text
    created = created_response.json()
    assert created["context_window_tokens"] == 32_768
    assert created["max_output_tokens"] == 4_096
    assert created["supports_chat"] is True
    assert created["source"] == "manual"

    updated = client.patch(
        f"/model-options/{created['id']}",
        json={"context_window_tokens": 65_536, "max_output_tokens": None},
    )

    assert updated.status_code == 200, updated.text
    assert updated.json()["context_window_tokens"] == 65_536
    assert updated.json()["max_output_tokens"] is None


def test_provider_model_derives_common_context_metadata_keys():
    google = model_provider_service._provider_model(
        "gemini-context",
        "Gemini Context",
        "google",
        {
            "supportedGenerationMethods": ["generateContent"],
            "inputTokenLimit": 1_048_576,
            "outputTokenLimit": 65_536,
        },
    )
    openrouter = model_provider_service._provider_model(
        "vendor/router-context",
        "Router Context",
        "openrouter",
        {
            "context_length": 131_072,
            "architecture": {"output_modalities": ["text"]},
            "top_provider": {"max_completion_tokens": 16_384},
        },
    )
    compatible = model_provider_service._provider_model(
        "qwen-context",
        "Qwen Context",
        "openai_compatible",
        {"context_window": "32768", "max_output_tokens": "4096"},
    )

    assert (google.context_window_tokens, google.max_output_tokens) == (1_048_576, 65_536)
    assert (openrouter.context_window_tokens, openrouter.max_output_tokens) == (131_072, 16_384)
    assert (compatible.context_window_tokens, compatible.max_output_tokens) == (32_768, 4_096)


def test_sync_preserves_configured_context_metadata_when_provider_omits_it(client, monkeypatch):
    account = client.post(
        "/model-provider-accounts",
        json={
            "provider_type": "openai_compatible",
            "alias": "Sparse Metadata",
            "base_url": "http://127.0.0.1:1234/v1",
            "preset": "local_gemma",
        },
    ).json()
    monkeypatch.setattr(
        model_provider_service,
        "fetch_provider_models",
        lambda _account: [
            model_provider_service.ProviderModel(
                "gemma-sparse",
                "Gemma Sparse",
                "local",
                context_window_tokens=32_768,
                max_output_tokens=4_096,
            )
        ],
    )
    synced = client.post(f"/model-provider-accounts/{account['id']}/sync-models").json()
    option = next(item for item in synced if item["model"] == "gemma-sparse")
    configured = client.patch(
        f"/model-options/{option['id']}",
        json={"context_window_tokens": 65_536, "max_output_tokens": 8_192},
    ).json()
    assert configured["context_window_tokens"] == 65_536

    monkeypatch.setattr(
        model_provider_service,
        "fetch_provider_models",
        lambda _account: [model_provider_service.ProviderModel("gemma-sparse", "Gemma Sparse", "local")],
    )
    resynced = client.post(f"/model-provider-accounts/{account['id']}/sync-models").json()
    preserved = next(item for item in resynced if item["id"] == option["id"])

    assert preserved["context_window_tokens"] == 65_536
    assert preserved["max_output_tokens"] == 8_192


def test_sync_models_reclassifies_existing_google_options_and_runtime_filters(client, monkeypatch):
    secret_file = _secret_path("provider_secrets_6.json")
    monkeypatch.setattr(provider_secret_service, "SECRET_ROOT", secret_file.parent)
    monkeypatch.setattr(provider_secret_service, "SECRET_FILE", secret_file)
    monkeypatch.setattr(model_provider_service, "fetch_provider_models", _mixed_google_provider_models)

    account = client.post(
        "/model-provider-accounts",
        json={"provider_type": "google", "alias": "분류 Gemini", "api_key": "gkey-secret-test-2222"},
    ).json()
    initial_options = {option["model"]: option for option in client.get("/model-options").json()}
    stale = client.patch(
        f"/model-options/{initial_options['nano-banana-pro-preview']['id']}",
        json={"enabled": True, "supports_chat": True, "supports_compression": True},
    ).json()
    active_text = _enable_option(client, initial_options["gemini-2.5-flash"])
    active_tts = _enable_option(client, initial_options["gemini-2.5-flash-preview-tts"])
    assert stale["supports_chat"] is True

    sync_response = client.post(f"/model-provider-accounts/{account['id']}/sync-models")
    assert sync_response.status_code == 200
    options = sync_response.json()
    by_model = {option["model"]: option for option in options}

    assert by_model["gemini-2.5-flash"]["supports_chat"] is True
    assert by_model["gemini-2.5-flash"]["supports_compression"] is True
    assert by_model["gemini-2.5-flash"]["enabled"] is True
    assert by_model["gemini-2.5-flash"]["id"] == active_text["id"]
    assert by_model["gemini-2.5-flash-preview-tts"]["supports_tts"] is True
    assert by_model["gemini-2.5-flash-preview-tts"]["supports_chat"] is False
    assert by_model["gemini-2.5-flash-preview-tts"]["enabled"] is True
    assert by_model["gemini-2.5-flash-preview-tts"]["id"] == active_tts["id"]
    assert by_model["nano-banana-pro-preview"]["supports_chat"] is False
    assert by_model["nano-banana-pro-preview"]["supports_compression"] is False
    assert by_model["nano-banana-pro-preview"]["enabled"] is False
    for excluded_model in ["gemini-2.5-flash-image", "veo-3.1-generate-preview", "lyria-3-pro-preview", "gemini-embedding-001", "deep-research-preview-04-2026", "gemini-2.5-computer-use-preview-10-2025"]:
        assert by_model[excluded_model]["supports_chat"] is False
        assert by_model[excluded_model]["supports_compression"] is False

    runtime = client.get("/runtime-settings/default").json()
    runtime_chat_models = {option["model"] for option in runtime["options"]}
    runtime_compression_models = {option["model"] for option in runtime["compression_options"]}
    runtime_tts_models = {option["model"] for option in runtime["tts_options"]}

    assert "gemini-2.5-flash" in runtime_chat_models
    assert "gemini-2.5-flash" in runtime_compression_models
    assert "gemini-2.5-flash-preview-tts" in runtime_tts_models
    assert "gemini-2.5-flash-preview-tts" not in runtime_chat_models
    assert "nano-banana-pro-preview" not in runtime_chat_models
    assert "gemini-2.5-flash-image" not in runtime_chat_models
    assert "veo-3.1-generate-preview" not in runtime_compression_models
