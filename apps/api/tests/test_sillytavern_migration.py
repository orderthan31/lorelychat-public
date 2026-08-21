from datetime import datetime, timezone
import json

from app.db.models import (
    BattleMatchRecord,
    BattleStanding,
    Character,
    CharacterAsset,
    CharacterMemory,
    Conversation,
    ConversationParticipant,
    ConversationRelationshipState,

    Message,
    MessageAsset,
    SceneState,
    WorldSetting,
)
from app.interop.sillytavern_migration import export_bundle


def test_export_bundle_builds_st_card_chat_lorebook_and_asset(session, tmp_path):
    now = datetime(2026, 7, 16, tzinfo=timezone.utc)
    session.add(Character(
        id="char_aria",
        name="아리아",
        persona="사용자를 챙기는 후배",
        description="로어챗 캐릭터",
        appearance="검은 머리",
        behavior_style="다정하고 빠르게 정리한다",
        speech_style="친근한 반말",
        emotional_rules=["차갑게 말하지 않는다"],
        forbidden_rules=["사용자 행동을 대신 결정하지 않는다"],
        trait_scores={"kindness": 5},
        avatar_url="/uploads/avatars/aria.png",
        created_at=now,
        updated_at=now,
    ))
    session.add(CharacterAsset(
        id="asset_avatar",
        character_id="char_aria",
        label="기본 프사",
        image_url="/uploads/avatars/aria.png",
        is_default=True,
    ))
    session.add(WorldSetting(
        id="world_office",
        title="오피스",
        description="현대 회사",
        genre_mode="romance",
        location="사무실",
        mood="잔잔함",
        world_seed="두 사람은 같은 팀이다.",
        tags=["회사", "현대"],
        created_at=now,
        updated_at=now,
    ))

    session.add(Conversation(
        id="conv_one",
        title="아리아 대화방",
        mode="user_character",
        genre_mode="romance",
        world_setting_id="world_office",
        created_at=now,
        updated_at=now,
    ))
    session.add(ConversationParticipant(conversation_id="conv_one", participant_type="user", participant_id="user_001", order_index=0))
    session.add(ConversationParticipant(conversation_id="conv_one", participant_type="character", participant_id="char_aria", order_index=1))
    session.add(SceneState(conversation_id="conv_one", location="회의실", summary="프로젝트 회의를 마쳤다."))
    session.add(CharacterMemory(id="mem_one", conversation_id="conv_one", character_id="char_aria", memory_type="user_note", content="사용자는 진한 커피를 좋아한다.", importance=5))
    session.add(ConversationRelationshipState(
        conversation_id="conv_one",
        character_id="char_aria",
        counterpart_type="user",
        counterpart_id="user_001",
        trust_level=4,
        affinity_level=5,
        tension_level=1,
        conflict_level=0,
        cooperation_level=5,
        current_mood="편안함",
        current_dynamic="서로 신뢰함",
        unresolved_hooks=["주말 약속"],
    ))
    session.add(BattleMatchRecord(
        id="match_one",
        conversation_id="conv_one",
        matchup_key="aria-user",
        participant_a_id="char_aria",
        participant_b_id="user_001",
        result_status="completed",
        winner_id="char_aria",
    ))
    session.add(BattleStanding(conversation_id="conv_one", character_id="char_aria", wins=1, losses=0, points=3, rank=1))
    session.add(Message(
        id="msg_user",
        conversation_id="conv_one",
        speaker_type="user",
        speaker_id="user_001",
        content="회의 끝났어.",
        created_at=now,
    ))
    session.add(Message(
        id="msg_char",
        conversation_id="conv_one",
        speaker_type="character",
        speaker_id="char_aria",
        content="수고했어 사용자.",
        action="커피를 건넨다.",
        thought="피곤해 보인다.",
        emotion="caring",
        metadata_={
            "api_key": "must-not-leak",
            "render_parts": ["dialogue"],
            "command_blocks": [{"type": "note", "title": "알림", "text": "상태 갱신"}],
        },
        created_at=now,
    ))
    session.add(MessageAsset(id="ma_one", message_id="msg_char", asset_id="asset_avatar", display_order=0))
    session.commit()

    result = export_bundle(session, tmp_path)

    assert result["counts"] == {
        "characters": 1,
        "worlds": 1,
        "conversations": 1,
        "messages": 2,
        "assets": 1,
    }
    card = json.loads((tmp_path / "characters" / "char_aria.json").read_text())
    assert card["spec"] == "chara_card_v2"
    assert card["data"]["name"] == "아리아"
    assert card["data"]["extensions"]["lorechat"]["source_id"] == "char_aria"

    chat_lines = [json.loads(line) for line in (tmp_path / "chats" / "conv_one.jsonl").read_text().splitlines()]
    assert chat_lines[0]["chat_metadata"]["world_info"].startswith("Lorechat · 아리아 대화방")
    char_line = next(line for line in chat_lines[1:] if line["extra"]["lorechat"]["id"] == "msg_char")
    assert char_line["mes"] == "*커피를 건넨다.*\n\n수고했어 사용자.\n\n### 알림\n상태 갱신"
    assert char_line["extra"]["image"].startswith("/user/images/")
    assert char_line["extra"]["lorechat"]["thought"] == "피곤해 보인다."
    assert "must-not-leak" not in json.dumps(chat_lines, ensure_ascii=False)

    lorebook = json.loads((tmp_path / "worlds" / "conversation_conv_one.json").read_text())
    lore_text = "\n".join(entry["content"] for entry in lorebook["entries"].values())
    assert "프로젝트 회의를 마쳤다" in lore_text
    assert "사용자는 진한 커피를 좋아한다" in lore_text
    assert "서로 신뢰함" in lore_text
    assert "aria-user" in lore_text

    manifest = json.loads((tmp_path / "manifest.json").read_text())
    assert manifest["direction"] == "lorechat_to_sillytavern"
    assert manifest["storage_policy"] == "separate_stores_no_shared_database"
    assert manifest["characters"]["char_aria"]["preserved_name"] == "lorechat_char_aria"
