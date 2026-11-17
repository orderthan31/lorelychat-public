"""Manual memory context coverage retained after per-turn extraction retirement."""

from app.db.models import Character, CharacterMemory, Conversation, ConversationParticipant, Message
from app.services.conversation_service import (
    GLOBAL_CHARACTER_MEMORY_CONVERSATION_ID,
    list_continuity_memories_for_character,
)


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
