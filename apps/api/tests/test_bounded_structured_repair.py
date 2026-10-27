import json
from copy import deepcopy
from typing import Any

import httpx
import pytest
from sqlmodel import select

from app.core.config import Settings
from app.db.models import Character, ChatCommand, LLMUsageEvent, Message, MessageGenerationJob
from app.engine import llm_client as transport
from app.engine.character_runtime import CharacterRuntime, StructuredOutputTruncatedError
from app.engine.llm_client import LLMClient, LLMResponse, LLMUnavailableError
from app.services.chat_command_contract import parse_command_payload
from app.services.chat_command_service import apply_chat_command_postprocess
from app.services.conversation_service import mark_message_generation_failed, mark_message_generation_job_failed


TRUNCATED = '{"replies":[{"character_id":"char_1","dialogue":"PRIVATE-RAW'
VALID = json.dumps({"replies": [{"character_id": "char_1", "speaker_name": "Aria", "reply_type": "character", "dialogue": "An intact response.", "emotion": "neutral", "action": "", "thought": ""}]})


def provider_client(session, monkeypatch, outputs, *, purpose="chat_generation", retries=2):
    monkeypatch.setattr(transport, "engine", session.get_bind())
    client = LLMClient(Settings(llm_mock=False), purpose=purpose, overrides={
        "provider": "gemini", "model": "unchanged-model", "api_key": "PRIVATE-KEY",
        "min_output_tokens": 768, "retry_attempts": retries, "circuit_breaker_enabled": False,
    })
    requests = []
    pending = iter(outputs)

    async def fake_post(self, url, *, headers, json):
        requests.append(deepcopy(json))
        value = next(pending)
        request = httpx.Request("POST", url)
        if isinstance(value, int):
            return httpx.Response(value, request=request)
        text, finish = value
        return httpx.Response(200, request=request, json={
            "candidates": [{"finishReason": finish, "content": {"parts": [{"text": text}]}}],
            "usageMetadata": {"promptTokenCount": 17, "candidatesTokenCount": 9, "thoughtsTokenCount": 1500, "totalTokenCount": 1526},
        })

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    monkeypatch.setattr(client, "retry_delay_seconds", lambda *args, **kwargs: 0)
    return client, requests


@pytest.mark.asyncio
@pytest.mark.parametrize("recovered", [True, False])
async def test_truncation_is_bounded_atomic_and_diagnostic(session, monkeypatch, caplog, recovered):
    client, requests = provider_client(session, monkeypatch, [(TRUNCATED, "MAX_TOKENS"), (VALID, "STOP") if recovered else (TRUNCATED, "MAX_TOKENS")])
    snapshots, writes = [], []
    runtime = CharacterRuntime(llm_client=client)

    def persist(replies):
        writes.append(replies)
        return replies

    kwargs: dict[str, Any] = dict(characters=[Character(id="char_1", name="Aria", persona="Kind")], recent_messages=[], user_message="PRIVATE-PROMPT", persist_replies=persist, prompt_snapshot_callback=snapshots.append, min_bubbles=1, max_bubbles=4)
    if recovered:
        await runtime.generate_replies(**kwargs)
        assert len(writes) == 1
    else:
        with pytest.raises(StructuredOutputTruncatedError):
            await runtime.generate_replies(**kwargs)
        assert writes == []
    assert len(requests) == 2
    assert all(r["generationConfig"]["maxOutputTokens"] == 1536 for r in requests)
    assert all(r["generationConfig"]["responseSchema"]["properties"]["replies"]["maxItems"] == 4 for r in requests)
    assert client.model() == "unchanged-model" and client.provider() == "gemini"
    metadata = snapshots[0]["metadata"]
    assert metadata["semantic_attempts"] == 2 and metadata["network_attempts"] == 2
    assert metadata["fallback_used"] is False
    assert metadata["attempt_diagnostics"][0]["code"] == "structured_output_truncated"
    rows = session.exec(select(LLMUsageEvent).order_by(LLMUsageEvent.created_at)).all()
    assert [r.metadata_["status"] for r in rows] == ["parse_failed", "validated" if recovered else "parse_failed"]
    assert rows[0].metadata_["output_budget"] == {"requested_max_output_tokens": 1536, "thoughts_token_count": 1500, "cached_content_token_count": None}
    assert session.exec(select(Message)).all() == []
    safe = caplog.text + str([r.metadata_ for r in rows])
    assert all(secret not in safe for secret in ["PRIVATE-PROMPT", "PRIVATE-RAW", "PRIVATE-KEY"])


@pytest.mark.asyncio
async def test_transport_retries_share_three_attempt_cap_with_repair(session, monkeypatch):
    client, requests = provider_client(session, monkeypatch, [503, (TRUNCATED, "MAX_TOKENS"), 503, (VALID, "STOP")])
    snapshots, writes = [], []
    with pytest.raises(LLMUnavailableError):
        await CharacterRuntime(llm_client=client).generate_replies(
            characters=[Character(id="char_1", name="Aria", persona="Kind")], recent_messages=[], user_message="hello",
            persist_replies=writes.append, prompt_snapshot_callback=snapshots.append,
        )
    assert len(requests) == 3 and not writes
    assert snapshots[0]["metadata"]["network_attempts"] == 3
    assert snapshots[0]["metadata"]["semantic_attempts"] == 2
    assert snapshots[0]["metadata"]["parse_attempts"] == 1
    assert snapshots[0]["metadata"]["attempt_diagnostics"][0]["code"] == "structured_output_truncated"
    assert snapshots[0]["metadata"]["attempt_diagnostics"][1]["response_length"] == 0


def command_fixtures():
    command = ChatCommand(id="command", name="note", display_name="note", prompt="PRIVATE-PROMPT", postprocess_target="all_bubbles")
    source = Message(id="source", conversation_id="room", speaker_type="user", speaker_id="user", content="PRIVATE-PROMPT", metadata_={"command_postprocess_status": "pending"})
    targets = [Message(id=f"target-{i}", conversation_id="room", speaker_type="character", speaker_id="char_1", content=f"Original {i}", metadata_={"previous": True}) for i in range(2)]
    return command, source, targets


def command_valid():
    return json.dumps({"messages": [{"content": "Long prose\n" + "x" * 2500, "blocks": [{"type": "note", "title": "", "text": "Note"}]}, {"content": "Second target", "blocks": []}]})


@pytest.mark.asyncio
@pytest.mark.parametrize("recovered", [True, False])
async def test_command_repair_full_targets_and_usage_status(session, monkeypatch, caplog, recovered):
    monkeypatch.setattr("app.services.chat_command_service.build_command_postprocess_context", lambda *args: "")
    invalid = json.dumps({"messages": [{"content": "PRIVATE-RAW", "blocks": []}, {"content": "bad", "blocks": ["bad"]}]})
    client, requests = provider_client(session, monkeypatch, [(invalid, "STOP"), (command_valid() if recovered else invalid, "STOP")], purpose="chat_command_postprocess")
    command, source, targets = command_fixtures()
    result = await apply_chat_command_postprocess(session=session, command=command, conversation_id="room", source_message=source, generated_messages=targets, llm_client=client, commit=False)
    assert result == targets and len(requests) == 2
    assert all(r["generationConfig"]["responseSchema"]["properties"]["messages"]["minItems"] == 2 for r in requests)
    assert source.metadata_["command_postprocess_status"] == ("applied" if recovered else "failed")
    assert source.metadata_["command_postprocess_attempts"] == 2
    if recovered:
        assert targets[0].content == "Long prose\n" + "x" * 2500
        assert targets[1].content == "Second target"
    else:
        assert [m.content for m in targets] == ["Original 0", "Original 1"]
        assert all(m.metadata_["previous"] and m.metadata_["command_postprocess_error_code"] == "command_schema_invalid" for m in targets)
    assert session.exec(select(Message)).all() == []
    rows = session.exec(select(LLMUsageEvent).order_by(LLMUsageEvent.created_at)).all()
    assert [r.metadata_["status"] for r in rows] == ["parse_failed", "validated" if recovered else "parse_failed"]
    safe = caplog.text + str([r.metadata_ for r in rows])
    assert all(secret not in safe for secret in ["PRIVATE-PROMPT", "PRIVATE-RAW", "PRIVATE-KEY"])


@pytest.mark.parametrize("payload", [
    '{"messages":[{"content":"good","blocks":[]},null]}',
    '{"messages":[{"content":123,"blocks":[]}]}',
    '{"messages":[{"content":"good","blocks":[{"type":"table","headers":["a","b"],"rows":[["only one"]]}]}]}',
    '{"messages":[{"content":"good","blocks":[]}]} trailing',
    '{"messages":[{"content":"good","blocks":[],"content":"duplicate"}]}',
    '{"messages":[{"content":"good","blocks":[{"type":"unknown","text":"text"}]}]}',
    '{"messages":[{"content":"good","blocks":[{"type":"checklist","items":[{"text":"good","checked":"false"}]}]}]}',
])
def test_command_rejects_entire_malformed_contract(payload):
    assert parse_command_payload(payload, 1) is None


@pytest.mark.asyncio
async def test_command_transport_failure_sanitized_and_originals_retained(session, monkeypatch, caplog):
    monkeypatch.setattr("app.services.chat_command_service.build_command_postprocess_context", lambda *args: "")
    command, source, targets = command_fixtures()

    class BrokenClient:
        async def chat(self, *args, **kwargs):
            raise RuntimeError("PRIVATE-RAW PRIVATE-KEY PRIVATE-PROMPT")

    await apply_chat_command_postprocess(session=session, command=command, conversation_id="room", source_message=source, generated_messages=targets, llm_client=BrokenClient(), commit=False)
    assert [m.content for m in targets] == ["Original 0", "Original 1"]
    assert source.metadata_["command_postprocess_status"] == "failed"
    assert "PRIVATE-" not in caplog.text + str(source.metadata_) + str([m.metadata_ for m in targets])


def test_generation_failure_terminates_source_pending_command(session):
    _, source, _ = command_fixtures()
    session.add(source)
    session.commit()
    mark_message_generation_failed(session, source, StructuredOutputTruncatedError("Structured JSON response was truncated or incomplete"))
    session.expire_all()
    saved = session.get(Message, source.id)
    assert saved.metadata_["command_postprocess_status"] == "skipped_generation_failed"
    assert saved.metadata_["generation_error_code"] == "structured_output_truncated"


@pytest.mark.asyncio
async def test_command_overlay_repairs_missing_blocks_without_mutating_originals(session, monkeypatch):
    monkeypatch.setattr("app.services.chat_command_service.build_command_postprocess_context", lambda *args: "")
    command, source, targets = command_fixtures()
    command.postprocess_target = "last_bubble"
    invalid = json.dumps({"messages": [{"content": "Not a renderable overlay", "blocks": []}]})
    valid = json.dumps({"messages": [{"content": "", "blocks": [{"type": "note", "title": "", "text": "Overlay"}]}]})
    client, requests = provider_client(session, monkeypatch, [(invalid, "STOP"), (valid, "STOP")], purpose="chat_command_postprocess")
    result = await apply_chat_command_postprocess(session=session, command=command, conversation_id="room", source_message=source, generated_messages=targets, llm_client=client, commit=False)
    assert len(requests) == 2 and len(result) == 3
    assert [m.content for m in targets] == ["Original 0", "Original 1"]
    assert result[-1].metadata_["command_postprocess_status"] == "applied"
    assert result[-1].metadata_["command_postprocess_attempts"] == 2


@pytest.mark.asyncio
async def test_command_mixed_retry_exhaustion_never_exceeds_network_cap(session, monkeypatch):
    monkeypatch.setattr("app.services.chat_command_service.build_command_postprocess_context", lambda *args: "")
    command, source, targets = command_fixtures()
    client, requests = provider_client(session, monkeypatch, [503, ("PRIVATE-RAW", "STOP"), 503, (command_valid(), "STOP")], purpose="chat_command_postprocess")
    await apply_chat_command_postprocess(session=session, command=command, conversation_id="room", source_message=source, generated_messages=targets, llm_client=client, commit=False)
    assert len(requests) == 3
    assert source.metadata_["command_postprocess_status"] == "failed"
    assert [m.content for m in targets] == ["Original 0", "Original 1"]
    rows = session.exec(select(LLMUsageEvent).order_by(LLMUsageEvent.created_at)).all()
    assert [row.metadata_["status"] for row in rows] == ["parse_failed", "transport_failed"]


@pytest.mark.asyncio
@pytest.mark.parametrize("finish", ["MAX_TOKENS", "length"])
async def test_complete_valid_limit_response_is_not_regenerated(session, monkeypatch, finish):
    client, requests = provider_client(session, monkeypatch, [(VALID, finish)])
    replies = await CharacterRuntime(llm_client=client).generate_replies(
        characters=[Character(id="char_1", name="Aria", persona="Kind")], recent_messages=[], user_message="hello",
    )
    assert len(replies) == 1 and len(requests) == 1


def test_dispatcher_failure_also_terminates_pending_command(session):
    _, source, _ = command_fixtures()
    job = MessageGenerationJob(id="job", conversation_id="room", incoming_message_id=source.id)
    session.add(source)
    session.add(job)
    session.commit()
    mark_message_generation_job_failed(session, job, RuntimeError("worker stopped"))
    session.expire_all()
    assert session.get(Message, source.id).metadata_["command_postprocess_status"] == "skipped_generation_failed"
    assert session.get(MessageGenerationJob, job.id).status == "failed"


@pytest.mark.asyncio
async def test_spent_budget_does_not_trigger_fallback_or_circuit_failure(session, monkeypatch):
    from app.engine.llm_client import LLMAttemptBudgetError

    client, requests = provider_client(session, monkeypatch, [503, 503, (TRUNCATED, "MAX_TOKENS")], retries=3)
    circuit_failures = []
    monkeypatch.setattr(client, "record_circuit_failure", lambda: circuit_failures.append(True))

    class ForbiddenFallback:
        async def chat(self, *args, **kwargs):
            pytest.fail("budget exhaustion must not change models")

    with pytest.raises(LLMAttemptBudgetError) as captured:
        await CharacterRuntime(llm_client=client, fallback_llm_client=ForbiddenFallback()).generate_replies(
            characters=[Character(id="char_1", name="Aria", persona="Kind")], recent_messages=[], user_message="hello",
        )
    assert captured.value.transport_attempts == 0
    assert len(requests) == 3 and not circuit_failures


@pytest.mark.asyncio
async def test_gemini_reasoning_parts_are_not_visible_json(session, monkeypatch):
    client, _ = provider_client(session, monkeypatch, [])

    async def fake_post(url, *, model, payload):
        return httpx.Response(200, json={
            "candidates": [{"finishReason": "STOP", "content": {"parts": [
                {"thought": True, "text": "PRIVATE-REASONING"}, {"text": VALID},
            ]}}],
            "usageMetadata": {"thoughtsTokenCount": 13},
        })

    monkeypatch.setattr(client, "_post_gemini_with_retries", fake_post)
    response = await client.chat([{"role": "user", "content": "hello"}])
    assert response.content == VALID
    assert response.budget_diagnostics is not None
    assert response.budget_diagnostics["thoughts_token_count"] == 13
