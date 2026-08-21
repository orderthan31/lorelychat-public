import json

from sqlmodel import select

from app.db.models import Character, CharacterMemory, Conversation, ConversationParticipant, ConversationRelationshipState, Message
from app.engine.llm_client import LLMResponse, LLMUnavailableError
from app.services.conversation_service import (
    GLOBAL_CHARACTER_MEMORY_CONVERSATION_ID,
    extract_continuity_update_with_llm,
    list_continuity_memories_for_character,
    update_continuity_from_turn,
)


class FakeContinuityLLM:
    async def chat(self, messages, *, response_format=None):
        assert response_format == {"type": "json_object"}
        assert "generic continuity memory" in messages[0]["content"]
        assert "Newest turn" in messages[1]["content"]
        return LLMResponse(content=json.dumps({
            "memories_to_add": [
                {"memory_type": "preference", "content": "사용자는 장황한 설명보다 바로 이어지는 반응을 선호한다.", "importance": 4},
                {"memory_type": "open_hook", "content": "다음 턴에서 사용자의 피곤함을 계속 의식한다.", "importance": 3},
            ],
            "state_patch": {
                "trust_delta": 2,
                "affinity_delta": 1,
                "tension_delta": 0,
                "conflict_delta": 0,
                "cooperation_delta": 1,
                "current_mood": "차분함",
                "current_dynamic": "사용자가 피곤함을 드러냈고 캐릭터가 차분히 받쳐주는 흐름",
            },
            "unresolved_hooks": ["사용자의 피곤함을 다음 턴에서 이어받기"],
        }, ensure_ascii=False))


class FailingContinuityLLM:
    async def chat(self, messages, *, response_format=None):
        raise LLMUnavailableError("down")


def seed_turn(session):
    char = Character(id="char_aria", name="아리아", persona="다정하다")
    conv = Conversation(id="conv_1", mode="user_character", title="테스트")
    session.add(char)
    session.add(conv)
    session.add(ConversationParticipant(conversation_id=conv.id, participant_type="user", participant_id="user_001", order_index=0))
    session.add(ConversationParticipant(conversation_id=conv.id, participant_type="character", participant_id=char.id, order_index=1))
    user_msg = Message(
        id="msg_user",
        conversation_id=conv.id,
        speaker_type="user",
        speaker_id="user_001",
        content="오늘 너무 피곤해",
        action="소파에 앉는다",
    )
    char_msg = Message(
        id="msg_char",
        conversation_id=conv.id,
        speaker_type="character",
        speaker_id=char.id,
        content="말 길게 안 해도 돼. 여기 있을게.",
        emotion="차분함",
        action="옆에 앉는다",
    )
    session.add(user_msg)
    session.add(char_msg)
    session.commit()
    return conv, char, user_msg, char_msg


async def test_extract_continuity_update_with_llm_validates_json(session):
    conv, char, user_msg, char_msg = seed_turn(session)

    update = await extract_continuity_update_with_llm(
        character_id=char.id,
        existing_memories=[],
        relationship_state=None,
        user_message=user_msg,
        character_message=char_msg,
        llm_client=FakeContinuityLLM(),
    )

    assert update is not None
    assert update["memories_to_add"] == []
    assert update["state_patch"]["trust_delta"] == 2
    assert update["state_patch"]["current_mood"] == "차분함"
    assert update["unresolved_hooks"] == ["사용자의 피곤함을 다음 턴에서 이어받기"]


async def test_update_continuity_from_turn_updates_lightweight_state_without_llm_memory(session):
    conv, char, user_msg, char_msg = seed_turn(session)

    state = await update_continuity_from_turn(
        session,
        conversation_id=conv.id,
        character_id=char.id,
        user_message=user_msg,
        character_message=char_msg,
        llm_client=FakeContinuityLLM(),
    )

    memories = list(session.exec(select(CharacterMemory).where(CharacterMemory.conversation_id == conv.id)).all())
    assert memories == []
    assert state.affinity_level >= 1
    assert state.cooperation_level >= 1
    assert state.current_mood == "차분함"
    assert state.current_dynamic
    assert state.unresolved_hooks


async def test_update_continuity_from_turn_falls_back_when_llm_unavailable(session):
    conv, char, user_msg, char_msg = seed_turn(session)

    state = await update_continuity_from_turn(
        session,
        conversation_id=conv.id,
        character_id=char.id,
        user_message=user_msg,
        character_message=char_msg,
        llm_client=FailingContinuityLLM(),
    )

    memories = list(session.exec(select(CharacterMemory).where(CharacterMemory.conversation_id == conv.id)).all())
    assert memories == []
    assert state.affinity_level >= 1
    assert state.current_dynamic


def test_continuity_memories_include_global_character_memory(session):
    conv, char, _, _ = seed_turn(session)
    session.add(CharacterMemory(
        id="mem_global_character",
        conversation_id=GLOBAL_CHARACTER_MEMORY_CONVERSATION_ID,
        character_id=char.id,
        memory_type="user_note",
        content="아리아는 이전 방에서 사용자가 짧고 바로 이어지는 답을 선호한다는 것을 기억한다.",
        importance=5,
    ))
    session.add(CharacterMemory(
        id="mem_room_character",
        conversation_id=conv.id,
        character_id=char.id,
        memory_type="user_note",
        content="현재 방의 고정 설정: 사용자는 피곤한 상태에서 짧고 바로 이어지는 반응을 선호한다.",
        importance=5,
    ))
    session.add(CharacterMemory(
        id="mem_auto_fact_should_not_inject",
        conversation_id=conv.id,
        character_id=char.id,
        memory_type="fact",
        content="자동 압축이 만든 팩트는 프롬프트 장기기억으로 주입되면 안 된다.",
        importance=5,
    ))
    session.add(CharacterMemory(
        id="mem_global_room_should_not_leak",
        conversation_id=GLOBAL_CHARACTER_MEMORY_CONVERSATION_ID,
        character_id="__room__",
        memory_type="fact",
        content="다른 방의 방 공통 장부는 섞이면 안 된다.",
        importance=5,
    ))
    session.commit()

    memories = list_continuity_memories_for_character(session, conv.id, char.id)
    contents = [memory.content for memory in memories]

    assert any("이전 방" in content for content in contents)
    assert any("현재 방" in content for content in contents)
    assert all("자동 압축" not in content for content in contents)
    assert all("다른 방의 방 공통 장부" not in content for content in contents)
