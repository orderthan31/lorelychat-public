from app.db.models import Character, CharacterMemory, Conversation, ConversationParticipant, Message
from app.services import domain_actor_service


def _add_character(session, character_id: str, name: str) -> Character:
    character = Character(id=character_id, name=name, persona=f"{name} persona")
    session.add(character)
    return character


def test_offstage_actor_resolver_uses_global_characters_not_only_room_participants(session):
    _add_character(session, "char_emily", "니아")
    _add_character(session, "char_suna", "이브")
    arin = _add_character(session, "char_arin", "루나")
    session.commit()

    actors = domain_actor_service.resolve_mentioned_actors(
        session,
        "이브는 루나가 예전에 보여준 압박 방식을 떠올렸다.",
        active_character_ids={"char_emily", "char_suna"},
    )

    offstage = [actor for actor in actors if not actor.on_stage]
    assert [actor.character.id for actor in offstage] == [arin.id]
    assert offstage[0].on_stage is False
    assert "루나" in offstage[0].matched_aliases


def test_offstage_actor_recall_context_includes_lightweight_memory_without_full_card(session):
    _add_character(session, "char_emily", "니아")
    _add_character(session, "char_suna", "이브")
    _add_character(session, "char_arin", "루나")
    conversation = Conversation(id="conv_offstage", title="league", mode="character_character", genre_mode="battle")
    session.add(conversation)
    session.add(ConversationParticipant(conversation_id="conv_offstage", participant_type="character", participant_id="char_emily", order_index=0))
    session.add(ConversationParticipant(conversation_id="conv_offstage", participant_type="character", participant_id="char_suna", order_index=1))
    session.add(CharacterMemory(
        id="mem_arin_pressure",
        conversation_id="conv_offstage",
        character_id="char_arin",
        memory_type="user_note",
        content="루나는 초반 압박을 버티다가 후반 반격 타이밍을 잡는 스타일로 기억된다.",
        importance=5,
    ))
    session.commit()

    context = domain_actor_service.build_offstage_actor_recall_context(
        session,
        conversation,
        text="이브는 루나가 예전에 보여준 압박 방식을 떠올리며 니아를 노려봤다.",
        active_character_ids={"char_emily", "char_suna"},
        genre_mode="battle",
        query="이브 루나 압박 방식 니아",
    )

    assert [actor.character.id for actor in context.actors] == ["char_arin"]
    assert "[Relevant off-stage actor recall]" in context.content
    assert "루나(char_arin)" in context.content
    assert "후반 반격" in context.content
    assert "do not make them speak" in context.content


def test_context_preview_shows_offstage_actor_recall_for_mentions_outside_participants(client, session):
    _add_character(session, "char_emily", "니아")
    _add_character(session, "char_suna", "이브")
    _add_character(session, "char_arin", "루나")
    session.add(Conversation(id="conv_preview_offstage", title="league", mode="character_character", genre_mode="battle"))
    session.add(ConversationParticipant(conversation_id="conv_preview_offstage", participant_type="character", participant_id="char_emily", order_index=0))
    session.add(ConversationParticipant(conversation_id="conv_preview_offstage", participant_type="character", participant_id="char_suna", order_index=1))
    session.add(CharacterMemory(
        id="mem_preview_arin",
        conversation_id="conv_preview_offstage",
        character_id="char_arin",
        memory_type="user_note",
        content="루나은 리그에서 침착한 후반 반격으로 알려져 있다.",
        importance=5,
    ))
    session.add(Message(
        id="msg_offstage_mention",
        conversation_id="conv_preview_offstage",
        speaker_type="user",
        speaker_id="user_001",
        content="이브는 루나의 후반 반격을 떠올리며 니아를 노려봤다.",
    ))
    session.commit()

    response = client.get("/conversations/conv_preview_offstage/context-preview")
    assert response.status_code == 200
    sections = {section["key"]: section for section in response.json()["sections"]}
    assert "offstage_actor_recall" in sections
    assert "루나(char_arin)" in sections["offstage_actor_recall"]["content"]
    assert "후반 반격" in sections["offstage_actor_recall"]["content"]
    assert "니아 persona" in sections["character_cards"]["content"]
    assert "이브 persona" in sections["character_cards"]["content"]
    assert "루나 persona" not in sections["character_cards"]["content"]
