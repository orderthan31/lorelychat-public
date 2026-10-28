"""Wire-level capability/accounting regressions. No provider network calls."""
import json

import httpx
import pytest
from sqlmodel import select

from app.core.config import Settings
from app.db.models import LLMUsageEvent
from app.engine import llm_client as module
from app.engine.llm_client import LLMClient, LLMUnavailableError


def transport(monkeypatch, body, *, status=200, headers=None):
    captured = []
    real_client = httpx.AsyncClient

    def handler(request):
        captured.append(request)
        return httpx.Response(status, json=body, headers=headers)

    mock = httpx.MockTransport(handler)
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: real_client(transport=mock, **kwargs))
    return captured


def client(model, endpoint="https://api.openai.com/v1", provider="openai_compatible", **overrides):
    return LLMClient(Settings(llm_mock=False), overrides={
        "provider": provider, "model": model, "base_url": endpoint,
        "api_key": "synthetic-key", "min_output_tokens": 1234,
        "circuit_breaker_enabled": False, "retry_attempts": 1, **overrides,
    })


COMPATIBLE_BODY = {
    "model": "reported-version-2026",
    "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
    "usage": {"prompt_tokens": 100, "completion_tokens": 40, "total_tokens": 140,
              "prompt_tokens_details": {"cached_tokens": 80},
              "completion_tokens_details": {"reasoning_tokens": 30}},
}


@pytest.mark.asyncio
@pytest.mark.parametrize("model,endpoint,cap,temperature", [
    ("o3", "https://api.openai.com/v1", "max_completion_tokens", None),
    ("gpt-5", "https://api.openai.com/v1", "max_completion_tokens", None),
    ("gpt-5.2", "https://api.openai.com/v1", "max_completion_tokens", None),
    ("gpt-6", "https://api.openai.com/v1", "max_completion_tokens", None),
    ("gpt-4o", "https://api.openai.com/v1", "max_tokens", .7),
    ("gpt-5", "https://other.invalid/v1", "max_tokens", .7),
    ("o3", "http://api.openai.com/v1", "max_tokens", .7),
    ("o3", "https://api.openai.com.evil.invalid/v1", "max_tokens", .7),
    ("grok-4.7", "https://api.x.ai/v1", "max_tokens", 1.0),
    ("grok-3", "https://api.x.ai/v1", "max_tokens", 1.0),
])
async def test_compatible_model_and_endpoint_wire_policy(monkeypatch, model, endpoint, cap, temperature):
    requests = transport(monkeypatch, COMPATIBLE_BODY)
    llm = client(model, endpoint)
    response = await llm._chat_openai_compatible([{"role": "user", "content": "private prompt"}])
    payload = json.loads(requests[0].content)
    assert payload["model"] == model
    assert payload[cap] == 2468
    assert ("max_tokens" in payload) != ("max_completion_tokens" in payload)
    assert payload.get("temperature") == temperature
    assert requests[0].headers["authorization"] == "Bearer synthetic-key"
    assert response.model == model  # Routing identity must not change.
    assert response.requested_model == model
    assert response.reported_model_version == "reported-version-2026"
    assert (response.cached_tokens, response.reasoning_tokens, response.total_tokens) == (80, 30, 140)
    if endpoint == "https://api.x.ai/v1":
        assert payload["top_p"] == .95


@pytest.mark.asyncio
@pytest.mark.parametrize("model,temperature", [
    ("gemini-2.5-pro", .7), ("gemini-3-pro-preview", .7),
    ("gemini-3.1-pro-preview", .7), ("gemini-3.5-flash", None),
    ("gemini-3.5-flash-lite", None), ("gemini-3.6-flash", None),
    ("gemini-3.7-flash", None), ("gemini-3.8-flash", None), ("gemini-4-argon", None),
])
async def test_gemini_generation_policy_and_reported_version(monkeypatch, model, temperature):
    requests = transport(monkeypatch, {
        "modelVersion": "gemini-3.8-flash-001",
        "candidates": [{"content": {"parts": [{"text": "ok"}]}, "finishReason": "STOP"}],
        "usageMetadata": {"promptTokenCount": 100, "candidatesTokenCount": 10,
                          "thoughtsTokenCount": 30, "cachedContentTokenCount": 80, "totalTokenCount": 140},
    })
    response = await client(model, provider="gemini")._chat_gemini([{"role": "user", "content": "hello"}])
    config = json.loads(requests[0].content)["generationConfig"]
    assert config.get("temperature") == temperature
    assert config["maxOutputTokens"] == 2468
    assert requests[0].headers["x-goog-api-key"] == "synthetic-key"
    assert model in str(requests[0].url)
    assert response.model == response.requested_model == model
    assert response.reported_model_version == "gemini-3.8-flash-001"
    assert (response.cached_tokens, response.reasoning_tokens, response.total_tokens) == (80, 30, 140)
    assert response.transport_diagnostics["omitted_parameters"] == (["temperature"] if temperature is None else [])


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint,provider,expected", [
    ("https://api.x.ai/v1", "openai_compatible", True),
    ("https://api.x.ai/v1", "xai", True),
    ("https://other.invalid/v1", "xai", False),
    ("https://api.x.ai.evil.invalid/v1", "xai", False),
    ("https://user@api.x.ai/v1", "xai", False),
])
async def test_cache_affinity_scoped_to_verified_endpoint(monkeypatch, endpoint, provider, expected):
    requests = transport(monkeypatch, COMPATIBLE_BODY)
    llm = client("grok-4.7", endpoint, provider)
    for conversation in ("private-room", "private-room", "other-room", None):
        await llm.chat([{"role": "user", "content": "hello"}], conversation_id=conversation)
    keys = [r.headers.get("x-grok-conv-id") for r in requests]
    if expected:
        assert keys[0] == keys[1] and keys[0] != keys[2] and keys[0]
        assert "private-room" not in keys[0]
        assert keys[3] is None
    else:
        assert keys == [None] * 4
    assert all("store" not in json.loads(r.content) and "prompt_cache_key" not in json.loads(r.content) for r in requests)


@pytest.mark.asyncio
@pytest.mark.parametrize("details,expected", [(None, None), ({}, None), ({"cached_tokens": 0}, 0),
    ({"cached_tokens": -1}, None), ({"cached_tokens": True}, None), ({"cached_tokens": "8"}, None)])
async def test_unknown_cache_is_not_zero(monkeypatch, details, expected):
    body = {**COMPATIBLE_BODY, "usage": {"prompt_tokens_details": details}}
    transport(monkeypatch, body)
    response = await client("local", "https://other.invalid/v1")._chat_openai_compatible([])
    assert response.cached_tokens == expected
    assert response.reasoning_tokens is None
    assert response.total_tokens is None


@pytest.mark.asyncio
async def test_usage_metadata_survives_validation_and_sanitizes_warnings(session, monkeypatch):
    monkeypatch.setattr(module, "engine", session.get_bind())
    transport(monkeypatch, COMPATIBLE_BODY, headers={"warning": '299 provider "PRIVATE BODY synthetic-key"'})
    llm = client("grok-4.7", "https://api.x.ai/v1", stage="command_postprocess")
    response = await llm.chat([], conversation_id="capability-success")
    llm.record_response_outcome(response, status="validated", parse_code="ok", validation_code="ok", metadata_updates={"custom": True})
    event = session.get(LLMUsageEvent, response.usage_event_id)
    assert event.model == "grok-4.7" and event.total_tokens == 140
    assert event.metadata_["requested_model"] == "grok-4.7"
    assert event.metadata_["reported_model_version"] == "reported-version-2026"
    assert event.metadata_["token_details"] == {"cached_tokens": 80, "reasoning_tokens": 30}
    assert event.metadata_["stage"] == "command_postprocess"
    assert event.metadata_["transport"]["provider_warning_count"] == 1
    assert "provider_warning_present" in event.metadata_["transport"]["warning_codes"]
    assert event.metadata_["custom"] is True
    assert "PRIVATE BODY" not in str(event.metadata_) and "synthetic-key" not in str(event.metadata_)


@pytest.mark.asyncio
async def test_failed_transport_keeps_context_but_not_fabricated_usage(session, monkeypatch):
    monkeypatch.setattr(module, "engine", session.get_bind())
    requests = transport(monkeypatch, {"error": "PRIVATE BODY synthetic-key"}, status=400,
                         headers={"warning": "PRIVATE BODY synthetic-key"})
    llm = client("gpt-5", stage="repair")
    with pytest.raises(LLMUnavailableError):
        await llm.chat([], conversation_id="capability-failure")
    event = session.exec(select(LLMUsageEvent).where(LLMUsageEvent.conversation_id == "capability-failure")).one()
    assert event.purpose == "chat_generation" and event.metadata_["stage"] == "repair"
    assert event.metadata_["requested_model"] == "gpt-5"
    assert event.metadata_["reported_model_version"] is None
    assert event.metadata_["transport_attempts"] == len(requests) == 1
    assert event.metadata_["transport"]["omitted_parameters"] == ["temperature"]
    assert event.metadata_["transport"]["provider_warning_count"] == 1
    assert event.prompt_tokens is event.completion_tokens is event.total_tokens is None
    assert "PRIVATE BODY" not in str(event.metadata_) and "synthetic-key" not in str(event.metadata_)


@pytest.mark.asyncio
@pytest.mark.parametrize("model,endpoint,effort", [("gpt-5.2", "https://api.openai.com/v1", "high"),
    ("grok-4.7", "https://api.x.ai/v1", "high")])
async def test_selected_reasoning_is_preserved(monkeypatch, model, endpoint, effort):
    requests = transport(monkeypatch, COMPATIBLE_BODY)
    await client(model, endpoint, reasoning_effort=effort)._chat_openai_compatible([])
    assert json.loads(requests[0].content)["reasoning_effort"] == effort


@pytest.mark.asyncio
async def test_explicit_xai_sampling_is_not_rejected_or_silently_clamped(monkeypatch):
    requests = transport(monkeypatch, COMPATIBLE_BODY)
    await client("grok-4.7", "https://api.x.ai/v1", temperature=1.1, top_p=1.0)._chat_openai_compatible([])
    payload = json.loads(requests[0].content)
    assert payload["temperature"] == 1.1 and payload["top_p"] == 1.0


@pytest.mark.asyncio
async def test_modern_gemini_omits_all_selected_sampling_but_keeps_thinking(monkeypatch):
    requests = transport(monkeypatch, {"candidates": []})
    response = await client("gemini-3.8-flash", provider="gemini", temperature=.2, top_p=.8,
                            top_k=30, thinking_level="high")._chat_gemini([])
    config = json.loads(requests[0].content)["generationConfig"]
    assert not {"temperature", "topP", "topK"} & config.keys()
    assert config["thinkingConfig"] == {"thinkingLevel": "high"}
    assert response.transport_diagnostics["omitted_parameters"] == ["temperature", "topP", "topK"]
    assert response.reported_model_version is None  # Not inferred from request alias.


@pytest.mark.asyncio
async def test_old_gemini_keeps_selected_sampling_and_thinking_budget(monkeypatch):
    requests = transport(monkeypatch, {"candidates": []})
    await client("gemini-2.5-pro", provider="gemini", temperature=.2, top_p=.8,
                 top_k=30, thinking_budget=4096)._chat_gemini([])
    config = json.loads(requests[0].content)["generationConfig"]
    assert config == {"temperature": .2, "topP": .8, "topK": 30,
                      "thinkingConfig": {"thinkingBudget": 4096}, "maxOutputTokens": 2468}


@pytest.mark.asyncio
async def test_openai_reasoning_sampling_none_is_family_specific(monkeypatch):
    requests = transport(monkeypatch, COMPATIBLE_BODY)
    for model in ("gpt-5", "gpt-5.2", "gpt-6-astra"):
        await client(model, reasoning_effort="none", temperature=.3, top_p=.9)._chat_openai_compatible([])
    payloads = [json.loads(r.content) for r in requests]
    assert "temperature" not in payloads[0] and "top_p" not in payloads[0]
    assert payloads[1]["temperature"] == .3 and payloads[1]["top_p"] == .9
    # Unsupported none is NOT silently replaced with a different reasoning tier.
    assert payloads[2]["reasoning_effort"] == "none" and "temperature" not in payloads[2]


@pytest.mark.asyncio
async def test_untrusted_version_and_warning_body_are_not_diagnostics(monkeypatch):
    transport(monkeypatch, {**COMPATIBLE_BODY, "model": "PRIVATE prompt with spaces", "warnings": [{"message": "PRIVATE"}]})
    response = await client("local", "https://other.invalid/v1")._chat_openai_compatible([])
    assert response.reported_model_version is None
    assert "PRIVATE" not in str(response.transport_diagnostics)


@pytest.mark.asyncio
async def test_circuit_failure_does_not_invent_network_attempt(session, monkeypatch):
    monkeypatch.setattr(module, "engine", session.get_bind())
    requests = transport(monkeypatch, {"error": "busy"}, status=503)
    llm = client("circuit-test", circuit_breaker_enabled=True, circuit_failure_threshold=1)
    llm.reset_circuit_breakers()
    try:
        for room in ("first-failure", "open-circuit"):
            with pytest.raises(LLMUnavailableError):
                await llm.chat([], conversation_id=room)
        event = session.exec(select(LLMUsageEvent).where(LLMUsageEvent.conversation_id == "open-circuit")).one()
        assert event.metadata_["transport_attempts"] == 0
        assert len(requests) == 1
    finally:
        llm.reset_circuit_breakers()


@pytest.mark.asyncio
async def test_compatible_unknown_family_does_not_strip_explicit_effort(monkeypatch):
    requests = transport(monkeypatch, COMPATIBLE_BODY)
    await client("custom", "https://custom.invalid/v1", reasoning_effort="high")._chat_openai_compatible([])
    payload = json.loads(requests[0].content)
    assert payload["reasoning_effort"] == "high"
    assert payload["max_tokens"] == 2468 and payload["temperature"] == .7
