import json

import pytest
import httpx
from app.core.config import Settings
from app.engine.llm_client import CircuitOpenError, LLMClient, LLMResponse, LLMUnavailableError


@pytest.mark.asyncio
async def test_mock_llm_returns_json_response():
    client = LLMClient(Settings(llm_mock=True))
    response = await client.chat([
        {"role": "user", "content": "안녕"}
    ], response_format={"type": "json_object"})
    assert "Mock reply to" in response.content
    assert "안녕" in response.content


def test_default_llm_timeout_allows_slow_local_models():
    assert Settings().llm_timeout_seconds == 120.0


def test_json_object_response_format_is_preserved_for_structured_outputs():
    client = LLMClient(Settings(llm_mock=False))
    assert client.normalize_response_format({"type": "json_object"}) == {"type": "json_object"}


def test_higher_temperature_is_isolated_to_xai():
    settings = Settings(llm_mock=False)
    xai_client = LLMClient(settings, overrides={"provider": "xai"})
    compatible_client = LLMClient(settings, overrides={"provider": "openai_compatible"})

    assert xai_client.openai_compatible_temperature() == 1.1
    assert compatible_client.openai_compatible_temperature() == 0.7


@pytest.mark.asyncio
async def test_xai_uses_openai_compatible_chat_with_strict_structured_output(monkeypatch):
    captured = {}

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            captured["timeout"] = kwargs["timeout"]

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, **kwargs):
            captured["url"] = url
            captured["kwargs"] = kwargs
            return httpx.Response(
                200,
                request=httpx.Request("POST", url),
                json={
                    "choices": [{
                        "message": {"role": "assistant", "content": '{"replies":[{"dialogue":"안녕"}]}'},
                        "finish_reason": "stop",
                    }],
                    "usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18},
                },
            )

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)
    client = LLMClient(
        Settings(llm_mock=False),
        overrides={
            "provider": "xai",
            "provider_type": "xai",
            "model": "grok-4.5",
            "base_url": "https://api.x.ai/v1",
            "api_key": "xai-test-key",
        },
    )
    schema = {
        "type": "object",
        "properties": {"replies": {"type": "array", "items": {"type": "object"}}},
        "required": ["replies"],
    }

    response = await client._chat_openai_compatible(
        [{"role": "user", "content": "안녕"}],
        response_format={"type": "json_object", "name": "character_chat_replies", "schema": schema},
    )

    assert captured["url"] == "https://api.x.ai/v1/chat/completions"
    assert captured["kwargs"]["headers"] == {"Authorization": "Bearer xai-test-key"}
    payload = captured["kwargs"]["json"]
    assert payload["model"] == "grok-4.5"
    assert payload["temperature"] == 1.1
    assert payload["response_format"] == {
        "type": "json_schema",
        "json_schema": {
            "name": "character_chat_replies",
            "strict": True,
            "schema": schema,
        },
    }
    assert response.provider == "xai"
    assert response.model == "grok-4.5"
    assert response.finish_reason == "stop"
    assert response.prompt_tokens == 11
    assert response.completion_tokens == 7
    assert response.total_tokens == 18


@pytest.mark.asyncio
async def test_gemini_json_object_sends_response_schema(monkeypatch):
    settings = Settings(llm_mock=False, gemini_api_key="test-key")
    client = LLMClient(settings, overrides={"provider": "gemini", "model": "gemini-3-flash-preview"})
    captured = {}

    async def fake_post(url, *, model, payload):
        captured["payload"] = payload
        return httpx.Response(200, json={
            "candidates": [{
                "finishReason": "STOP",
                "content": {"parts": [{"text": '{"replies":[{"reply_type":"character","dialogue":"ok"}]}' }]},
            }],
            "usageMetadata": {"promptTokenCount": 1, "candidatesTokenCount": 1, "totalTokenCount": 2},
        })

    monkeypatch.setattr(client, "_post_gemini_with_retries", fake_post)

    response_format = {
        "type": "json_object",
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "replies": {
                    "type": "array",
                    "minItems": 2,
                    "maxItems": 3,
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {"thought": {"type": "string", "maxLength": 96}},
                    },
                },
            },
            "required": ["replies"],
        },
    }
    response = await client._chat_gemini(
        [{"role": "user", "content": "안녕"}],
        response_format=response_format,
    )

    assert response.finish_reason == "STOP"
    assert captured["payload"]["generationConfig"]["responseMimeType"] == "application/json"
    schema = captured["payload"]["generationConfig"]["responseSchema"]
    assert schema["required"] == ["replies"]
    assert schema["properties"]["replies"]["minItems"] == 2
    assert schema["properties"]["replies"]["maxItems"] == 3
    assert schema["properties"]["replies"]["items"]["properties"]["thought"]["maxLength"] == 96
    assert "additionalProperties" not in json.dumps(schema)
    assert response_format["schema"]["additionalProperties"] is False
    assert response_format["schema"]["properties"]["replies"]["items"]["additionalProperties"] is False


@pytest.mark.asyncio
async def test_gemini_returns_retryable_non_stop_finish_reason_to_runtime_without_hidden_regeneration(monkeypatch):
    settings = Settings(llm_mock=False, gemini_api_key="test-key")
    client = LLMClient(settings, overrides={"provider": "gemini", "model": "gemini-3-flash-preview", "retry_attempts": 2})
    calls = []

    async def fake_post(url, *, model, payload):
        calls.append(payload)
        return httpx.Response(200, json={
            "candidates": [{"finishReason": "MAX_TOKENS", "content": {"parts": [{"text": '{"replies":[{"reply_type":"character"'}]}}],
            "usageMetadata": {},
        })

    monkeypatch.setattr(client, "_post_gemini_with_retries", fake_post)

    response = await client._chat_gemini([{"role": "user", "content": "안녕"}], response_format={"type": "json_object"})

    assert len(calls) == 1
    assert response.finish_reason == "MAX_TOKENS"
    assert response.generation_attempts == 1


def test_default_gemini_transport_retry_budget_is_two_attempts():
    client = LLMClient(
        Settings(llm_mock=False, gemini_api_key="test-key"),
        overrides={"provider": "gemini", "model": "gemini-3-flash-preview"},
    )

    assert client.gemini_retry_attempts() == 2


def test_compression_gemini_ignores_local_placeholder_api_key():
    settings = Settings(
        llm_mock=False,
        compression_llm_api_key="not-needed-for-local",
        gemini_api_key="real-gemini-key",
    )
    client = LLMClient(settings, profile="compression", overrides={"provider": "gemini", "model": "gemini-3-flash-preview"})

    assert client.gemini_api_key() == "real-gemini-key"


@pytest.mark.asyncio
async def test_local_openai_compatible_does_not_fallback_even_when_configured(monkeypatch):
    settings = Settings(
        llm_mock=False,
        chat_llm_provider="openai_compatible",
        gemini_api_key="test-key",
        local_llm_fallback_enabled=True,
        local_llm_fallback_model="gemini-3-flash-preview",
    )
    client = LLMClient(settings)
    async def fake_local(*args, **kwargs):
        raise LLMUnavailableError("local off")

    async def fake_gemini(*args, **kwargs):  # pragma: no cover - should not be called
        raise AssertionError("Hidden Gemini fallback must not run")

    monkeypatch.setattr(client, "_chat_openai_compatible", fake_local)
    monkeypatch.setattr(client, "_chat_gemini", fake_gemini)

    with pytest.raises(LLMUnavailableError):
        await client.chat([{"role": "user", "content": "안녕"}], response_format={"type": "json_object"})


@pytest.mark.asyncio
async def test_local_openai_compatible_does_not_fallback_without_gemini_key(monkeypatch):
    settings = Settings(
        llm_mock=False,
        chat_llm_provider="openai_compatible",
        gemini_api_key=None,
        google_api_key=None,
        local_llm_fallback_enabled=True,
    )
    client = LLMClient(settings)

    async def fake_local(*args, **kwargs):
        raise LLMUnavailableError("local off")

    async def fake_gemini(*args, **kwargs):  # pragma: no cover - should not be called
        raise AssertionError("Gemini fallback should require an API key")

    monkeypatch.setattr(client, "_chat_openai_compatible", fake_local)
    monkeypatch.setattr(client, "_chat_gemini", fake_gemini)

    with pytest.raises(LLMUnavailableError):
        await client.chat([{"role": "user", "content": "안녕"}], response_format={"type": "json_object"})


@pytest.mark.asyncio
async def test_gemini_api_key_is_sent_in_header_not_query(monkeypatch):
    captured = {}

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, **kwargs):
            captured["url"] = url
            captured["kwargs"] = kwargs
            return httpx.Response(200, request=httpx.Request("POST", url), json={})

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)
    client = LLMClient(
        Settings(llm_mock=False, gemini_api_key="test-header-key"),
        overrides={"provider": "gemini", "model": "gemini-test", "retry_attempts": 1},
    )

    response = await client._post_gemini_with_retries(
        "https://generativelanguage.googleapis.com/v1beta/models/gemini-test:generateContent",
        model="gemini-test",
        payload={"contents": []},
    )

    assert "?key=" not in captured["url"]
    assert "params" not in captured["kwargs"]
    assert captured["kwargs"]["headers"] == {"x-goog-api-key": "test-header-key"}
    assert response.extensions["lorechat_attempts"] == 1


@pytest.mark.asyncio
async def test_gemini_transport_retry_records_attempt_count_after_503(monkeypatch):
    calls = 0

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, **kwargs):
            nonlocal calls
            calls += 1
            request = httpx.Request("POST", url)
            if calls == 1:
                return httpx.Response(503, request=request, json={"error": {"message": "busy"}})
            return httpx.Response(200, request=request, json={"candidates": []})

    async def no_sleep(*args, **kwargs):
        return None

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)
    monkeypatch.setattr("app.engine.llm_client.asyncio.sleep", no_sleep)
    client = LLMClient(
        Settings(llm_mock=False, gemini_api_key="test-header-key"),
        overrides={"provider": "gemini", "model": "gemini-test", "retry_attempts": 2},
    )

    response = await client._post_gemini_with_retries(
        "https://generativelanguage.googleapis.com/v1beta/models/gemini-test:generateContent",
        model="gemini-test",
        payload={"contents": []},
    )

    assert calls == 2
    assert response.status_code == 200
    assert response.extensions["lorechat_attempts"] == 2


@pytest.mark.asyncio
async def test_openai_compatible_retries_503_and_honors_retry_after(monkeypatch):
    calls = 0
    sleeps = []

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, **kwargs):
            nonlocal calls
            calls += 1
            request = httpx.Request("POST", url)
            if calls == 1:
                return httpx.Response(503, request=request, headers={"Retry-After": "3"}, json={"error": {"message": "busy"}})
            return httpx.Response(200, request=request, json={
                "choices": [{"message": {"content": '{"replies":[]}'}, "finish_reason": "stop"}],
                "usage": {},
            })

    async def capture_sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)
    monkeypatch.setattr("app.engine.llm_client.asyncio.sleep", capture_sleep)
    client = LLMClient(Settings(llm_mock=False), overrides={
        "provider": "openai_compatible",
        "model": "local-test",
        "base_url": "http://local.test/v1",
        "api_key": "test-only",
        "retry_attempts": 2,
    })

    response = await client._chat_openai_compatible([{"role": "user", "content": "안녕"}])

    assert calls == 2
    assert sleeps == [3.0]
    assert response.generation_attempts == 2


@pytest.mark.asyncio
async def test_openai_compatible_retries_timeout_then_succeeds(monkeypatch):
    calls = 0

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, **kwargs):
            nonlocal calls
            calls += 1
            request = httpx.Request("POST", url)
            if calls == 1:
                raise httpx.ReadTimeout("slow provider", request=request)
            return httpx.Response(200, request=request, json={
                "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
                "usage": {},
            })

    async def no_sleep(*args, **kwargs):
        return None

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)
    monkeypatch.setattr("app.engine.llm_client.asyncio.sleep", no_sleep)
    client = LLMClient(Settings(llm_mock=False), overrides={
        "provider": "openai_compatible",
        "model": "local-test",
        "base_url": "http://local.test/v1",
        "api_key": "test-only",
        "retry_attempts": 2,
    })

    response = await client._chat_openai_compatible([{"role": "user", "content": "안녕"}])

    assert calls == 2
    assert response.generation_attempts == 2


@pytest.mark.asyncio
async def test_openai_compatible_does_not_retry_non_retryable_400(monkeypatch):
    calls = 0

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, **kwargs):
            nonlocal calls
            calls += 1
            request = httpx.Request("POST", url)
            return httpx.Response(400, request=request, json={"error": {"message": "bad request"}})

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)
    client = LLMClient(Settings(llm_mock=False), overrides={
        "provider": "openai_compatible",
        "model": "local-test",
        "base_url": "http://local.test/v1",
        "api_key": "test-only",
        "retry_attempts": 3,
    })

    with pytest.raises(LLMUnavailableError) as captured:
        await client._chat_openai_compatible([{"role": "user", "content": "안녕"}])

    assert calls == 1
    assert captured.value.transport_attempts == 1
    assert captured.value.http_status == 400


@pytest.mark.asyncio
async def test_provider_circuit_opens_after_consecutive_unavailable_errors(monkeypatch):
    LLMClient.reset_circuit_breakers()
    client = LLMClient(
        Settings(llm_mock=False),
        overrides={
            "provider": "openai_compatible",
            "model": "unstable-model",
            "base_url": "http://provider.test/v1",
            "circuit_failure_threshold": 2,
            "circuit_cooldown_seconds": 60,
        },
    )
    calls = 0

    async def unavailable(*args, **kwargs):
        nonlocal calls
        calls += 1
        raise LLMUnavailableError("provider 503")

    monkeypatch.setattr(client, "_chat_openai_compatible", unavailable)

    for _ in range(2):
        with pytest.raises(LLMUnavailableError, match="provider 503"):
            await client.chat([{"role": "user", "content": "안녕"}])

    with pytest.raises(CircuitOpenError, match="circuit breaker is open"):
        await client.chat([{"role": "user", "content": "또 시도"}])

    assert calls == 2
    LLMClient.reset_circuit_breakers()


@pytest.mark.asyncio
async def test_provider_success_resets_consecutive_circuit_failures(monkeypatch):
    LLMClient.reset_circuit_breakers()
    client = LLMClient(
        Settings(llm_mock=False),
        overrides={
            "provider": "openai_compatible",
            "model": "recovering-model",
            "base_url": "http://provider.test/v1",
            "circuit_failure_threshold": 2,
            "circuit_cooldown_seconds": 60,
        },
    )
    outcomes = [
        LLMUnavailableError("first failure"),
        LLMResponse(content="recovered", provider="openai_compatible", model="recovering-model"),
        LLMUnavailableError("failure after recovery"),
        LLMResponse(content="still callable", provider="openai_compatible", model="recovering-model"),
    ]

    async def sequenced(*args, **kwargs):
        outcome = outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(client, "_chat_openai_compatible", sequenced)

    with pytest.raises(LLMUnavailableError, match="first failure"):
        await client.chat([{"role": "user", "content": "1"}])
    assert (await client.chat([{"role": "user", "content": "2"}])).content == "recovered"
    with pytest.raises(LLMUnavailableError, match="failure after recovery"):
        await client.chat([{"role": "user", "content": "3"}])
    assert (await client.chat([{"role": "user", "content": "4"}])).content == "still callable"
    assert not outcomes
    LLMClient.reset_circuit_breakers()
