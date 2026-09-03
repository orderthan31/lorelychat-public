import json

import pytest

from app.db.models import Conversation, Message, SceneState
from app.engine.llm_client import LLMResponse
from app.services import conversation_service


def valid_arc(label: str = "사건") -> str:
    return "[Rolling Story Arc]\n" + "\n".join(
        f"- {label} {index}: 인물들은 공개된 사건의 원인과 결과를 확인하고 이어질 약속과 미해결 과제를 남겼다."
        for index in range(1, 13)
    )


def extraction_payload() -> dict:
    return {
        "events": [{
            "event": "두 인물이 대화를 이어갔다",
            "actors": ["char_a"],
            "chronology_causality": "질문 뒤 답변이 이어졌다",
            "lifecycle_state": "active",
            "source_support": "마지막 메시지까지 공개된 대사와 행동",
        }],
        "open_hooks": ["다음 답변이 남아 있다"],
        "do_not_promote_to_fact": [],
        "terminal_state": "마지막 질문에 답할 차례다",
    }


def critic_payload() -> dict:
    return {
        "repair_required": False,
        "missing_or_weak_items": [],
        "unsupported_or_promoted_items": [],
        "chronology_lifecycle_corrections": [],
        "terminal_hook_requirements": ["마지막 질문을 미해결로 유지"],
        "final_guidance": ["한국어 사건 흐름으로 합성"],
    }


class SequenceClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.outcomes = []

    async def chat(self, messages, *, response_format=None, conversation_id=None):
        self.calls.append({"messages": messages, "response_format": response_format, "conversation_id": conversation_id})
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return LLMResponse(content=response)

    def record_response_outcome(self, response, **kwargs):
        self.outcomes.append(kwargs)


@pytest.mark.asyncio
async def test_fast_strategy_is_singleton_and_does_not_semantically_repair_invalid_output():
    scene = SceneState(conversation_id="conv_fast", summary="[Rolling Story Arc]\n- 이전 사건")
    messages = [Message(id="msg_fast", conversation_id=scene.conversation_id, speaker_type="character", speaker_id="char_a", content="다음 이야기를 이어가자")]
    client = SequenceClient(["not a rolling arc", valid_arc("호출되면 안 됨")])

    result = await conversation_service.summarize_scene_memory_with_llm(
        scene, messages, llm_client=client, compression_strategy="fast"
    )

    assert result is None
    assert len(client.calls) == 1
    assert "Fast compression" in client.calls[0]["messages"][0]["content"]
    assert client.outcomes[-1]["metadata_updates"] == {
        "compression_strategy": "fast",
        "compression_stage": "audited_one_pass",
    }


@pytest.mark.asyncio
async def test_fast_strategy_uses_generic_core_and_adds_battle_guard_only_for_battle_rooms():
    scene = SceneState(conversation_id="conv_genre", summary="[Rolling Story Arc]\n- 이전 사건")
    messages = [Message(id="msg_genre", conversation_id=scene.conversation_id, speaker_type="character", speaker_id="char_a", content="다음 이야기를 이어가자")]
    generic = SequenceClient([valid_arc("일상")])
    battle = SequenceClient([valid_arc("배틀")])

    await conversation_service.summarize_scene_memory_with_llm(
        scene, messages, llm_client=generic, compression_strategy="fast", genre_mode="slice_of_life"
    )
    await conversation_service.summarize_scene_memory_with_llm(
        scene, messages, llm_client=battle, compression_strategy="fast", genre_mode="battle"
    )

    generic_prompt = generic.calls[0]["messages"][0]["content"]
    battle_prompt = battle.calls[0]["messages"][0]["content"]
    assert "8-18 complete one-line bullets according to actual durable event density" in generic_prompt
    assert "sparse source" in generic_prompt
    assert "Official standings, points, rankings" not in generic_prompt
    assert "Official standings, points, rankings" in battle_prompt


@pytest.mark.asyncio
async def test_quality_strategy_adds_battle_guard_to_all_three_stages():
    scene = SceneState(conversation_id="conv_quality_battle", summary="[Rolling Story Arc]\n- 이전 경기")
    messages = [Message(id="msg_quality_battle", conversation_id=scene.conversation_id, speaker_type="character", speaker_id="char_a", content="다음 경기를 약속했다")]
    client = SequenceClient([
        json.dumps(extraction_payload(), ensure_ascii=False),
        json.dumps(critic_payload(), ensure_ascii=False),
        valid_arc("배틀 품질"),
    ])

    await conversation_service.summarize_scene_memory_with_llm(
        scene, messages, llm_client=client, compression_strategy="quality", genre_mode="battle"
    )

    assert len(client.calls) == 3
    assert all("Battle-domain provenance guard" in call["messages"][0]["content"] for call in client.calls)


@pytest.mark.asyncio
async def test_fast_strategy_uses_db_managed_strategy_prompt_content():
    scene = SceneState(conversation_id="conv_custom_prompt", summary="[Rolling Story Arc]\n- 이전 사건")
    messages = [Message(id="msg_custom_prompt", conversation_id=scene.conversation_id, speaker_type="character", speaker_id="char_a", content="다음 이야기를 이어가자")]
    client = SequenceClient([valid_arc("커스텀")])

    await conversation_service.summarize_scene_memory_with_llm(
        scene,
        messages,
        llm_client=client,
        compression_strategy="fast",
        genre_mode="slice_of_life",
        prompt_settings={
            "compression_scene_base": "CUSTOM BASE",
            "compression_source_scope_rules": "CUSTOM SCOPE",
            "compression_fast_strategy": "CUSTOM FAST STRATEGY",
        },
    )

    system_prompt = client.calls[0]["messages"][0]["content"]
    assert "CUSTOM BASE" in system_prompt
    assert "CUSTOM SCOPE" in system_prompt
    assert "CUSTOM FAST STRATEGY" in system_prompt
    assert "Fast compression: coverage" not in system_prompt


@pytest.mark.asyncio
async def test_quality_strategy_calls_extraction_critic_final_once_and_validates_final_arc():
    scene = SceneState(conversation_id="conv_quality", summary="[Rolling Story Arc]\n- 전체 이전 Arc")
    messages = [Message(id="msg_quality", conversation_id=scene.conversation_id, speaker_type="character", speaker_id="char_a", content="마지막 공개 대사")]
    final_arc = valid_arc("품질")
    client = SequenceClient([
        json.dumps(extraction_payload(), ensure_ascii=False),
        json.dumps(critic_payload(), ensure_ascii=False),
        final_arc,
    ])

    result = await conversation_service.summarize_scene_memory_with_llm(
        scene, messages, llm_client=client, compression_strategy="quality"
    )

    assert result == final_arc
    assert len(client.calls) == 3
    assert client.calls[0]["response_format"]["name"] == "compression_continuity_extraction"
    assert client.calls[1]["response_format"]["name"] == "compression_independent_critic"
    assert client.calls[2]["response_format"] is None
    assert "message_id=msg_quality" in client.calls[0]["messages"][1]["content"]
    assert "SOURCE EVIDENCE" in client.calls[1]["messages"][1]["content"]
    assert "INDEPENDENT CRITIC" in client.calls[2]["messages"][1]["content"]
    assert [item["metadata_updates"]["compression_stage"] for item in client.outcomes] == [
        "structured_extraction", "independent_critic", "final_synthesis"
    ]


@pytest.mark.asyncio
async def test_quality_fallback_reruns_whole_selected_strategy(session):
    conversation = Conversation(id="conv_quality_fallback", title="quality fallback", mode="user_character")
    messages = [Message(
        id=f"msg_quality_fallback_{index:02d}",
        conversation_id=conversation.id,
        speaker_type="character",
        speaker_id="char_a",
        content=f"압축 대상 {index}",
    ) for index in range(13)]
    old_arc = "[Rolling Story Arc]\n- 기존 사건"
    scene = SceneState(conversation_id=conversation.id, summary=old_arc)
    session.add_all([conversation, *messages, scene])
    session.commit()
    primary = SequenceClient(["malformed extraction"])
    final_arc = valid_arc("폴백")
    fallback = SequenceClient([
        json.dumps(extraction_payload(), ensure_ascii=False),
        json.dumps(critic_payload(), ensure_ascii=False),
        final_arc,
    ])

    result = await conversation_service.update_scene_orchestration_summary(
        session,
        conversation.id,
        messages,
        llm_client=primary,
        fallback_llm_client=fallback,
        character_ids=["char_a"],
        compression_strategy="quality",
    )

    assert len(primary.calls) == 1
    assert len(fallback.calls) == 3
    assert result.summary == final_arc
    assert result.compression_revision == 1
    assert result.last_compression_source_message_id == messages[0].id


@pytest.mark.asyncio
async def test_quality_failure_preserves_previous_applied_state(session):
    conversation = Conversation(id="conv_quality_preserve", title="quality preserve", mode="user_character")
    previous = Message(id="msg_quality_boundary", conversation_id=conversation.id, speaker_type="character", speaker_id="char_a", content="기존 경계")
    messages = [Message(
        id=f"msg_quality_preserve_{index:02d}", conversation_id=conversation.id,
        speaker_type="character", speaker_id="char_a", content=f"새 압축 대상 {index}"
    ) for index in range(13)]
    old_arc = "[Rolling Story Arc]\n- 절대 보존할 기존 사건"
    scene = SceneState(
        conversation_id=conversation.id,
        summary=old_arc,
        compression_revision=4,
        last_compression_source_message_id=previous.id,
    )
    old_timestamp = scene.last_compressed_at
    session.add_all([conversation, previous, *messages, scene])
    session.commit()
    primary = SequenceClient(["bad"])
    fallback = SequenceClient(["also bad"])

    result = await conversation_service.update_scene_orchestration_summary(
        session, conversation.id, [previous, *messages], llm_client=primary,
        fallback_llm_client=fallback, character_ids=["char_a"], compression_strategy="quality"
    )

    assert result.summary == old_arc
    assert result.compression_revision == 4
    assert result.last_compression_source_message_id == previous.id
    assert result.last_compressed_at == old_timestamp
    assert "primary" in result.last_compression_error
    assert "fallback" in result.last_compression_error
