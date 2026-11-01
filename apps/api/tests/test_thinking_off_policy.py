"""Deployment thinking preference: actual mocked HTTP, never provider calls."""
import json
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.engine.llm_client import LLMClient


XAI = ("xai", "grok-4.3", "https://api.x.ai/v1")
ROUTER = ("openai_compatible", "nvidia/nemotron-3-ultra-550b-a55b:free", "https://openrouter.ai/api/v1")
CHAT_PURPOSES = ["chat_generation", "chat_generation_fallback", "chat_command_postprocess"]
GEMINI_MODELS = ["gemini-2.5-pro", "gemini-flash-latest", "gemini-flash-lite-latest",
                 "gemini-3-flash-preview", "gemini-3.1-pro-preview", "gemini-3.7-flash", "gemini-3.8-flash"]
MESSAGES = [{"role": "user", "content": "synthetic input"}]
FORMAT = {"type": "json_object", "name": "free_body_replies", "strict": True,
          "schema": {"type": "object", "properties": {"replies": {"type": "array", "items": {
              "type": "object", "properties": {"who": {"type": "string"}, "text": {"type": "string"}},
              "required": ["who", "text"], "additionalProperties": False}}},
              "required": ["replies"], "additionalProperties": False}}
BODY = {"model": "reported-version", "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 100, "completion_tokens": 40, "total_tokens": 140,
                  "prompt_tokens_details": {"cached_tokens": 80}, "completion_tokens_details": {"reasoning_tokens": 30}}}
GEMINI_BODY = {"modelVersion": "reported-version", "candidates": [{"content": {"parts": [{"text": "ok"}]}, "finishReason": "STOP"}],
               "usageMetadata": {"promptTokenCount": 100, "candidatesTokenCount": 10, "totalTokenCount": 140,
                                 "cachedContentTokenCount": 80, "thoughtsTokenCount": 30}}


def client(route=XAI, *, policy="off_when_supported", profile="chat", purpose="chat_generation", **overrides):
    provider, model, endpoint = route
    return LLMClient(Settings(_env_file=None, llm_mock=False, chat_thinking_policy=policy,
                              chat_response_protocol="free_body_json", chat_command_postprocess_mode="combined_overlay"),
                     profile=profile, purpose=purpose, overrides={
                         "provider": provider, "model": model, "base_url": endpoint, "api_key": "synthetic-key",
                         "min_output_tokens": 1234, "retry_attempts": 1, "circuit_breaker_enabled": False, **overrides})


@pytest.fixture
def wire(monkeypatch):
    requests = []
    original = httpx.AsyncClient

    def handle(request):
        requests.append(request)
        return httpx.Response(200, json=GEMINI_BODY if ":generateContent" in str(request.url) else BODY)

    transport = httpx.MockTransport(handle)
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original(transport=transport, **kwargs))
    # Public chat dispatch is exercised without usage persistence.
    monkeypatch.setattr(LLMClient, "_record_usage", lambda *args, **kwargs: None)
    return requests


async def send(llm, wire):
    response = await llm.chat(MESSAGES, response_format=FORMAT, conversation_id="synthetic-room")
    return json.loads(wire[-1].content), response


def diagnostic(response):
    return response.transport_diagnostics["thinking_policy"]


def assert_no_auto(payload):
    assert not {"reasoning_effort", "reasoning", "thinking_level", "thinking_budget"} & payload.keys()
    assert "thinkingConfig" not in payload.get("generationConfig", {})


def test_deployment_default_and_validation(monkeypatch):
    monkeypatch.delenv("CHAT_THINKING_POLICY", raising=False)
    assert Settings(_env_file=None).chat_thinking_policy == "provider_default"
    assert Settings.model_fields["chat_thinking_policy"].default == "provider_default"
    monkeypatch.setenv("CHAT_THINKING_POLICY", "off_when_supported")
    assert Settings(_env_file=None).chat_thinking_policy == "off_when_supported"
    with pytest.raises(ValidationError):
        Settings(_env_file=None, chat_thinking_policy="minimal")
    root = Path(__file__).resolve().parents[3]
    assert "CHAT_THINKING_POLICY=provider_default" in (root / ".env.example").read_text()
    assert "CHAT_THINKING_POLICY: ${CHAT_THINKING_POLICY:-provider_default}" in (root / "docker-compose.yml").read_text()


@pytest.mark.asyncio
@pytest.mark.parametrize("route,parameter,value", [(XAI, "reasoning_effort", "none"), (ROUTER, "reasoning", {"enabled": False})])
@pytest.mark.parametrize("purpose", CHAT_PURPOSES)
async def test_exact_off_on_wire_preserves_all_other_controls(wire, route, parameter, value, purpose):
    default, before = await send(client(route, policy="provider_default", purpose=purpose), wire)
    enabled, after = await send(client(route, purpose=purpose), wire)
    assert enabled == {**default, parameter: value}
    assert enabled["max_tokens"] == 2468
    assert enabled["response_format"]["json_schema"]["schema"] == FORMAT["schema"]
    assert enabled["response_format"]["json_schema"]["name"] == "free_body_replies"
    assert {k: v for k, v in wire[0].headers.items() if k != "content-length"} == {
        k: v for k, v in wire[1].headers.items() if k != "content-length"}
    assert wire[0].url == wire[1].url
    assert len(wire) == 2
    assert after.provider == before.provider == route[0]
    assert after.model == after.requested_model == route[1]
    assert after.reported_model_version == "reported-version"
    assert (after.cached_tokens, after.reasoning_tokens, after.total_tokens) == (80, 30, 140)
    assert after.budget_diagnostics == before.budget_diagnostics
    assert diagnostic(after) == {"requested": "off_when_supported", "applied_on_wire": True,
                                 "parameter": parameter, "omission_reason": None,
                                 "explicit_parameters_on_wire": []}
    assert diagnostic(before)["omission_reason"] == "provider_default"


@pytest.mark.asyncio
@pytest.mark.parametrize("profile,purpose", [("compression", "compression"), ("compression", "compression_fallback"),
    ("compression", "chat_generation"), ("chat", "compression"), ("chat", "compression_fallback"), ("chat", "other")])
@pytest.mark.parametrize("route", [XAI, ROUTER])
async def test_nonchat_scope_byte_identical(wire, route, profile, purpose):
    await send(client(route, policy="provider_default", profile=profile, purpose=purpose), wire)
    payload, response = await send(client(route, profile=profile, purpose=purpose), wire)
    assert wire[0].content == wire[1].content
    assert_no_auto(payload)
    assert diagnostic(response)["omission_reason"] == "nonchat_scope"


@pytest.mark.asyncio
@pytest.mark.parametrize("model", GEMINI_MODELS)
async def test_all_configured_google_options_unchanged(wire, model):
    route = ("gemini", model, "https://api.x.ai/v1")  # Actual transport, not advertised base URL, wins.
    await send(client(route, policy="provider_default"), wire)
    payload, response = await send(client(route), wire)
    assert wire[0].content == wire[1].content
    assert_no_auto(payload)
    assert payload["generationConfig"]["maxOutputTokens"] == 2468
    assert payload["generationConfig"]["responseMimeType"] == "application/json"
    assert diagnostic(response)["omission_reason"] == "unsupported_or_unknown_capability"
    assert response.reasoning_tokens == 30


@pytest.mark.asyncio
@pytest.mark.parametrize("route", [(ROUTER[0], "nex-agi/nex-n2-pro:free", ROUTER[2]),
    (XAI[0], "grok-4.3-latest", XAI[2]), (XAI[0], "grok-4.7", XAI[2]),
    (ROUTER[0], "nvidia/nemotron-3-ultra-550b-a55b", ROUTER[2]),
    ("gemini", "grok-4.3", XAI[2]), ("openai_compatible", "unknown", XAI[2])])
async def test_unknown_model_or_wrong_transport_noop(wire, route):
    payload, response = await send(client(route), wire)
    assert_no_auto(payload)
    assert diagnostic(response)["omission_reason"] == "unsupported_or_unknown_capability"


@pytest.mark.asyncio
@pytest.mark.parametrize("route", [XAI, ROUTER])
@pytest.mark.parametrize("variant", ["custom", "scheme", "spoof", "path", "port", "query", "fragment", "userinfo",
                                     "empty_query", "empty_fragment", "empty_userinfo", "null"])
async def test_unverified_actual_endpoints_noop(wire, route, variant):
    provider, model, endpoint = route
    from urllib.parse import urlsplit
    parsed = urlsplit(endpoint)
    variants = {"custom": "https://custom.invalid/v1", "scheme": endpoint.replace("https:", "http:"),
                "spoof": endpoint.replace(parsed.netloc, parsed.netloc + ".invalid"), "path": endpoint + "/extra",
                "port": endpoint.replace(parsed.netloc, parsed.netloc + ":8443"), "query": endpoint + "?q=1",
                "fragment": endpoint + "#fragment", "userinfo": endpoint.replace("https://", "https://user@"),
                "empty_query": endpoint + "?", "empty_fragment": endpoint + "#",
                "empty_userinfo": endpoint.replace("https://", "https://@"), "null": None}
    payload, response = await send(client((provider, model, variants[variant])), wire)
    assert_no_auto(payload)
    assert diagnostic(response)["omission_reason"] == "unverified_endpoint"
    assert diagnostic(response)["applied_on_wire"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("route", [XAI, ROUTER])
@pytest.mark.parametrize("overrides", [{"reasoning_effort": "high"}, {"reasoning_effort": "none"}, {"reasoning_effort": 0},
    {"thinking_budget": 0}, {"thinking_level": "high"}, {"reasoning_effort": "high", "thinking_budget": 0}])
async def test_explicit_controls_block_automatic_even_when_not_supported_by_transport(wire, route, overrides):
    await send(client(route, policy="provider_default", **overrides), wire)
    payload, response = await send(client(route, **overrides), wire)
    assert wire[0].content == wire[1].content
    assert "reasoning" not in payload
    assert payload.get("reasoning_effort") == overrides.get("reasoning_effort")
    diag = diagnostic(response)
    assert diag["applied_on_wire"] is False
    assert diag["parameter"] is None
    serialized = ["reasoning_effort"] if "reasoning_effort" in overrides else []
    assert diag["explicit_parameters_on_wire"] == serialized
    assert diag["omission_reason"] == ("explicit_override_preserved" if set(overrides) == {"reasoning_effort"}
                                       else "explicit_override_not_serialized")


@pytest.mark.asyncio
@pytest.mark.parametrize("overrides,expected", [({"thinking_budget": 0}, {"thinkingBudget": 0}),
    ({"thinking_level": "high"}, {"thinkingLevel": "high"}),
    ({"thinking_budget": 4096, "thinking_level": "high"}, {"thinkingBudget": 4096, "thinkingLevel": "high"})])
async def test_gemini_explicit_controls_preserved(wire, overrides, expected):
    route = ("gemini", "gemini-2.5-pro", None)
    await send(client(route, policy="provider_default", **overrides), wire)
    payload, response = await send(client(route, **overrides), wire)
    assert wire[0].content == wire[1].content
    assert payload["generationConfig"]["thinkingConfig"] == expected
    assert diagnostic(response)["omission_reason"] == "explicit_override_preserved"
    assert set(diagnostic(response)["explicit_parameters_on_wire"]) == {"thinkingConfig." + key for key in expected}


@pytest.mark.asyncio
async def test_none_controls_are_not_explicit_and_metadata_is_sanitized(wire):
    payload, response = await send(client(reasoning_effort=None, thinking_budget=None, thinking_level=None), wire)
    assert payload["reasoning_effort"] == "none"
    assert "synthetic" not in json.dumps(response.transport_diagnostics)
    assert "https" not in json.dumps(diagnostic(response))


def test_preflight_diagnostics_do_not_claim_wire_application():
    diag = client().transport_diagnostics()["thinking_policy"]
    assert diag["applied_on_wire"] is False
    assert diag["omission_reason"] == "transport_unconfirmed"


@pytest.mark.asyncio
@pytest.mark.parametrize("purpose", ["compression", "compression_fallback"])
@pytest.mark.parametrize("route,overrides", [(XAI, {"reasoning_effort": "high"}),
    (ROUTER, {"reasoning_effort": "high"}), (("gemini", "gemini-2.5-pro", None), {"thinking_budget": 0})])
async def test_compression_preserves_explicit_controls(wire, purpose, route, overrides):
    await send(client(route, profile="compression", purpose=purpose, policy="provider_default", **overrides), wire)
    _, response = await send(client(route, profile="compression", purpose=purpose, **overrides), wire)
    assert wire[0].content == wire[1].content
    assert diagnostic(response)["omission_reason"] == "nonchat_scope"
    assert diagnostic(response)["applied_on_wire"] is False


@pytest.mark.asyncio
async def test_custom_endpoint_preserves_existing_effort_without_guessing_fields(wire):
    route = ("openai_compatible", "custom-model", "https://custom.invalid/v1")
    payload, response = await send(client(route, reasoning_effort="high", thinking_budget=0), wire)
    assert payload["reasoning_effort"] == "high"
    assert "thinking_budget" not in payload and "reasoning" not in payload
    assert diagnostic(response)["explicit_parameters_on_wire"] == ["reasoning_effort"]
    assert diagnostic(response)["omission_reason"] == "explicit_override_not_serialized"


@pytest.mark.parametrize("route", [XAI, ROUTER])
@pytest.mark.parametrize("transport", ["generate_content", "responses", "unknown"])
def test_capability_registry_requires_chat_completions(route, transport):
    from app.engine.provider_capabilities import thinking_off_parameters
    params, reason = thinking_off_parameters(model=route[1], endpoint=route[2], transport=transport)
    assert params == {}
    assert reason == "unsupported_or_unknown_capability"
