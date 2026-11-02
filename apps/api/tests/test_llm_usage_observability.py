import hashlib
import httpx

import pytest
from sqlmodel import select

from app.core.config import Settings
from app.db.models import Character, LLMUsageEvent
from app.engine.character_runtime import CharacterRuntime
from app.engine import llm_client as llm_client_module
from app.engine.llm_client import LLMClient, LLMResponse, LLMUnavailableError, LLMPolicyBlockedError


ROUTE_KEYS = {"provider_account_id", "model_option_key", "route_revision", "requested_route",
              "effective_route", "attempt_trace"}


def assert_observed_route(metadata, client, *, inside_task):
    # Additive content-free transport observations are now part of the ledger.
    revision = metadata["route_revision"]
    assert len(revision) == 64 and all(c in "0123456789abcdef" for c in revision)
    route = {"provider": client.provider(), "model": client.model(),
             "provider_account_id": None, "model_option_key": None, "route_revision": revision}
    assert metadata["provider_account_id"] is metadata["model_option_key"] is None
    assert metadata["effective_route"] == route
    assert metadata["requested_route"] == (route if inside_task else None)
    assert metadata["attempt_trace"] == []
    if inside_task:
        # This test injects a typed failure above the actual-send boundary.
        assert metadata["network_attempts"] == 0
        assert metadata["network_attempt_limit"] == 3
        assert "fallback_used" not in metadata


def test_record_usage_persists_finish_reason_and_response_attempt_metadata(session, monkeypatch):
    monkeypatch.setattr(llm_client_module, "engine", session.get_bind())
    client = LLMClient(
        settings=Settings(
            llm_mock=False,
            chat_llm_provider="gemini",
            chat_llm_model="gemini-test",
            gemini_api_key="test-only-key",
        ),
        profile="chat",
    )

    response = LLMResponse(
        content="structured response",
        provider="gemini",
        model="gemini-test",
        prompt_tokens=12,
        completion_tokens=4,
        total_tokens=16,
        finish_reason="STOP",
        generation_attempts=2,
    )
    client._record_usage(response, conversation_id="conv_usage_observability")
    client.record_response_outcome(
        response,
        status="validated",
        parse_code="json_ok",
        validation_code="schema_ok",
    )

    event = session.exec(
        select(LLMUsageEvent).where(LLMUsageEvent.conversation_id == "conv_usage_observability")
    ).one()
    assert response.usage_event_id == event.id
    assert_observed_route(event.metadata_, client, inside_task=False)
    assert {k: v for k, v in event.metadata_.items() if k not in ROUTE_KEYS} == {
        "status": "validated",
        "stage": "chat_generation",
        "requested_model": "gemini-test",
        "reported_model_version": None,
        "token_details": {"cached_tokens": None, "reasoning_tokens": None},
        "finish_reason": "STOP",
        "response_length": len("structured response"),
        "response_sha256": hashlib.sha256(b"structured response").hexdigest(),
        "transport_attempts": 2,
        "generation_attempts": 2,
        "parse_code": "json_ok",
        "validation_code": "schema_ok",
    }


@pytest.mark.asyncio
async def test_empty_compression_response_logs_and_persists_safe_diagnostics(session, monkeypatch, caplog):
    monkeypatch.setattr(llm_client_module, "engine", session.get_bind())
    client = LLMClient(
        Settings(llm_mock=False, gemini_api_key="test-only-secret"),
        profile="compression", purpose="conversation_compression",
        overrides={"provider": "gemini", "model": "gemini-test"},
    )

    async def fake_post(url, *, model, payload):
        return httpx.Response(200, json={
            "candidates": [],
            "promptFeedback": {"blockReason": "SAFETY", "blockReasonMessage": "sensitive private text"},
            "usageMetadata": {"promptTokenCount": 8, "thoughtsTokenCount": 3},
        })

    monkeypatch.setattr(client, "_post_gemini_with_retries", fake_post)
    # Provider safety blocks terminate before semantic parse/repair.
    with pytest.raises(LLMPolicyBlockedError) as exc:
        await client.chat(
            [{"role": "user", "content": "sensitive private text"}],
            conversation_id="conv_empty_compression_diagnostics",
        )
    assert exc.value.transport_attempts == 1
    event = session.exec(select(LLMUsageEvent).where(
        LLMUsageEvent.conversation_id == "conv_empty_compression_diagnostics"
    )).one()
    assert event.metadata_["status"] == "transport_failed"
    assert event.metadata_["error_type"] == "provider_policy_block"
    assert event.metadata_["transport_attempts"] == 1
    assert event.prompt_tokens is event.completion_tokens is event.total_tokens is None
    assert event.metadata_["parse_code"] is event.metadata_["validation_code"] is None
    assert "sensitive private text" not in caplog.text + str(event.metadata_)
    assert "test-only-secret" not in caplog.text + str(event.metadata_)


@pytest.mark.asyncio
async def test_terminal_transport_failure_persists_sanitized_attempt_metadata(session, monkeypatch):
    monkeypatch.setattr(llm_client_module, "engine", session.get_bind())
    client = LLMClient(
        settings=Settings(llm_mock=False),
        overrides={
            "provider": "openai_compatible",
            "model": "provider-test",
            "base_url": "http://provider.invalid/v1",
            "api_key": "test-only-secret",
            "circuit_breaker_enabled": False,
        },
    )

    async def fail_transport(*args, **kwargs):
        raise LLMUnavailableError(
            "sanitized terminal failure",
            transport_attempts=2,
            http_status=503,
            error_type="ReadTimeout",
        )

    monkeypatch.setattr(client, "_chat_openai_compatible", fail_transport)
    with pytest.raises(LLMUnavailableError):
        await client.chat(
            [{"role": "user", "content": "do not persist this payload"}],
            conversation_id="conv_transport_failure",
        )

    event = session.exec(
        select(LLMUsageEvent).where(LLMUsageEvent.conversation_id == "conv_transport_failure")
    ).one()
    assert event.provider == "openai_compatible"
    assert event.model == "provider-test"
    assert event.prompt_tokens is None
    assert_observed_route(event.metadata_, client, inside_task=True)
    assert {k: v for k, v in event.metadata_.items() if k not in ROUTE_KEYS | {"network_attempts", "network_attempt_limit"}} == {
        "status": "transport_failed",
        "stage": "chat_generation",
        "requested_model": "provider-test",
        "reported_model_version": None,
        "token_details": {"cached_tokens": None, "reasoning_tokens": None},
        "output_budget": {"requested_max_output_tokens": None},
        "transport": {
            "policy": "generic_compatible", "output_cap_parameter": "max_tokens",
            "omitted_parameters": [], "warning_codes": [], "provider_warning_count": 0,
            "thinking_policy": {
                "requested": "provider_default", "applied_on_wire": False,
                "parameter": None, "omission_reason": "provider_default",
                "explicit_parameters_on_wire": [],
            },
        },
        "transport_attempts": 2,
        "http_status": 503,
        "error_type": "ReadTimeout",
        "response_length": 0,
        "response_sha256": None,
        "parse_code": None,
        "validation_code": None,
    }
    serialized = str(event.metadata_)
    assert "test-only-secret" not in serialized
    assert "do not persist this payload" not in serialized


@pytest.mark.asyncio
async def test_character_runtime_persists_canonical_parser_shadow_metrics(session, monkeypatch):
    monkeypatch.setattr(llm_client_module, "engine", session.get_bind())
    client = LLMClient(
        settings=Settings(llm_mock=False),
        overrides={
            "provider": "openai_compatible",
            "model": "canonical-test",
            "base_url": "http://provider.invalid/v1",
            "api_key": "test-only",
            "circuit_breaker_enabled": False,
        },
        purpose="chat_generation",
    )
    canonical_content = (
        '{"replies":[{'
        '"reply_type":"character",'
        '"character_id":"char_1",'
        '"speaker_name":"아리아",'
        '"dialogue":"canonical reply",'
        '"emotion":"",'
        '"action":"",'
        '"thought":""'
        '}]}'
    )

    async def fake_chat(*args, **kwargs):
        return LLMResponse(
            content=canonical_content,
            provider="openai_compatible",
            model="canonical-test",
            finish_reason="stop",
            generation_attempts=1,
        )

    monkeypatch.setattr(client, "_chat_openai_compatible", fake_chat)
    runtime = CharacterRuntime(llm_client=client)
    replies = await runtime.generate_replies(
        characters=[Character(id="char_1", name="아리아", persona="다정하다")],
        recent_messages=[],
        user_message="안녕",
        conversation_id="conv_parser_shadow",
    )

    assert [reply.text for reply in replies] == ["canonical reply"]
    event = session.exec(
        select(LLMUsageEvent).where(LLMUsageEvent.conversation_id == "conv_parser_shadow")
    ).one()
    assert event.metadata_["status"] == "validated"
    assert event.metadata_["canonical_parse_code"] == "accepted"
    assert event.metadata_["compatibility_parse_code"] == "accepted"
    assert event.metadata_["compatibility_used"] is False
    assert event.metadata_["canonical_match"] is True
