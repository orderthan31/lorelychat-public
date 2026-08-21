import hashlib

import pytest
from sqlmodel import select

from app.core.config import Settings
from app.db.models import Character, LLMUsageEvent
from app.engine.character_runtime import CharacterRuntime
from app.engine import llm_client as llm_client_module
from app.engine.llm_client import LLMClient, LLMResponse, LLMUnavailableError


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
    assert event.metadata_ == {
        "status": "validated",
        "finish_reason": "STOP",
        "response_length": len("structured response"),
        "response_sha256": hashlib.sha256(b"structured response").hexdigest(),
        "transport_attempts": 2,
        "generation_attempts": 2,
        "parse_code": "json_ok",
        "validation_code": "schema_ok",
    }


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
    assert event.metadata_ == {
        "status": "transport_failed",
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
