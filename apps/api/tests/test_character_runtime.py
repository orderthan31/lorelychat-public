import json
import pytest
from datetime import datetime, timedelta, timezone

from app.db.models import Character, CharacterMemory, ConversationRelationshipState, Message, SceneState
from app.engine.character_runtime import (
    THOUGHT_MAX_CHARS,
    CharacterRuntime,
    CharacterRuntimeError,
    build_chat_replies_response_schema,
    build_parser_shadow_metrics,
    parse_canonical_multi_reply_content,
    parse_character_reply_content,
    parse_multi_reply_content,
    validate_replies,
)
from app.engine.llm_client import LLMClient, LLMResponse, LLMUnavailableError
from app.core.config import Settings
from app.engine.prompts import build_character_messages, build_multi_character_messages, build_multi_character_prompt_harness, build_scene_text
from app.services import runtime_settings_service
from app.services.conversation_service import (
    COMMON_ROOM_MEMORY_CHARACTER_ID,
    summarize_scene_memory_with_llm,
    summarize_conversation_state_with_llm,
    build_continuity_context,
    validate_conversation_compression_update,
    apply_conversation_compression_update,
    memory_already_exists,
    record_system_scene_direction,
    list_relationship_states_for_character,
    should_update_scene_orchestration_summary,
    update_scene_orchestration_summary,
)


class FakeLLMClient:
    async def chat(self, messages, response_format=None):
        return LLMResponse(content='{"dialogue":"ok","emotion":"calm","action":"nod","thought":"사용자가 괜찮아 보여서 다행이다"}')

class WrappedLLMClient:
    async def chat(self, messages, response_format=None):
        return LLMResponse(content='말로 설명하지 말고\n{"dialogue":"ok","emotion":"calm","action":"nod","thought":"조금 더 챙겨야겠다"}\n{"extra":true}')


class RetryThenValidLLMClient:
    def __init__(self):
        self.calls = []

    async def chat(self, messages, response_format=None, conversation_id=None):
        self.calls.append({"messages": messages, "response_format": response_format})
        if len(self.calls) == 1:
            return LLMResponse(content='{"replies":[{"reply_type":"character"')
        return LLMResponse(content='{"replies":[{"reply_type":"character","character_id":"char_1","dialogue":"재시도 성공"}]}')


class MissingIdentityThenValidLLMClient:
    def __init__(self):
        self.calls = []

    async def chat(self, messages, response_format=None, conversation_id=None):
        self.calls.append({"messages": messages, "response_format": response_format})
        if len(self.calls) == 1:
            return LLMResponse(
                content='{"replies":[{"reply_type":"character","dialogue":"화자 정보가 빠진 응답"}]}',
                provider="xai",
                model="grok-4.20-0309-non-reasoning",
                finish_reason="stop",
            )
        return LLMResponse(
            content='{"replies":[{"reply_type":"character","character_id":"char_1","speaker_name":"아리아","dialogue":"화자를 명시한 재시도 응답"}]}',
            provider="xai",
            model="grok-4.20-0309-non-reasoning",
            finish_reason="stop",
        )


class EmptyLLMClient:
    def __init__(self):
        self.calls = 0

    async def chat(self, messages, response_format=None, conversation_id=None):
        self.calls += 1
        return LLMResponse(content="", finish_reason="STOP")


class RepeatingThenValidLLMClient:
    def __init__(self, *, always_repeat: bool = False):
        self.calls = []
        self.always_repeat = always_repeat

    async def chat(self, messages, response_format=None, conversation_id=None):
        self.calls.append({"messages": messages, "response_format": response_format})
        if self.always_repeat or len(self.calls) == 1:
            payload = {
                "replies": [{
                    "reply_type": "character",
                    "character_id": "char_1",
                    "dialogue": "거기서 뭐 해?",
                    "thought": "처음 판단은 짧았다. " + ("건방져 보여. " * 291),
                }],
            }
            return LLMResponse(content=json.dumps(payload, ensure_ascii=False), finish_reason="STOP")
        return LLMResponse(
            content='{"replies":[{"reply_type":"character","character_id":"char_1","dialogue":"반복 없이 재시도 성공","thought":"이번에는 상황을 차분히 판단한다"}]}',
            finish_reason="STOP",
        )


class LongThoughtThenMultiReplyLLMClient:
    def __init__(self):
        self.calls = []

    async def chat(self, messages, response_format=None, conversation_id=None):
        self.calls.append({"messages": messages, "response_format": response_format})
        if len(self.calls) == 1:
            long_thought = " ".join(f"서로다른생각{index}" for index in range(40))
            return LLMResponse(content=json.dumps({
                "replies": [{
                    "reply_type": "character",
                    "character_id": "char_1",
                    "dialogue": "대사는 한 줄만 말한다.",
                    "thought": long_thought,
                }],
            }, ensure_ascii=False), finish_reason="MAX_TOKENS")
        return LLMResponse(content=json.dumps({
            "replies": [
                {"reply_type": "character", "character_id": "char_1", "dialogue": "첫 번째 대사", "thought": "짧게 판단한다."},
                {"reply_type": "character", "character_id": "char_1", "dialogue": "두 번째 대사", "thought": ""},
                {"reply_type": "character", "character_id": "char_1", "dialogue": "세 번째 대사", "thought": "다음 반응을 기다린다."},
            ],
        }, ensure_ascii=False), finish_reason="STOP")


class AlwaysMaxTokensValidJsonLLMClient:
    def __init__(self):
        self.calls = 0

    async def chat(self, messages, response_format=None, conversation_id=None):
        self.calls += 1
        return LLMResponse(content=json.dumps({
            "replies": [
                {"reply_type": "character", "character_id": "char_1", "dialogue": "첫 번째 대사"},
                {"reply_type": "character", "character_id": "char_1", "dialogue": "두 번째 대사"},
                {"reply_type": "character", "character_id": "char_1", "dialogue": "세 번째 대사"},
            ],
        }, ensure_ascii=False), finish_reason="MAX_TOKENS")


class XaiPromptCapturingLLMClient:
    def __init__(self):
        self.calls = []

    def provider(self):
        return "xai"

    async def chat(self, messages, response_format=None, conversation_id=None):
        self.calls.append({"messages": messages, "response_format": response_format})
        return LLMResponse(content=json.dumps({
            "replies": [{
                "reply_type": "character",
                "character_id": "char_1",
                "speaker_name": "아리아",
                "dialogue": "사용자, 이건 자연스럽게 이어서 말할게.",
                "emotion": "다정한 집중",
                "action": "사용자 쪽으로 몸을 돌려 눈을 맞춘다.",
                "thought": "대충 줄이지 말고 내 말투를 제대로 살리고 싶다.",
            }],
        }, ensure_ascii=False), provider="xai", model="grok-4.3", finish_reason="STOP")


class FailingCompressionClient:
    async def chat(self, messages, response_format=None, conversation_id=None):
        raise RuntimeError("gemini 503")


def test_scene_prompt_separates_opening_hook_from_world_seed():
    scene = SceneState(
        conversation_id="conv_opening",
        location="연습실 복도",
        mood="비밀스럽고 가까운 긴장감",
        world_seed="같은 아이돌 프로젝트를 준비하는 세계관.",
        opening_scene="비가 새는 복도에서 사용자가 젖은 악보를 들고 서 있다.",
        opening_line="사용자, 그거 나 때문에 망가진 거야?",
        tone_preset="slow_burn",
        relationship_archetype="tsundere_hidden_affection",
        current_conflict="서로 책임을 미루지 못하고 가까워지는 첫 장면",
    )

    prompt = build_scene_text(scene)

    assert "Fixed room world setting / origin premise: 같은 아이돌 프로젝트를 준비하는 세계관." in prompt
    assert "Opening scene hook: 비가 새는 복도에서 사용자가 젖은 악보를 들고 서 있다." in prompt
    assert "Opening line / first character beat: 사용자, 그거 나 때문에 망가진 거야?" in prompt
    assert "Room tone preset: slow_burn" in prompt
    assert "Prioritize restrained tension, delayed emotional payoff, and subtle resistance" in prompt
    assert "Relationship archetype: tsundere_hidden_affection" in prompt
    assert "visible reaction while verbally deflecting" in prompt
    assert "Blocked outcome: instant stable commitment or fully secure trust" in prompt
    assert "Do not repeat the opening line verbatim after the room is already underway" in prompt


@pytest.mark.asyncio
async def test_character_runtime_accepts_json_with_extra_text():
    character = Character(id="char_1", name="아리아", persona="다정하다")
    runtime = CharacterRuntime(llm_client=WrappedLLMClient())

    reply = await runtime.generate_reply(character=character, recent_messages=[], user_message="안녕")

    assert reply.text == "ok"
    assert reply.action == "nod"
    assert reply.thought == "조금 더 챙겨야겠다"
    assert reply.character_id == "char_1"


@pytest.mark.asyncio
async def test_generate_replies_retries_after_unusable_structured_json():
    character = Character(id="char_1", name="아리아", persona="다정하다")
    llm = RetryThenValidLLMClient()
    runtime = CharacterRuntime(llm_client=llm)  # type: ignore[arg-type]

    replies = await runtime.generate_replies(characters=[character], recent_messages=[], user_message="안녕")

    assert [reply.text for reply in replies] == ["재시도 성공"]
    assert len(llm.calls) == 2
    assert llm.calls[0]["response_format"]["schema"]["required"] == ["replies"]
    assert "System retry instruction" in llm.calls[1]["messages"][-1]["content"]


@pytest.mark.asyncio
async def test_character_authored_turn_retry_keeps_character_content_out_of_user_role():
    character = Character(id="char_1", name="아리아", persona="다정하다")
    llm = RetryThenValidLLMClient()
    runtime = CharacterRuntime(llm_client=llm)  # type: ignore[arg-type]
    character_turn = "[Character dialogue: 아리아(char_1)]\n내가 먼저 말했어."

    replies = await runtime.generate_replies(
        characters=[character],
        recent_messages=[],
        user_message=character_turn,
        source_speaker_type="character",
    )

    assert [reply.text for reply in replies] == ["재시도 성공"]
    assert len(llm.calls) == 2
    for call in llm.calls:
        assert [message["role"] for message in call["messages"]] == ["system", "assistant", "user"]
        assert call["messages"][1]["content"] == character_turn
        assert character_turn not in call["messages"][2]["content"]
    assert "System retry instruction" in llm.calls[1]["messages"][2]["content"]


@pytest.mark.asyncio
async def test_generate_replies_retry_requires_exact_speaker_identity_after_grok_omission():
    characters = [
        Character(id="char_1", name="아리아", persona="다정하다"),
        Character(id="char_2", name="루나", persona="밝다"),
    ]
    llm = MissingIdentityThenValidLLMClient()
    runtime = CharacterRuntime(llm_client=llm)  # type: ignore[arg-type]

    replies = await runtime.generate_replies(
        characters=characters,
        recent_messages=[],
        user_message="다음 장면",
    )

    assert [reply.text for reply in replies] == ["화자를 명시한 재시도 응답"]
    assert len(llm.calls) == 2
    retry_instruction = llm.calls[1]["messages"][-1]["content"]
    assert "Every character reply must include the exact character_id and speaker_name" in retry_instruction


@pytest.mark.asyncio
async def test_generate_replies_fails_after_repeated_empty_structured_responses():
    character = Character(id="char_1", name="아리아", persona="다정하다")
    llm = EmptyLLMClient()
    runtime = CharacterRuntime(llm_client=llm)  # type: ignore[arg-type]

    with pytest.raises(CharacterRuntimeError, match="empty"):
        await runtime.generate_replies(characters=[character], recent_messages=[], user_message="안녕")

    assert llm.calls == 2


@pytest.mark.asyncio
async def test_generate_replies_retries_after_degenerate_thought_loop():
    character = Character(id="char_1", name="아리아", persona="다정하다")
    llm = RepeatingThenValidLLMClient()
    runtime = CharacterRuntime(llm_client=llm)  # type: ignore[arg-type]

    replies = await runtime.generate_replies(characters=[character], recent_messages=[], user_message="안녕")

    assert [reply.text for reply in replies] == ["반복 없이 재시도 성공"]
    assert len(llm.calls) == 2
    assert "System retry instruction" in llm.calls[1]["messages"][-1]["content"]


@pytest.mark.asyncio
async def test_generate_replies_fails_after_two_degenerate_thought_loops():
    character = Character(id="char_1", name="아리아", persona="다정하다")
    llm = RepeatingThenValidLLMClient(always_repeat=True)
    runtime = CharacterRuntime(llm_client=llm)  # type: ignore[arg-type]
    persisted = []
    snapshots = []

    async def persist_replies(replies):
        persisted.append(replies)

    with pytest.raises(CharacterRuntimeError, match="repetition loop"):
        await runtime.generate_replies(
            characters=[character],
            recent_messages=[],
            user_message="안녕",
            persist_replies=persist_replies,
            source_message_id="msg_repeat_source",
            prompt_snapshot_callback=lambda snapshot: snapshots.append(snapshot),
        )

    assert len(llm.calls) == 2
    assert persisted == []
    assert len(snapshots) == 1
    assert snapshots[0]["metadata"]["parse_status"] == "failed"
    assert snapshots[0]["metadata"]["parse_attempts"] == 2


def test_chat_reply_schema_constrains_bubble_count_and_thought_during_generation():
    schema = build_chat_replies_response_schema(min_bubbles=3, max_bubbles=6)

    replies_schema = schema["properties"]["replies"]
    properties = replies_schema["items"]["properties"]
    thought_schema = properties["thought"]
    assert replies_schema["minItems"] == 3
    assert replies_schema["maxItems"] == 6
    assert replies_schema["items"]["required"] == [
        "reply_type",
        "character_id",
        "speaker_name",
        "dialogue",
        "emotion",
        "action",
        "thought",
    ]
    assert schema["additionalProperties"] is False
    assert replies_schema["items"]["additionalProperties"] is False
    assert "text" not in properties
    assert thought_schema["maxLength"] == THOUGHT_MAX_CHARS
    assert THOUGHT_MAX_CHARS == 120
    assert "Private in-character inner voice" in thought_schema["description"]
    assert "Do not repeat or summarize" in thought_schema["description"]
    assert "natural Korean conversation" in properties["dialogue"]["description"]
    assert "concrete visible movement" in properties["action"]["description"]
    assert "Optional short" not in properties["action"]["description"]
    assert "Omit" not in thought_schema["description"]
    for field_name in ("dialogue", "action", "thought"):
        assert "semantic role comes from this JSON field" in properties[field_name]["description"]
        assert "Markdown" in properties[field_name]["description"]


def test_canonical_parser_accepts_only_the_closed_reply_envelope():
    content = json.dumps({
        "replies": [{
            "reply_type": "character",
            "character_id": "char_1",
            "speaker_name": "아리아",
            "dialogue": "응, 이렇게 받으면 돼.",
            "emotion": "차분함",
            "action": "고개를 끄덕인다",
            "thought": "",
        }],
    }, ensure_ascii=False)

    replies = parse_canonical_multi_reply_content(content)

    assert replies[0]["dialogue"] == "응, 이렇게 받으면 돼."
    assert build_parser_shadow_metrics(content, compatibility_replies=replies) == {
        "canonical_parse_code": "accepted",
        "compatibility_parse_code": "accepted",
        "compatibility_used": False,
        "canonical_match": True,
    }


def test_parser_shadow_marks_legacy_nested_envelope_as_compatibility_used():
    content = json.dumps([{
        "character": "아리아 (char_1)",
        "replies": [{"dialogue": "legacy envelope", "action": "", "thought": ""}],
    }], ensure_ascii=False)
    compatibility_replies = parse_multi_reply_content(content)

    metrics = build_parser_shadow_metrics(content, compatibility_replies=compatibility_replies)

    assert metrics == {
        "canonical_parse_code": "top_level_not_object",
        "compatibility_parse_code": "accepted",
        "compatibility_used": True,
        "canonical_match": False,
    }


def test_canonical_parser_rejects_legacy_text_alias_and_extra_fields():
    content = json.dumps({
        "replies": [{
            "reply_type": "character",
            "character_id": "char_1",
            "speaker_name": "아리아",
            "text": "legacy alias",
            "emotion": "",
            "action": "",
            "thought": "",
            "metadata": {},
        }],
    }, ensure_ascii=False)

    with pytest.raises(CharacterRuntimeError, match="Canonical reply fields"):
        parse_canonical_multi_reply_content(content)



def test_long_length_prompt_uses_clean_dialogue_density_contract():
    character = Character(id="char_1", name="아리아", persona="다정하다")

    harness = build_multi_character_prompt_harness(
        characters=[character],
        recent_messages=[],
        user_message="다음 장면",
        min_bubbles=3,
        max_bubbles=6,
        min_output_tokens=1280,
    )

    assert THOUGHT_MAX_CHARS == 120
    assert "Keep total replies between 3 and 6" in harness.compiled_text
    assert "Length preset: long. Return 3-6 distinct reply bubbles" in harness.compiled_text
    assert "2-4 natural sentences" in harness.compiled_text
    assert "at most 120 characters" in harness.compiled_text
    assert "visible action is at most 80 characters" not in harness.compiled_text
    assert "Keep dialogue, action, and thought in separate JSON fields" in harness.compiled_text
    assert "Never use Markdown markers to encode whether text is dialogue, action, or thought" in harness.compiled_text
    assert "Available output tokens are a ceiling" not in harness.compiled_text
    assert "Target around" not in harness.compiled_text
    assert "Dialogue should be short" not in harness.compiled_text
    assert '\"reply_type\":\"character|storytelling\"' in harness.compiled_text
    assert "storytelling uses empty ID/name" in harness.compiled_text


def test_validate_replies_rejects_long_thought_without_truncating_it():
    long_thought = " ".join(f"서로다른생각{index}" for index in range(40))
    raw = {
        "reply_type": "character",
        "character_id": "char_1",
        "dialogue": "정상 대사",
        "thought": long_thought,
    }

    with pytest.raises(CharacterRuntimeError, match="thought exceeded"):
        validate_replies([raw], default_character_id="char_1", allowed_character_ids={"char_1"})

    assert raw["thought"] == long_thought


def test_validate_replies_rejects_over_maximum_instead_of_slicing_prefix():
    raw = [
        {"reply_type": "character", "character_id": "char_1", "dialogue": f"대사 {index}"}
        for index in range(7)
    ]

    with pytest.raises(CharacterRuntimeError, match="7 reply bubbles"):
        validate_replies(
            raw,
            default_character_id="char_1",
            allowed_character_ids={"char_1"},
            min_bubbles=3,
            max_bubbles=6,
        )


def test_validate_replies_rejects_whole_response_when_one_bubble_is_invalid():
    raw = [
        {"reply_type": "character", "character_id": "char_1", "dialogue": "정상 대사 1"},
        {"reply_type": "character", "character_id": "char_1", "dialogue": ""},
        {"reply_type": "character", "character_id": "char_1", "dialogue": "정상 대사 2"},
    ]

    with pytest.raises(CharacterRuntimeError, match="reply 2"):
        validate_replies(
            raw,
            default_character_id="char_1",
            allowed_character_ids={"char_1"},
            min_bubbles=2,
            max_bubbles=3,
        )


def test_validate_replies_rejects_all_storytelling_output():
    raw = [
        {"reply_type": "storytelling", "dialogue": "장면 설명 하나"},
        {"reply_type": "storytelling", "dialogue": "장면 설명 둘"},
    ]

    with pytest.raises(CharacterRuntimeError, match="at least one character dialogue"):
        validate_replies(raw, min_bubbles=2, max_bubbles=3)


@pytest.mark.asyncio
async def test_generate_replies_accepts_complete_valid_json_at_max_tokens_without_retry():
    character = Character(id="char_1", name="아리아", persona="다정하다")
    llm = AlwaysMaxTokensValidJsonLLMClient()
    runtime = CharacterRuntime(llm_client=llm)  # type: ignore[arg-type]
    persisted = []

    async def persist_replies(replies):
        persisted.append(replies)
        return replies

    replies = await runtime.generate_replies(
        characters=[character],
        recent_messages=[],
        user_message="다음 장면",
        min_bubbles=3,
        max_bubbles=6,
        persist_replies=persist_replies,
    )

    assert llm.calls == 1
    assert [reply.text for reply in replies] == ["첫 번째 대사", "두 번째 대사", "세 번째 대사"]
    assert len(persisted) == 1


@pytest.mark.asyncio
async def test_generate_replies_retries_whole_response_after_one_bubble_long_thought():
    character = Character(id="char_1", name="아리아", persona="다정하다")
    llm = LongThoughtThenMultiReplyLLMClient()
    runtime = CharacterRuntime(llm_client=llm)  # type: ignore[arg-type]

    replies = await runtime.generate_replies(
        characters=[character],
        recent_messages=[],
        user_message="다음 장면",
        min_bubbles=3,
        max_bubbles=6,
        min_output_tokens=1280,
    )

    assert [reply.text for reply in replies] == ["첫 번째 대사", "두 번째 대사", "세 번째 대사"]
    assert len(llm.calls) == 2
    assert llm.calls[0]["response_format"]["schema"]["properties"]["replies"]["minItems"] == 3
    retry_instruction = llm.calls[1]["messages"][-1]["content"]
    assert f"at most {THOUGHT_MAX_CHARS} characters" in retry_instruction
    assert "exactly 3 reply bubbles" in retry_instruction
    assert "Preserve the intended roleplay wording and emotional specificity" in retry_instruction
    assert "Keep every field concise" not in retry_instruction
    assert "omit thought instead of using it to fill length" not in retry_instruction
    retry_schema = llm.calls[1]["response_format"]["schema"]["properties"]["replies"]
    assert retry_schema["minItems"] == 3
    assert retry_schema["maxItems"] == 3


@pytest.mark.asyncio
async def test_generate_replies_routes_xai_roleplay_contract_into_final_system_section():
    character = Character(id="char_1", name="아리아", persona="다정하다")
    llm = XaiPromptCapturingLLMClient()
    runtime = CharacterRuntime(llm_client=llm)  # type: ignore[arg-type]
    custom_contract = "CUSTOM XAI CONTRACT: JSON is transport only; preserve the authored prose."

    replies = await runtime.generate_replies(
        characters=[character],
        recent_messages=[],
        user_message="다음 장면",
        prompt_settings={"xai_roleplay_rendering_contract": custom_contract},
    )

    assert [reply.text for reply in replies] == ["사용자, 이건 자연스럽게 이어서 말할게."]
    system_prompt = llm.calls[0]["messages"][0]["content"]
    assert "[Grok RP rendering contract]" in system_prompt
    assert system_prompt.rstrip().endswith(custom_contract)


@pytest.mark.parametrize("field", ["dialogue", "action", "thought"])
def test_validate_replies_rejects_degenerate_repetition_in_all_text_fields(field):
    raw = {
        "reply_type": "character",
        "character_id": "char_1",
        "dialogue": "정상 대사",
        "action": "고개를 든다",
        "thought": "상황을 살핀다",
    }
    raw[field] = "건방져 보여. " * 40

    with pytest.raises(CharacterRuntimeError, match=f"{field} repetition loop"):
        validate_replies([raw], default_character_id="char_1", allowed_character_ids={"char_1"})


def test_validate_replies_allows_short_emphatic_repetition():
    replies = validate_replies(
        [{
            "reply_type": "character",
            "character_id": "char_1",
            "dialogue": "안 돼. 안 돼. 안 돼. 이번엔 내가 직접 확인할게.",
            "thought": "침착하자. 침착하자. 침착하자. 아직 판단할 수 있어.",
        }],
        default_character_id="char_1",
        allowed_character_ids={"char_1"},
    )

    assert len(replies) == 1


@pytest.mark.parametrize(
    "loop_value",
    [
        "너를 이겨야 해 " * 30,
        "아" * 100,
    ],
)
def test_validate_replies_rejects_non_sentence_repetition_loops(loop_value):
    with pytest.raises(CharacterRuntimeError, match="dialogue repetition loop"):
        validate_replies(
            [{
                "reply_type": "character",
                "character_id": "char_1",
                "dialogue": loop_value,
            }],
            default_character_id="char_1",
            allowed_character_ids={"char_1"},
        )


def test_validate_replies_allows_long_non_repetitive_dialogue():
    dialogue = " ".join(
        f"장면{index}에서 인물{index}가 선택{index}을 하고 흐름{index}으로 이동한다."
        for index in range(30)
    )

    replies = validate_replies(
        [{
            "reply_type": "character",
            "character_id": "char_1",
            "dialogue": dialogue,
        }],
        default_character_id="char_1",
        allowed_character_ids={"char_1"},
    )

    assert replies[0].text == dialogue


def test_parse_character_reply_prefers_dialogue_object_from_concatenated_json():
    parsed = parse_character_reply_content('{"emotion":"calm"}\n{"dialogue":"ok","emotion":"warm","action":"smile","thought":"속으로 반가워한다"}')

    assert parsed["dialogue"] == "ok"
    assert parsed["thought"] == "속으로 반가워한다"


def test_parse_character_reply_repairs_gemma_malformed_json_with_trailing_empty_key():
    content = '{"character_id":"char_1","dialogue":"대사만 보여야 해","emotion":"경멸","action":"팔짱을 낀다","thought":"속으로 계산한다",""}'

    parsed = parse_character_reply_content(content)

    assert parsed["dialogue"] == "대사만 보여야 해"
    assert parsed["action"] == "팔짱을 낀다"
    assert parsed["thought"] == "속으로 계산한다"
    assert parsed["emotion"] == "경멸"


def test_character_reply_preserves_inline_markdown_inside_each_semantic_json_field():
    content = json.dumps({
        "reply_type": "character",
        "character_id": "char_1",
        "speaker_name": "아리아",
        "dialogue": "그건 **절대로** 양보 못 해.",
        "action": "손끝으로 *천천히* 책상을 두드린다.",
        "thought": "`지금` 말해야 해.",
    }, ensure_ascii=False)

    parsed = parse_character_reply_content(content)
    replies = validate_replies(
        [parsed],
        default_character_id="char_1",
        allowed_character_ids={"char_1"},
        character_id_by_name={"아리아": "char_1"},
    )

    assert replies[0].text == "그건 **절대로** 양보 못 해."
    assert replies[0].action == "손끝으로 *천천히* 책상을 두드린다."
    assert replies[0].thought == "`지금` 말해야 해."


def test_parse_multi_reply_rejects_truncated_gemini_replies_prefix():
    content = '''{
  "replies": [
    {
      "reply_type": "character",
      "character_id": "char_d02018a9a29c",
      "speaker_name": "서모아",
      "dialogue": "언니가 요즘 나 피한다고 해도, 그런 상상은 좀 너무하잖아. 그래도 뭐... 합동 무대 하면 재미있긴 하겠다'''

    with pytest.raises(CharacterRuntimeError, match="truncated or incomplete"):
        parse_multi_reply_content(
            content,
            default_character_id="char_d02018a9a29c",
            allowed_character_ids={"char_d02018a9a29c"},
        )


def test_parse_multi_reply_flattens_gemini_character_replies_envelope():
    content = '''[
      {
        "character": "아리아 (char_4cb135d5d008)",
        "replies": [
          {
            "thought": "떨린다.",
            "action": "영상을 보낸다.",
            "dialogue": "사용자님! 약속했던 영상 보내드려요."
          }
        ]
      }
    ]'''

    raw_replies = parse_multi_reply_content(
        content,
        default_character_id="char_4cb135d5d008",
        allowed_character_ids={"char_4cb135d5d008"},
    )
    replies = validate_replies(
        raw_replies,
        default_character_id="char_4cb135d5d008",
        allowed_character_ids={"char_4cb135d5d008"},
        character_id_by_name={"아리아": "char_4cb135d5d008"},
    )

    assert len(replies) == 1
    assert replies[0].character_id == "char_4cb135d5d008"
    assert replies[0].text == "사용자님! 약속했던 영상 보내드려요."
    assert replies[0].action == "영상을 보낸다."
    assert replies[0].thought == "떨린다."


def test_multi_character_replies_without_identity_do_not_fallback_to_first_character():
    raw_replies = [
        {"reply_type": "character", "dialogue": "나는 루나처럼 보이면 안 돼", "thought": "!방송마지막"},
        {"reply_type": "character", "dialogue": "나도 다른 사람인데 아이디가 없다", "thought": "!방송마지막"},
    ]

    with pytest.raises(CharacterRuntimeError, match="omitted speaker identity"):
        validate_replies(
            raw_replies,
            default_character_id="char_luna",
            allowed_character_ids={"char_luna", "char_alina"},
            character_id_by_name={"고루나": "char_luna", "베라": "char_alina"},
        )


def test_multi_character_replies_resolve_speaker_name_and_strip_command_literals():
    raw_replies = [
        {
            "reply_type": "character",
            "speaker_name": "베라",
            "dialogue": "나는 베라야. !방송마지막",
            "thought": "흔들리지 않는다. !방송마지막",
        }
    ]

    replies = validate_replies(
        raw_replies,
        default_character_id="char_luna",
        allowed_character_ids={"char_luna", "char_alina"},
        character_id_by_name={"고루나": "char_luna", "베라": "char_alina"},
    )

    assert len(replies) == 1
    assert replies[0].character_id == "char_alina"
    assert replies[0].text == "나는 베라야."
    assert replies[0].thought == "흔들리지 않는다."


class PlainTextLLMClient:
    def __init__(self):
        self.calls = 0

    async def chat(self, messages, response_format=None):
        self.calls += 1
        return LLMResponse(content='그냥 일반 문장으로 답했어.')


@pytest.mark.asyncio
async def test_character_runtime_retries_then_rejects_plain_text_when_json_is_missing():
    character = Character(id="char_1", name="아리아", persona="다정하다")
    llm = PlainTextLLMClient()
    runtime = CharacterRuntime(llm_client=llm)  # type: ignore[arg-type]

    with pytest.raises(CharacterRuntimeError, match="0 reply bubbles"):
        await runtime.generate_reply(character=character, recent_messages=[], user_message="안녕")

    assert llm.calls == 2


class TruncatedThenValidLLMClient:
    def __init__(self):
        self.calls = 0

    async def chat(self, messages, response_format=None, conversation_id=None):
        self.calls += 1
        if self.calls == 1:
            return LLMResponse(
                content='{"replies":[{"character_id":"char_1","dialogue":"잘린 응답',
                provider="gemini",
                model="primary-model",
                finish_reason="STOP",
            )
        return LLMResponse(
            content='{"replies":[{"character_id":"char_1","dialogue":"완결된 보정 응답"}]}',
            provider="gemini",
            model="primary-model",
            finish_reason="STOP",
        )


@pytest.mark.asyncio
async def test_character_runtime_repairs_truncated_json_only_by_regenerating():
    character = Character(id="char_1", name="아리아", persona="다정하다")
    llm = TruncatedThenValidLLMClient()
    runtime = CharacterRuntime(llm_client=llm)  # type: ignore[arg-type]

    replies = await runtime.generate_replies(
        characters=[character],
        recent_messages=[],
        user_message="안녕",
    )

    assert [reply.text for reply in replies] == ["완결된 보정 응답"]
    assert llm.calls == 2


class UnavailableChatClient:
    def __init__(self):
        self.calls = 0

    async def chat(self, messages, response_format=None, conversation_id=None):
        self.calls += 1
        raise LLMUnavailableError("primary unavailable")


class SuccessfulFallbackChatClient:
    def __init__(self):
        self.calls = 0

    async def chat(self, messages, response_format=None, conversation_id=None):
        self.calls += 1
        return LLMResponse(
            content='{"replies":[{"character_id":"char_1","dialogue":"fallback 답변"}]}',
            provider="gemini",
            model="fallback-model",
            finish_reason="STOP",
        )


@pytest.mark.asyncio
async def test_character_runtime_uses_explicit_fallback_only_when_primary_is_unavailable():
    character = Character(id="char_1", name="아리아", persona="다정하다")
    primary = UnavailableChatClient()
    fallback = SuccessfulFallbackChatClient()
    runtime = CharacterRuntime(
        llm_client=primary,  # type: ignore[arg-type]
        fallback_llm_client=fallback,  # type: ignore[arg-type]
    )

    replies = await runtime.generate_replies(
        characters=[character],
        recent_messages=[],
        user_message="안녕",
    )

    assert replies[0].text == "fallback 답변"
    assert primary.calls == 1
    assert fallback.calls == 1


def test_parse_multi_reply_rejects_mixed_non_object_items_atomically():
    content = (
        '{"replies":['
        '{"character_id":"char_1","dialogue":"첫 버블"},'
        '"MALFORMED",'
        '{"character_id":"char_1","dialogue":"두 번째 버블"}'
        ']}'
    )

    with pytest.raises(CharacterRuntimeError, match="non-object reply item"):
        parse_multi_reply_content(content, default_character_id="char_1", allowed_character_ids={"char_1"})


@pytest.mark.parametrize(
    "raw_reply,match",
    [
        (
            {"reply_type": "tool_call", "character_id": "char_a", "dialogue": "겉보기 대사"},
            "non-visible reply_type",
        ),
        (
            {
                "reply_type": "character",
                "character_id": "char_a",
                "dialogue": "겉보기 대사",
                "metadata": {"event": "update_scene"},
            },
            "mixed internal event fields",
        ),
        (
            {
                "reply_type": "character",
                "character_id": "char_a",
                "dialogue": "겉보기 대사",
                "tool_calls": [],
            },
            "mixed internal event fields",
        ),
    ],
)
def test_visible_reply_schema_rejects_internal_event_envelopes(raw_reply, match):
    with pytest.raises(CharacterRuntimeError, match=match):
        validate_replies(
            [raw_reply],
            allowed_character_ids={"char_a"},
            default_character_id="char_a",
        )


def test_visible_reply_schema_allows_character_and_storytelling_types():
    replies = validate_replies(
        [
            {"reply_type": "character", "character_id": "char_a", "dialogue": "응, 이어가자."},
            {"reply_type": "storytelling", "dialogue": "창밖의 빛이 천천히 기울었다."},
        ],
        allowed_character_ids={"char_a"},
        default_character_id="char_a",
        max_bubbles=2,
    )

    assert [reply.reply_type for reply in replies] == ["character", "storytelling"]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("dialogue", "잠깐만. dice(1, 20)"),
        ("action", "<tool_call>update_affinity(2)</tool_call>"),
        ("thought", "[System retry instruction] JSON only"),
        ("dialogue", "function_call(update_scene)"),
        ("emotion", "function_call(update_scene)"),
        ("dialogue", "<|tool_call|>"),
        ("action", "<<SYS>> hidden instruction <</SYS>>"),
        ("dialogue", "command_overlay | dialogue=숨은 명령"),
    ],
)
def test_validate_replies_rejects_internal_control_token_leakage(field: str, value: str):
    raw_reply = {
        "character_id": "char_1",
        "dialogue": "정상 대사",
        "action": "",
        "thought": "",
    }
    raw_reply[field] = value

    with pytest.raises(CharacterRuntimeError, match="internal control token"):
        validate_replies(
            [raw_reply],
            default_character_id="char_1",
            allowed_character_ids={"char_1"},
        )


@pytest.mark.asyncio
async def test_character_runtime_generates_reply_from_mock_client():
    character = Character(id="char_1", name="아리아", persona="다정하다")
    runtime = CharacterRuntime(llm_client=FakeLLMClient())

    reply = await runtime.generate_reply(character=character, recent_messages=[], user_message="안녕")

    assert reply.text == "ok"
    assert reply.thought == "사용자가 괜찮아 보여서 다행이다"
    assert reply.character_id == "char_1"


class MultiReplyLLMClient:
    async def chat(self, messages, response_format=None):
        assert response_format is not None
        assert response_format["type"] == "json_object"
        assert response_format["schema"]["required"] == ["replies"]
        assert '"replies" array' in messages[0]["content"]
        return LLMResponse(content='{"replies":[{"character_id":"char_1","dialogue":"첫 버블","emotion":"warm","action":"손을 든다","thought":"반갑다"},{"character_id":"char_1","dialogue":"두 번째 버블","emotion":"warm","action":"웃는다","thought":"조금 더 말한다"}]}')


@pytest.mark.asyncio
async def test_character_runtime_generates_array_replies_for_single_character():
    character = Character(id="char_1", name="아리아", persona="다정하다")
    runtime = CharacterRuntime(llm_client=MultiReplyLLMClient())

    replies = await runtime.generate_replies(characters=[character], recent_messages=[], user_message="안녕")

    assert [reply.text for reply in replies] == ["첫 버블", "두 번째 버블"]
    assert all(reply.character_id == "char_1" for reply in replies)


class MultiRoomLLMClient:
    async def chat(self, messages, response_format=None):
        system = messages[0]["content"]
        assert "char_aria" in system
        assert "char_luna" in system
        return LLMResponse(content='{"replies":[{"character_id":"char_aria","dialogue":"루나아, 잠깐만.","emotion":"calm","action":"고개를 돌린다","thought":"흐름을 잡아야겠다"},{"character_id":"char_luna","dialogue":"응 언니, 나 들을게!","emotion":"bright","action":"몸을 앞으로 기울인다","thought":"재밌어졌다"}]}')


class MultiRoomNameOnlyLLMClient:
    async def chat(self, messages, response_format=None):
        system = messages[0]["content"]
        assert "Character ID map" in system
        assert "speaker_name" in system
        assert "아리아=char_aria" in system
        assert "루나=char_luna" in system
        return LLMResponse(content='{"replies":[{"speaker":"아리아","dialogue":"루나아, 잠깐만.","emotion":"calm","action":"고개를 돌린다","thought":"흐름을 잡아야겠다"},{"character_name":"루나","dialogue":"응 언니, 나 들을게!","emotion":"bright","action":"몸을 앞으로 기울인다","thought":"재밌어졌다"}]}')


class MultiRoomConflictingSpeakerLLMClient:
    async def chat(self, messages, response_format=None):
        return LLMResponse(content='{"replies":[{"character_id":"char_aria","speaker_name":"루나","dialogue":"언니, 나 들을게!","emotion":"bright"}]}')


@pytest.mark.asyncio
async def test_character_runtime_generates_multi_character_replies_in_one_call():
    aria = Character(id="char_aria", name="아리아", persona="차분하다")
    luna = Character(id="char_luna", name="루나", persona="밝다")
    runtime = CharacterRuntime(llm_client=MultiRoomLLMClient())

    replies = await runtime.generate_replies(
        characters=[aria, luna],
        recent_messages=[],
        user_message="*방에 들어온다* 둘이 얘기해봐",
        conversation_mode="character_character",
    )

    assert [reply.character_id for reply in replies] == ["char_aria", "char_luna"]
    assert replies[0].text == "루나아, 잠깐만."
    assert replies[1].text == "응 언니, 나 들을게!"


@pytest.mark.asyncio
async def test_character_runtime_maps_name_only_multi_room_replies_to_character_ids():
    aria = Character(id="char_aria", name="아리아", persona="차분하다")
    luna = Character(id="char_luna", name="루나", persona="밝다")
    runtime = CharacterRuntime(llm_client=MultiRoomNameOnlyLLMClient())

    replies = await runtime.generate_replies(
        characters=[aria, luna],
        recent_messages=[],
        user_message="둘 다 반응해줘",
        conversation_mode="character_character",
    )

    assert [reply.character_id for reply in replies] == ["char_aria", "char_luna"]
    assert [reply.text for reply in replies] == ["루나아, 잠깐만.", "응 언니, 나 들을게!"]


@pytest.mark.asyncio
async def test_character_runtime_speaker_name_overrides_conflicting_character_id():
    aria = Character(id="char_aria", name="아리아", persona="차분하다")
    luna = Character(id="char_luna", name="루나", persona="밝다")
    runtime = CharacterRuntime(llm_client=MultiRoomConflictingSpeakerLLMClient())

    replies = await runtime.generate_replies(
        characters=[aria, luna],
        recent_messages=[],
        user_message="둘 다 반응해줘",
        conversation_mode="character_character",
    )

    assert [reply.character_id for reply in replies] == ["char_luna"]
    assert replies[0].text == "언니, 나 들을게!"


class MultiRoomShortAliasSpeakerLLMClient:
    async def chat(self, messages, response_format=None):
        return LLMResponse(content='{"replies":[{"character_id":"char_mina","speaker_name":"카이","dialogue":"이건 완전 하드 모드잖아!","emotion":"playful"},{"character_id":"char_mina","speaker_name":"에코","dialogue":"후후, 흥미로운 흐름이네요.","emotion":"calm"},{"character_id":"char_mina","speaker_name":"리오","dialogue":"분석적으로 보면 변수가 많아요.","emotion":"cool"}]}')


@pytest.mark.asyncio
async def test_character_runtime_short_speaker_alias_overrides_collapsed_first_character_id():
    mina = Character(id="char_mina", name="아리아", persona="아이돌 연습생")
    yeonwoo = Character(id="char_yeonwoo", name="김카이", persona="게임 스트리머")
    reika = Character(id="char_reika", name="카미야 에코", persona="긴자 접객 에이스")
    yiju = Character(id="char_yiju", name="박리오", persona="분석적인 모델")
    runtime = CharacterRuntime(llm_client=MultiRoomShortAliasSpeakerLLMClient())

    replies = await runtime.generate_replies(
        characters=[mina, yeonwoo, reika, yiju],
        recent_messages=[],
        user_message="세 명이 반응해줘",
        conversation_mode="character_character",
    )

    assert [reply.character_id for reply in replies] == ["char_yeonwoo", "char_reika", "char_yiju"]


@pytest.mark.asyncio
async def test_character_runtime_persist_callback_receives_validated_replies_from_langgraph():
    aria = Character(id="char_aria", name="아리아", persona="차분하다")
    luna = Character(id="char_luna", name="루나", persona="밝다")
    runtime = CharacterRuntime(llm_client=MultiRoomLLMClient())
    persisted = []

    async def persist_replies(replies):
        persisted.extend(replies)
        return [f"message:{reply.character_id}:{reply.text}" for reply in replies]

    result = await runtime.generate_replies(
        characters=[aria, luna],
        recent_messages=[],
        user_message="*방에 들어온다* 둘이 얘기해봐",
        conversation_mode="character_character",
        persist_replies=persist_replies,
    )

    assert result == ["message:char_aria:루나아, 잠깐만.", "message:char_luna:응 언니, 나 들을게!"]
    assert [reply.character_id for reply in persisted] == ["char_aria", "char_luna"]
    assert [reply.text for reply in persisted] == ["루나아, 잠깐만.", "응 언니, 나 들을게!"]


class EmptyThenSummaryLLMClient:
    def __init__(self):
        self.calls = []

    async def chat(self, messages, response_format=None, conversation_id=None):
        self.calls.append(messages)
        if len(self.calls) == 1:
            return LLMResponse(content="", finish_reason="STOP")
        return LLMResponse(content="""[Rolling Story Arc]
- char_1과 char_2가 공개 대화 주도권을 두고 견제했고, 서로 물러서지 않기로 했다.""", finish_reason="STOP")


class SummaryLLMClient:
    async def chat(self, messages, response_format=None):
        assert "incrementally compress character-chat transcript" in messages[0]["content"]
        assert "Chronological Messages To Fold" in messages[1]["content"]
        assert "Room-specific memory/relationship update contract" not in messages[1]["content"]
        assert "Never restate fixed world setting" in messages[0]["content"]
        return LLMResponse(content="""[Rolling Story Arc]
- char_1이 차분하게 방어하고 char_2가 장난스럽게 압박하며 경쟁 구도가 형성됐다.""")


@pytest.mark.asyncio
async def test_scene_memory_summarizer_uses_llm_compaction_format():
    scene = SceneState(conversation_id="conv_1", mood="긴장", current_conflict="주도권 다툼", compression_focus="관계 변화와 미해결 떡밥을 우선 보존")
    recent = [
        Message(id="m1", conversation_id="conv_1", speaker_type="character", speaker_id="char_1", content="조심스럽게 반응한다", emotion="calm"),
        Message(id="m2", conversation_id="conv_1", speaker_type="character", speaker_id="char_2", content="장난스럽게 압박한다", emotion="playful"),
    ]

    summary = await summarize_scene_memory_with_llm(scene, recent, llm_client=SummaryLLMClient())

    assert summary
    assert summary.startswith("[Rolling Story Arc]")
    assert "[Current Scene State]" not in summary
    assert "[Recent Events]" not in summary
    assert "[Characters]" not in summary
    assert "Next continuity anchors" not in summary
    assert "Recent flow" not in summary
    assert len(summary) <= 6000


@pytest.mark.asyncio
async def test_scene_memory_summarizer_retries_once_after_empty_response():
    scene = SceneState(conversation_id="conv_retry_summary")
    recent = [
        Message(
            id="msg_retry_summary",
            conversation_id=scene.conversation_id,
            speaker_type="system",
            speaker_id="system",
            content="장면을 다음 단계로 진행한다",
        )
    ]
    llm = EmptyThenSummaryLLMClient()

    summary = await summarize_scene_memory_with_llm(scene, recent, llm_client=llm)

    assert summary
    assert summary.startswith("[Rolling Story Arc]")
    assert len(llm.calls) == 2
    assert "System retry instruction" in llm.calls[1][-1]["content"]


def test_prompt_requests_structured_dialogue_action_thought_and_keeps_broader_context():
    character = Character(
        id="char_1",
        name="아리아",
        persona="사용자를 오래 봐온 후배이자 가까운 파트너다. 외형은 차분하고 단정한 인상이다.",
        behavior_style="상대가 피곤해 보이면 설명을 줄이고 먼저 정리한다. 가까운 거리에서 자연스럽게 챙긴다.",
        speech_style="사용자, 그건 내가 정리해줄게.\n아니 이건 이렇게 보면 됨.",
    )
    recent = [
        Message(id=f"msg_{idx}", conversation_id="conv_1", speaker_type="character", speaker_id="char_1", content=f"이전 대화 {idx}")
        for idx in range(18)
    ]
    scene = SceneState(
        conversation_id="conv_1",
        location="작업방",
        mood="편안함",
        current_conflict="캐릭터 대화가 말싸움만 되는 문제",
        last_event="방금 사용자가 액션과 속마음이 필요하다고 말했다.",
        summary="User description: 사용자는 개발자다.\nScene memory: 이전에 SSE와 1:1 대화방 구조를 같이 잡았다.",
    )

    messages = build_character_messages(character=character, recent_messages=recent, user_message="맥락 살려줘", scene_state=scene)
    system = messages[0]["content"]

    assert '"dialogue"' in system
    assert "action" in system
    assert "thought" in system
    assert "action/thought may be empty" in system
    assert "Keep dialogue, action, and thought in separate JSON fields" in system
    assert "Never use Markdown markers to encode whether text is dialogue, action, or thought" in system
    assert "Do not rely only on the immediately previous message" in system
    assert "[User situation/action]" in system
    assert "[User dialogue]" in system
    assert "Treat situation/action as what visibly happens" in system
    assert "Scene memory: 이전에 SSE와 1:1 대화방 구조를 같이 잡았다." in system
    assert "[Persona background]" in system
    assert "Do not dump this as exposition; use it implicitly" in system
    assert "[Behavior style]" in system
    assert "적재적소" not in system
    assert "상대가 피곤해 보이면 설명을 줄이고 먼저 정리한다" in system
    assert "[Speech examples / tone-and-manner samples]" in system
    assert "Do not copy/repeat only these lines" in system
    assert "사용자, 그건 내가 정리해줄게" in system
    assert "이전 대화 0" not in system
    assert "이전 대화 10" in system
    assert "이전 대화 17" in system


def test_long_preset_requires_more_bubbles_without_bloated_action_or_thought():
    assert runtime_settings_service.min_bubbles_for_preset("medium", is_multi_room=False) == 2
    assert runtime_settings_service.max_bubbles_for_preset("medium", is_multi_room=False) == 3
    assert runtime_settings_service.min_bubbles_for_preset("long", is_multi_room=False) == 3
    assert runtime_settings_service.max_bubbles_for_preset("long", is_multi_room=False) == 6

    character = Character(id="char_1", name="세아", persona="도장깨기 방에서 승부 흐름을 또렷하게 잡는다")
    messages = build_multi_character_messages(
        characters=[character],
        recent_messages=[],
        user_message="긴대화로 이어가",
        min_bubbles=runtime_settings_service.min_bubbles_for_preset("long", is_multi_room=False),
        max_bubbles=runtime_settings_service.max_bubbles_for_preset("long", is_multi_room=False),
        min_output_tokens=runtime_settings_service.preset_token_target("long"),
    )
    system = messages[0]["content"]

    assert "Keep total replies between 3 and 6" in system
    assert "Length preset: long. Return 3-6 distinct reply bubbles" in system
    assert "2-4 natural sentences" in system
    assert "at most 120 characters" in system
    assert "visible action is at most 80 characters" not in system
    assert "Available output tokens are a ceiling" not in system
    assert "Target around" not in system
    assert "Dialogue should be short" not in system
    assert '"thought"' in system


def test_multi_prompt_includes_cast_roles_and_speaking_priority():
    primary = Character(id="char_harin", name="서모아", persona="겉으론 튕기지만 반응이 빠르다")
    rival = Character(id="char_yerin", name="로아", persona="질투와 견제로 분위기를 흔든다")
    observer = Character(id="char_mina", name="아리아", persona="밝게 관찰하며 짧게 끼어든다")

    messages = build_multi_character_messages(
        characters=[primary, rival, observer],
        recent_messages=[],
        user_message="이 구도로 이어가",
        conversation_mode="character_character",
        room_cast_roles={
            "char_harin": "primary",
            "char_yerin": "rival",
            "char_mina": "observer",
        },
    )
    system = messages[0]["content"]

    assert "[Cast roles]" in system
    assert "Cast roles / speaking priority:" in system
    assert "서모아(char_harin)=primary" in system
    assert "로아(char_yerin)=rival" in system
    assert "아리아(char_mina)=observer" in system
    assert "Primary/rival/antagonist roles lead their intended beat" in system
    assert "support/observer roles stay lighter" in system
    assert "silent roles never generate replies" in system


def test_multi_prompt_keeps_official_domain_context_once_outside_character_continuity():
    first = Character(id="char_a", name="아린", persona="승부욕이 강하다")
    second = Character(id="char_b", name="도희", persona="여유롭다")

    messages = build_multi_character_messages(
        characters=[first, second],
        recent_messages=[],
        user_message="이어가",
        continuity_context_by_character={
            "char_a": "[Runtime relationships]\ncharacter:char_b | trust=1",
            "char_b": "[Runtime relationships]\ncharacter:char_a | trust=1",
        },
        official_domain_context="[Official battle state]\nStandings: 아린 1위",
    )
    system = messages[0]["content"]

    assert system.count("[Official battle state]") == 1
    assert "[Official domain state]" in system
    assert system.count("Standings: 아린 1위") == 1
    assert "[Character-specific user notes]" in system


def test_prompt_includes_generic_continuity_state_without_romance_lock_in():
    character = Character(id="char_1", name="아리아", persona="다정하다")
    messages = build_character_messages(
        character=character,
        recent_messages=[],
        user_message="이어가",
        continuity_context="[Relationship / interaction state]\ntrust=1; affinity=2; tension=0; conflict=0; cooperation=1\n[Long-term character memory]\n- event | importance=2/5 | dialogue=사용자가 피곤하다고 말했다",
    )
    system = messages[0]["content"]

    assert "[Continuity state]" in system
    assert "[Relationship / interaction state]" in system
    assert "[Long-term character memory]" in system
    assert "trust=1" in system
    assert "사용자가 피곤하다고 말했다" in system


def test_gemini_client_uses_lowest_safety_threshold_for_character_chat():
    settings = Settings(llm_mock=False, chat_llm_provider="gemini", gemini_api_key="test-key")
    client = LLMClient(settings=settings, profile="chat")

    safety = client.gemini_safety_settings()

    assert {item["threshold"] for item in safety} == {"BLOCK_NONE"}
    assert {item["category"] for item in safety} >= {
        "HARM_CATEGORY_HARASSMENT",
        "HARM_CATEGORY_HATE_SPEECH",
        "HARM_CATEGORY_SEXUALLY_EXPLICIT",
        "HARM_CATEGORY_DANGEROUS_CONTENT",
        "HARM_CATEGORY_CIVIC_INTEGRITY",
    }


def test_prompt_contains_identity_lock_description_and_counterpart_cards():
    aria = Character(
        id="char_aria",
        name="아리아",
        description="사용자를 오래 챙겨온 여자친구 같은 후배",
        persona="가깝고 다정하지만 일 얘기엔 빠르게 정리한다.",
        behavior_style="말로 설정을 설명하기보다 사용자 상태를 보고 먼저 움직인다.",
        speech_style="사용자라고 부르고 편한 반말을 쓴다.",
    )
    luna = Character(
        id="char_luna",
        name="루나",
        description="밝고 야무진 여동생 포지션",
        persona="언니를 믿고 장난스럽게 받쳐준다.",
        behavior_style="분위기가 무거우면 밝게 치고 들어오되 언니의 판단을 넘지 않는다.",
        speech_style="밝고 짧게 리액션한다.",
    )

    messages = build_character_messages(
        character=aria,
        recent_messages=[],
        user_message="이어가",
        conversation_mode="character_character",
        room_characters=[aria, luna],
    )
    system = messages[0]["content"]

    assert "[Identity lock - highest priority]" in system
    assert "사용자를 오래 챙겨온 여자친구 같은 후배" in system
    assert "가깝고 다정하지만 일 얘기엔 빠르게 정리한다." in system
    assert "말로 설정을 설명하기보다 사용자 상태를 보고 먼저 움직인다." in system
    assert "사용자라고 부르고 편한 반말을 쓴다." in system
    assert "Do not recite or explain" in system
    assert "Treat speech examples as tone-and-manner samples" in system
    assert "Do not drift into a generic assistant" in system
    assert "[Counterpart character cards]" in system
    assert "루나" in system
    assert "밝고 야무진 여동생 포지션" in system
    assert "언니를 믿고 장난스럽게 받쳐준다." in system
    assert "분위기가 무거우면 밝게 치고 들어오되 언니의 판단을 넘지 않는다." in system


def test_prompt_locks_every_character_as_female_even_with_mode_labels():
    rina = Character(
        id="char_rina",
        name="유나",
        description="클럽 핫걸, 무용과 여성 캐릭터",
        persona="[Man-mode] 상대가 남자일 때 다르게 반응한다.\n[Woman-mode] 상대가 여성 캐릭터일 때 경쟁적으로 반응한다.",
        speech_style="상대 유형에 따라 말투가 바뀌지만 유나 자신은 항상 여성이다.",
    )
    aria = Character(id="char_aria", name="아리아", description="여성 캐릭터", persona="다정하다.")

    messages = build_character_messages(
        character=rina,
        recent_messages=[],
        user_message="인사해",
        conversation_mode="character_character",
        room_characters=[rina, aria],
    )
    system = messages[0]["content"]

    assert "유나 is always a female character" in system
    assert "never male" in system
    assert "Man-mode" in system
    assert "labels describe the counterpart/user type, not 유나's own gender" in system
    assert "Woman-mode" in system
    assert "상대가 여성 캐릭터일 때" in system


def test_prompt_includes_trait_scores():
    character = Character(
        id="char_aria",
        name="아리아",
        persona="자신감 넘치고 장난스럽다.",
        trait_scores={"confidence": 5, "jealousy": 4, "eros": 3},
    )
    messages = build_character_messages(character=character, recent_messages=[], user_message="테스트")
    system = messages[0]["content"]

    assert "[Character trait scores]" in system
    assert "자신감: 5/5" in system
    assert "질투심: 4/5" in system
    assert "에로스/관능성: 3/5" in system
    assert "without stating the numbers directly" in system


def test_auto_dialogue_prompt_names_counterpart_character_and_all_female_context():
    aria = Character(id="char_aria", name="아리아", persona="사용자를 챙긴다.")
    luna = Character(id="char_luna", name="루나", persona="밝게 반응한다.")
    messages = build_character_messages(
        character=aria,
        recent_messages=[Message(id="msg_1", conversation_id="conv_1", speaker_type="character", speaker_id="char_luna", content="언니, 사용자가 아니라 나한테 대답해줘.")],
        user_message="언니, 사용자가 아니라 나한테 대답해줘.",
        conversation_mode="character_character",
        room_characters=[aria, luna],
    )
    system = messages[0]["content"]

    assert "This is a character-to-character room" in system
    assert "The human user is only an observer" in system
    assert "루나" in system
    assert "All character participants are female characters" in system
    assert "Do not treat the latest message as if it came from the human user" in system


class CompressionUpdateLLMClient:
    async def chat(self, messages, response_format=None):
        system_prompt = messages[0]["content"]
        user_prompt = messages[1]["content"]
        if "incrementally compress character-chat transcript" in system_prompt:
            assert response_format is None
            assert "Room-specific memory/relationship update contract" not in user_prompt
            return LLMResponse(content="""[Rolling Story Arc]
- char_1이 합의한 경계와 약속을 지키겠다는 태도를 분명히 했다.""")
        if "extract only durable long-term memory" in system_prompt:
            assert response_format == {"type": "json_object"}
            assert "Room-specific memory/relationship update contract: 약속과 경계선을 최우선 보존" in user_prompt
            assert "Reject ordinary dialogue" in system_prompt
            return LLMResponse(content='{"memories":[]}')
        if "person-to-person relationship state" in system_prompt:
            assert response_format == {"type": "json_object"}
            assert "Room-specific memory/relationship update contract: 약속과 경계선을 최우선 보존" in user_prompt
            assert "Official domain state is read-only constraint" in user_prompt
            return LLMResponse(content='{"relationships":[]}')
        raise AssertionError(system_prompt)


@pytest.mark.asyncio
async def test_conversation_compression_update_prompt_includes_room_focus():
    scene = SceneState(conversation_id="conv_1", compression_focus="약속과 경계선을 최우선 보존")
    recent = [Message(id="m1", conversation_id="conv_1", speaker_type="character", speaker_id="char_1", content="약속은 꼭 지킬게")]

    update = await summarize_conversation_state_with_llm(
        scene_state=scene,
        recent_messages=recent,
        character_ids=["char_1"],
        relationship_states=[],
        memories=[],
        llm_client=CompressionUpdateLLMClient(),
    )

    assert update["scene"]["summary"].startswith("[Rolling Story Arc]")
    assert "[Current Scene State]" not in update["scene"]["summary"]
    assert update["memories"] == []
    assert update["relationships"] == []


def test_common_room_memory_is_validated_and_rendered_separately_from_character_memory():
    update = validate_conversation_compression_update({
        "scene": {},
        "memories": [
            {"character_id": "__room__", "memory_type": "fact", "content": "세계관 규칙: 이 방에서는 카페가 비밀 모임 장소로 확정됐다.", "importance": 4},
            {"character_id": "char_1", "memory_type": "preference", "content": "사용자는 다음 장면에서 약속을 먼저 확인하길 원한다.", "importance": 4},
        ],
        "relationships": [],
    }, {"char_1"})

    assert update is not None
    assert update["memories"] == []
    context = build_continuity_context([
        CharacterMemory(id="mem_common", conversation_id="conv_1", character_id=COMMON_ROOM_MEMORY_CHARACTER_ID, memory_type="user_note", content="세계관 규칙: 이 방에서는 카페가 비밀 모임 장소로 확정됐다.", importance=4),
        CharacterMemory(id="mem_char", conversation_id="conv_1", character_id="char_1", memory_type="user_note", content="사용자는 다음 장면에서 약속을 먼저 확인하길 원한다.", importance=4),
    ])
    assert "[Common room memory known by all characters]" in context
    assert "[Long-term character memory]" in context
    assert "카페가 비밀 모임 장소" in context


def test_memory_dedupe_treats_rank_rule_rephrases_as_duplicates():
    existing = [
        CharacterMemory(
            id="mem_old",
            conversation_id="conv_1",
            character_id=COMMON_ROOM_MEMORY_CHARACTER_ID,
            memory_type="boundary",
            content="서열 결정은 오직 '성적 절정(오르가즘)과 쾌감으로의 완벽한 압도'를 통해서만 이루어진다.",
            importance=5,
        )
    ]

    assert memory_already_exists(existing, "모든 결투는 '성적 절정(오르가즘)과 쾌감으로의 완벽한 압도'를 승리 조건으로 한다.")


def test_compression_memory_dedupe_scans_beyond_context_display_limit(session):
    session.add(SceneState(conversation_id="conv_1"))
    for index in range(40):
        session.add(CharacterMemory(
            id=f"mem_filler_{index}",
            conversation_id="conv_1",
            character_id=COMMON_ROOM_MEMORY_CHARACTER_ID,
            memory_type="fact",
            content=f"세계관 사실 {index}: 오래된 표시용 메모리",
            importance=5,
        ))
    session.add(CharacterMemory(
        id="mem_old_duplicate",
        conversation_id="conv_1",
        character_id=COMMON_ROOM_MEMORY_CHARACTER_ID,
        memory_type="boundary",
        content="서열 결정은 오직 성적 절정과 쾌감으로 완벽히 압도해야 이루어진다.",
        importance=5,
    ))
    session.commit()

    apply_conversation_compression_update(session, "conv_1", {
        "scene": {},
        "memories": [{
            "character_id": COMMON_ROOM_MEMORY_CHARACTER_ID,
            "memory_type": "boundary",
            "content": "모든 결투는 성적 절정과 쾌감으로 완벽히 압도해야 서열이 결정된다.",
            "importance": 5,
        }],
        "relationships": [],
    })

    rows = session.query(CharacterMemory).filter(
        CharacterMemory.conversation_id == "conv_1",
        CharacterMemory.character_id == COMMON_ROOM_MEMORY_CHARACTER_ID,
    ).all()
    assert len(rows) == 41


def test_battle_flow_noise_is_rejected_from_long_term_memory():
    update = validate_conversation_compression_update({
        "scene": {},
        "memories": [{
            "character_id": COMMON_ROOM_MEMORY_CHARACTER_ID,
            "memory_type": "event",
            "content": "신나비(9위)가 다나(10위)에게 압도적인 공세를 가하며 승리 확정 직전 상태에 도달함. (최종 판결 대기)",
            "importance": 5,
        }],
        "relationships": [],
    }, {"char_1"})

    assert update["memories"] == []


def test_battle_ranking_ledger_keeps_existing_protected_slot(session):
    session.add(SceneState(conversation_id="conv_ledger"))
    session.add(CharacterMemory(
        id="mem_rank_old",
        conversation_id="conv_ledger",
        character_id=COMMON_ROOM_MEMORY_CHARACTER_ID,
        memory_type="fact",
        content="현재 확정된 주요 서열: 솔(char_02d43a7f6106): 13위 > 유나(char_4846da80d531): 14위.",
        importance=5,
    ))
    session.commit()

    apply_conversation_compression_update(session, "conv_ledger", {
        "scene": {},
        "memories": [{
            "character_id": COMMON_ROOM_MEMORY_CHARACTER_ID,
            "memory_type": "fact",
            "content": "현재 확정된 주요 서열: 로아(char_d75931ab1eca): 12위 > 솔(char_02d43a7f6106): 99위 > 유나(char_4846da80d531): 1위.",
            "importance": 5,
        }],
        "relationships": [],
    })

    rows = session.query(CharacterMemory).filter(
        CharacterMemory.conversation_id == "conv_ledger",
        CharacterMemory.character_id == COMMON_ROOM_MEMORY_CHARACTER_ID,
    ).all()
    assert len(rows) == 1
    assert "솔(char_02d43a7f6106): 13위" in rows[0].content
    assert "유나(char_4846da80d531): 14위" in rows[0].content
    assert "99위" not in rows[0].content


def test_battle_ledger_rejects_name_id_mismatch_from_compression(session):
    session.add(SceneState(conversation_id="conv_ledger_mismatch"))
    session.add(CharacterMemory(
        id="mem_rank_anchor",
        conversation_id="conv_ledger_mismatch",
        character_id=COMMON_ROOM_MEMORY_CHARACTER_ID,
        memory_type="fact",
        content="현재 확정된 주요 서열: 솔(char_02d43a7f6106): 13위 > 유나(char_4846da80d531): 14위.",
        importance=5,
    ))
    session.commit()

    apply_conversation_compression_update(session, "conv_ledger_mismatch", {
        "scene": {},
        "memories": [{
            "character_id": COMMON_ROOM_MEMORY_CHARACTER_ID,
            "memory_type": "event",
            "content": "솔(char_4846da80d531)가 유나(char_02d43a7f6106)에게 승리함.",
            "importance": 5,
        }],
        "relationships": [],
    })

    rows = session.query(CharacterMemory).filter(
        CharacterMemory.conversation_id == "conv_ledger_mismatch",
        CharacterMemory.character_id == COMMON_ROOM_MEMORY_CHARACTER_ID,
    ).all()
    assert len(rows) == 1
    assert rows[0].id == "mem_rank_anchor"


def test_battle_ledger_scene_summary_strips_rank_claims_from_compression(session):
    session.add(SceneState(conversation_id="conv_scene_rank"))
    session.commit()

    apply_conversation_compression_update(session, "conv_scene_rank", {
        "scene": {
            "summary": "[Scene memory compact]\nVisible situation: 공식 서열은 11위 리오, 12위 솔, 13위 로아으로 갱신됨.\nRecent visible beat: 상호 경계 반응이 이어진다.\nCharacter visible states:\n- 솔: 경계 유지\nCarry forward: 감정선은 이어가되 장부는 기존 메모리를 따른다.",
        },
        "memories": [],
        "relationships": [],
    })

    scene = session.get(SceneState, "conv_scene_rank")
    assert "공식 서열" not in scene.summary
    assert "12위 솔" not in scene.summary
    assert "상호 경계" in scene.summary
    assert "기존 메모리" in scene.summary


def test_battle_ledger_scene_fields_ignore_rank_claims_from_compression(session):
    session.add(SceneState(
        conversation_id="conv_scene_fields",
        current_conflict="공식 장부 확인",
        last_event="이전 공식 장부 유지",
    ))
    session.commit()

    apply_conversation_compression_update(session, "conv_scene_fields", {
        "scene": {
            "current_conflict": "공식 장부 기준: 11위 리오, 12위 솔, 13위 로아 상태에서 다음 흐름 확인",
            "last_event": "시스템 반영: 솔가 로아에게 승리해 12위가 되었고 로아은 13위가 됨.",
        },
        "memories": [],
        "relationships": [],
    })

    scene = session.get(SceneState, "conv_scene_fields")
    assert scene.current_conflict == "공식 장부 확인"
    assert scene.last_event == "이전 공식 장부 유지"


def test_battle_ledger_scene_summary_strips_rank_claims_when_ledger_exists(session):
    session.add(SceneState(conversation_id="conv_scene_rank_guard"))
    session.add(CharacterMemory(
        id="mem_rank_guard",
        conversation_id="conv_scene_rank_guard",
        character_id=COMMON_ROOM_MEMORY_CHARACTER_ID,
        memory_type="fact",
        content="현재 확정된 주요 서열: 솔(char_02d43a7f6106): 13위 > 유나(char_4846da80d531): 14위.",
        importance=5,
    ))
    session.commit()

    apply_conversation_compression_update(session, "conv_scene_rank_guard", {
        "scene": {
            "summary": "[Scene memory compact]\nVisible situation: 공식 서열은 11위 리오, 12위 솔, 13위 로아으로 갱신됨.\nRecent visible beat: 상호 경계 반응이 이어진다.\nCharacter visible states:\n- 솔와 유나가 서로 경계한다.\nCarry forward: 감정선은 이어가되 장부는 기존 메모리를 따른다.",
        },
        "memories": [],
        "relationships": [],
    })

    scene = session.get(SceneState, "conv_scene_rank_guard")
    assert "공식 서열" not in scene.summary
    assert "12위 솔" not in scene.summary
    assert "서로 경계" in scene.summary
    assert "기존 메모리" in scene.summary


def test_battle_ledger_scene_fields_ignore_rank_claims_when_ledger_exists(session):
    session.add(SceneState(
        conversation_id="conv_scene_fields_guard",
        current_conflict="기존 갈등 유지",
        last_event="기존 사건 유지",
    ))
    session.add(CharacterMemory(
        id="mem_rank_field_guard",
        conversation_id="conv_scene_fields_guard",
        character_id=COMMON_ROOM_MEMORY_CHARACTER_ID,
        memory_type="fact",
        content="현재 확정된 주요 서열: 솔(char_02d43a7f6106): 13위 > 유나(char_4846da80d531): 14위.",
        importance=5,
    ))
    session.commit()

    apply_conversation_compression_update(session, "conv_scene_fields_guard", {
        "scene": {
            "current_conflict": "공식 장부 기준: 솔가 1위, 유나가 2위로 바뀜.",
            "last_event": "시스템 반영: 솔가 유나에게 승리해 1위가 됨.",
            "mood": "긴장감 유지",
        },
        "memories": [],
        "relationships": [],
    })

    scene = session.get(SceneState, "conv_scene_fields_guard")
    assert scene.current_conflict == "기존 갈등 유지"
    assert scene.last_event == "기존 사건 유지"
    assert scene.mood == "긴장감 유지"


def test_multi_character_prompt_includes_battle_control_runtime_hints():
    characters = [
        Character(id="char_a", name="세아", persona="전략적이다"),
        Character(id="char_b", name="다나", persona="거칠다"),
    ]

    prompt = build_multi_character_messages(
        characters=characters,
        recent_messages=[],
        user_message="계속",
        conversation_mode="character_character",
        genre_mode="battle",
        battle_control_context={
            "action": "progress",
            "active_pair": {
                "participant_a_id": "char_a",
                "participant_a_name": "세아",
                "participant_b_id": "char_b",
                "participant_b_name": "다나",
            },
            "favored_character_id": "char_a",
            "favored_character_name": "세아",
            "advantage_percent": 60,
        },
    )[0]["content"]

    assert "[Battle control hint]" in prompt
    assert "official active pair: 세아(char_a) vs 다나(char_b)" in prompt
    assert "current advantage: 세아(char_a) 60%" in prompt
    assert "Treat this as a live battle-control signal" in prompt


def test_multi_character_prompt_locks_official_winner_for_battle_control_end():
    characters = [
        Character(id="char_a", name="세아", persona="전략적이다"),
        Character(id="char_b", name="다나", persona="거칠다"),
    ]

    prompt = build_multi_character_messages(
        characters=characters,
        recent_messages=[],
        user_message="마무리",
        conversation_mode="character_character",
        genre_mode="battle",
        battle_control_context={
            "action": "end",
            "active_pair": {
                "participant_a_id": "char_a",
                "participant_a_name": "세아",
                "participant_b_id": "char_b",
                "participant_b_name": "다나",
            },
            "winner_id": "char_b",
            "winner_name": "다나",
        },
    )[0]["content"]

    assert "official winner is fixed: 다나(char_b)" in prompt
    assert "Do not overturn, reinterpret, or make the loser win" in prompt


def test_multi_character_prompt_uses_adaptive_recent_history_and_excludes_thoughts():
    characters = [
        Character(id="char_a", name="세아", persona="전략적이다"),
        Character(id="char_b", name="다나", persona="거칠다"),
    ]
    messages = []
    for idx in range(20):
        speaker_type = "system" if idx in {2, 7} else "character"
        speaker_id = "system" if speaker_type == "system" else ("char_a" if idx % 2 == 0 else "char_b")
        messages.append(Message(
            id=f"msg_{idx}",
            conversation_id="conv_prompt",
            speaker_type=speaker_type,
            speaker_id=speaker_id,
            content=f"message {idx} " + ("x" * 300),
            action=f"action {idx} " + ("y" * 180),
            thought=f"thought {idx} " + ("z" * 180),
        ))

    prompt = build_multi_character_messages(
        characters=characters,
        recent_messages=messages,
        user_message="continue",
        conversation_mode="character_character",
    )[0]["content"]

    assert "message 0" not in prompt
    assert "message 7" in prompt  # older system anchor survives
    assert "message 12" in prompt
    assert "message 19" in prompt
    assert "thought 12" not in prompt
    assert "thought 16" not in prompt
    assert "z" * 120 not in prompt
    assert "x" * 260 not in prompt


def test_relationship_context_prefers_active_counterparts_over_recent_inactive(session):
    now = datetime.now(timezone.utc)
    states = [
        ConversationRelationshipState(
            conversation_id="conv_rel",
            character_id="char_a",
            counterpart_type="character",
            counterpart_id="char_old",
            current_dynamic="very recent inactive",
            updated_at=now,
        ),
        ConversationRelationshipState(
            conversation_id="conv_rel",
            character_id="char_a",
            counterpart_type="character",
            counterpart_id="char_active",
            current_dynamic="older active",
            updated_at=now - timedelta(days=1),
        ),
        ConversationRelationshipState(
            conversation_id="conv_rel",
            character_id="char_a",
            counterpart_type="user",
            counterpart_id="user_001",
            current_dynamic="user anchor",
            updated_at=now - timedelta(days=2),
        ),
    ]
    for state in states:
        session.add(state)
    session.commit()

    selected = list_relationship_states_for_character(
        session,
        "conv_rel",
        "char_a",
        limit=3,
        active_counterpart_ids={"char_active", "user_001"},
    )

    ids = [state.counterpart_id for state in selected]
    assert set(ids) == {"char_active", "user_001"}
    assert "char_old" not in ids


def test_scene_compression_is_throttled_until_enough_conversation_turns(session):
    now = datetime.now(timezone.utc)
    session.add(SceneState(conversation_id="conv_throttle", summary="existing compact summary", updated_at=now))
    for idx in range(4):
        session.add(Message(
            id=f"msg_throttle_turn_{idx}",
            conversation_id="conv_throttle",
            speaker_type="user",
            speaker_id="user_001",
            content="next beat",
            created_at=now + timedelta(seconds=idx + 1),
        ))
        session.add(Message(
            id=f"msg_throttle_bubble_{idx}",
            conversation_id="conv_throttle",
            speaker_type="character",
            speaker_id="char_a",
            content="recent bubble",
            created_at=now + timedelta(seconds=idx + 10),
        ))
    session.commit()

    assert not should_update_scene_orchestration_summary(session, "conv_throttle", generated_character_messages=2, interval_turns=5)

    session.add(Message(
        id="msg_throttle_turn_5",
        conversation_id="conv_throttle",
        speaker_type="user",
        speaker_id="user_001",
        content="fifth beat",
        created_at=now + timedelta(seconds=30),
    ))
    session.commit()

    assert should_update_scene_orchestration_summary(session, "conv_throttle", generated_character_messages=2, interval_turns=5)
    assert not should_update_scene_orchestration_summary(session, "conv_throttle", generated_character_messages=0, force=True)


def test_system_scene_updates_do_not_reset_compression_turn_cadence(session):
    baseline = datetime.now(timezone.utc) - timedelta(hours=1)
    scene = SceneState(
        conversation_id="conv_system_cadence",
        summary="[Rolling Story Arc]\n- 이전 압축",
        last_compression_attempt_at=baseline,
        updated_at=baseline,
    )
    session.add(scene)
    for index in range(3):
        session.add(Message(
            id=f"msg_system_cadence_{index}",
            conversation_id=scene.conversation_id,
            speaker_type="system",
            speaker_id="system",
            content=f"장면 지시 {index}",
            created_at=baseline + timedelta(minutes=index + 1),
        ))
    session.commit()

    record_system_scene_direction(session, scene.conversation_id, "세 번째 장면 지시")
    refreshed = session.get(SceneState, scene.conversation_id)

    assert refreshed.last_compression_attempt_at == baseline.replace(tzinfo=None)
    assert refreshed.updated_at > baseline.replace(tzinfo=None)
    assert should_update_scene_orchestration_summary(
        session,
        scene.conversation_id,
        generated_character_messages=1,
        interval_turns=3,
    )


def test_scene_compression_waits_for_interval_even_when_summary_is_missing(session):
    now = datetime.now(timezone.utc)
    session.add(SceneState(conversation_id="conv_first_summary", updated_at=now))
    session.add(Message(
        id="msg_first_summary_1",
        conversation_id="conv_first_summary",
        speaker_type="character",
        speaker_id="char_a",
        content="first generated bubble",
        created_at=now + timedelta(seconds=1),
    ))
    for idx in range(4):
        session.add(Message(
            id=f"msg_first_summary_user_{idx}",
            conversation_id="conv_first_summary",
            speaker_type="user",
            speaker_id="user_001",
            content="아직 압축 턴 전",
            created_at=now + timedelta(seconds=idx + 2),
        ))
    session.commit()

    assert not should_update_scene_orchestration_summary(
        session,
        "conv_first_summary",
        generated_character_messages=1,
        interval_turns=5,
    )

    session.add(Message(
        id="msg_first_summary_user_5",
        conversation_id="conv_first_summary",
        speaker_type="user",
        speaker_id="user_001",
        content="다섯 번째 턴",
        created_at=now + timedelta(seconds=20),
    ))
    session.commit()

    assert should_update_scene_orchestration_summary(
        session,
        "conv_first_summary",
        generated_character_messages=1,
        interval_turns=5,
    )


@pytest.mark.asyncio
async def test_scene_compression_failure_keeps_existing_summary_without_fallback(session):
    now = datetime.now(timezone.utc)
    scene = SceneState(
        conversation_id="conv_compression_fail",
        summary="[Rolling Story Arc]\n- 이전 압축 내용 유지",
        current_conflict="오래된 stale conflict",
        last_event="오래된 stale event",
        updated_at=now,
    )
    session.add(scene)
    recent = [
        Message(
            id=f"msg_fail_{idx}",
            conversation_id="conv_compression_fail",
            speaker_type="user" if idx % 2 == 0 else "character",
            speaker_id="user_001" if idx % 2 == 0 else "char_a",
            content=f"최근 대화 {idx}",
            created_at=now + timedelta(seconds=idx + 1),
        )
        for idx in range(14)
    ]
    for message in recent:
        session.add(message)
    session.commit()

    result = await update_scene_orchestration_summary(
        session,
        "conv_compression_fail",
        recent,
        llm_client=FailingCompressionClient(),
        character_ids=["char_a"],
    )

    refreshed = session.get(SceneState, "conv_compression_fail")
    assert result.summary == "[Rolling Story Arc]\n- 이전 압축 내용 유지"
    assert refreshed.summary == "[Rolling Story Arc]\n- 이전 압축 내용 유지"
    assert "최근 대화" not in refreshed.summary
    assert "gemini 503" in refreshed.last_compression_error
    assert refreshed.last_compression_attempt_at is not None
    assert refreshed.last_compression_attempt_at >= now.replace(tzinfo=None)
    assert refreshed.last_compressed_at is None


def test_successful_compression_records_success_separately_from_attempt(session):
    scene = SceneState(conversation_id="conv_compression_success")
    session.add(scene)
    session.commit()

    apply_conversation_compression_update(
        session,
        scene.conversation_id,
        {
            "scene": {
                "summary": "[Rolling Story Arc]\n- 새로운 압축 내용",
                "tension_delta": 0,
                "romance_delta": 0,
            },
            "relationships": [],
        },
    )

    refreshed = session.get(SceneState, scene.conversation_id)
    assert refreshed.last_compressed_at is not None
    assert refreshed.summary == "[Rolling Story Arc]\n- 새로운 압축 내용"
