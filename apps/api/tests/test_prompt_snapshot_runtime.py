import pytest

from app.db.models import Character
from app.engine.character_runtime import CharacterRuntime
from app.engine.llm_client import LLMResponse


class FakeLLMClient:
    provider_name = "gemini"
    model_name = "gemini-3-flash-preview"
    purpose = "chat_generation"

    async def chat(self, messages, *, response_format=None, conversation_id=None):
        return LLMResponse(
            content='[{"character_id":"char_a","text":"응답","emotion":"calm","action":"","thought":""}]',
            provider=self.provider_name,
            model=self.model_name,
            prompt_tokens=10,
            completion_tokens=5,
            total_tokens=15,
            finish_reason="STOP",
        )


class AlwaysInvalidLLMClient(FakeLLMClient):
    async def chat(self, messages, *, response_format=None, conversation_id=None):
        return LLMResponse(
            content='{"replies":[{"reply_type":"character","character_id":"char_a","speaker_name":"아린","thought":"대사는 없고 속마음만 길게 나온다"}]}',
            provider=self.provider_name,
            model=self.model_name,
            prompt_tokens=10,
            completion_tokens=5,
            total_tokens=15,
            finish_reason="MAX_TOKENS",
        )


class XaiFakeLLMClient(FakeLLMClient):
    provider_name = "xai"
    model_name = "grok-4.3"


@pytest.mark.asyncio
async def test_generate_replies_exposes_prompt_snapshot_payload_to_callback():
    snapshots = []
    runtime = CharacterRuntime(llm_client=FakeLLMClient())
    character = Character(id="char_a", name="아린", persona="차분함")

    replies = await runtime.generate_replies(
        characters=[character],
        recent_messages=[],
        user_message="다음 장면",
        conversation_id="conv_prompt",
        source_message_id="msg_user_1",
        prompt_snapshot_callback=lambda snapshot: snapshots.append(snapshot),
    )

    assert replies[0].text == "응답"
    assert len(snapshots) == 1
    snapshot = snapshots[0]
    assert snapshot["conversation_id"] == "conv_prompt"
    assert snapshot["source_message_id"] == "msg_user_1"
    assert snapshot["provider"] == "gemini"
    assert snapshot["model"] == "gemini-3-flash-preview"
    assert snapshot["purpose"] == "chat_generation"
    assert snapshot["messages"][0]["role"] == "system"
    assert snapshot["messages"][1] == {"role": "user", "content": "다음 장면"}
    assert snapshot["compiled_text"] == snapshot["messages"][0]["content"]
    assert snapshot["ledger"]
    assert "prepare_messages" in snapshot["pipeline_steps"]
    assert snapshot["prompt_tokens"] == 10
    assert snapshot["completion_tokens"] == 5
    assert snapshot["total_tokens"] == 15
    assert snapshot["metadata"] == {
        "finish_reason": "STOP",
        "response_length": len('[{"character_id":"char_a","text":"응답","emotion":"calm","action":"","thought":""}]'),
        "generation_attempts": 1,
        "parse_attempts": 1,
        "parse_status": "success",
        "fallback_used": False,
    }


@pytest.mark.asyncio
async def test_character_authored_turn_snapshot_uses_assistant_role_and_neutral_control():
    snapshots = []
    runtime = CharacterRuntime(llm_client=FakeLLMClient())  # type: ignore[arg-type]
    character = Character(id="char_a", name="아린", persona="차분함")

    await runtime.generate_replies(
        characters=[character],
        recent_messages=[],
        user_message="[Character dialogue: 아린(char_a)]\n내가 먼저 제안할게.",
        source_speaker_type="character",
        conversation_id="conv_character_prompt",
        source_message_id="msg_character_1",
        prompt_snapshot_callback=lambda snapshot: snapshots.append(snapshot),
    )

    assert len(snapshots) == 1
    messages = snapshots[0]["messages"]
    assert [message["role"] for message in messages] == ["system", "assistant", "user"]
    assert messages[1]["content"] == "[Character dialogue: 아린(char_a)]\n내가 먼저 제안할게."
    assert "not by the human user" in messages[2]["content"]
    assert "human user did not speak or act in this turn" in messages[2]["content"]


@pytest.mark.asyncio
async def test_xai_prompt_snapshot_preserves_final_roleplay_rendering_contract():
    snapshots = []
    runtime = CharacterRuntime(llm_client=XaiFakeLLMClient())  # type: ignore[arg-type]
    character = Character(id="char_a", name="아린", persona="차분함")
    custom_contract = "CUSTOM XAI SNAPSHOT CONTRACT: preserve the authored roleplay prose."

    await runtime.generate_replies(
        characters=[character],
        recent_messages=[],
        user_message="다음 장면",
        conversation_id="conv_xai_prompt",
        source_message_id="msg_xai_user_1",
        prompt_settings={"xai_roleplay_rendering_contract": custom_contract},
        prompt_snapshot_callback=lambda snapshot: snapshots.append(snapshot),
    )

    assert len(snapshots) == 1
    snapshot = snapshots[0]
    assert snapshot["provider"] == "xai"
    assert snapshot["model"] == "grok-4.3"
    assert snapshot["compiled_text"] == snapshot["messages"][0]["content"]
    assert "[Grok RP rendering contract]" in snapshot["compiled_text"]
    assert snapshot["compiled_text"].rstrip().endswith(custom_contract)


@pytest.mark.asyncio
async def test_generate_replies_records_failed_raw_response_snapshot_before_raising():
    snapshots = []
    runtime = CharacterRuntime(llm_client=AlwaysInvalidLLMClient())
    character = Character(id="char_a", name="아린", persona="차분함")

    with pytest.raises(Exception):
        await runtime.generate_replies(
            characters=[character],
            recent_messages=[],
            user_message="한말씀 해줘",
            conversation_id="conv_prompt",
            source_message_id="msg_user_failed",
            prompt_snapshot_callback=lambda snapshot: snapshots.append(snapshot),
        )

    assert len(snapshots) == 1
    snapshot = snapshots[0]
    assert snapshot["source_message_id"] == "msg_user_failed"
    assert "parse_failed" in snapshot["pipeline_steps"]
    assert "속마음만" in snapshot["response_preview"]
    assert snapshot["messages"][1]["content"].startswith("한말씀 해줘")
    assert snapshot["metadata"]["finish_reason"] == "MAX_TOKENS"
    assert snapshot["metadata"]["parse_attempts"] == 2
    assert snapshot["metadata"]["parse_status"] == "failed"
