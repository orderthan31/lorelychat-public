from app.db.models import BattleMatchRecord, CharacterMemory, ConversationRelationshipState, Message, SceneState
from app.core.config import get_settings


def test_context_preview_reports_prompt_sections_and_selected_history(client, session):
    character = client.post("/characters", json={
        "name": "세아",
        "description": "도장깨기 참가자",
        "persona": "승부 흐름을 또렷하게 잡는다.",
        "behavior_style": "불필요한 설명보다 압박과 반응을 중시한다.",
    }).json()
    room = client.post("/conversations", json={
        "mode": "user_character",
        "participants": [
            {"type": "user", "id": "user_001", "order_index": 0},
            {"type": "character", "id": character["id"], "order_index": 1},
        ],
        "scene": {
            "location": "체육관",
            "mood": "긴장",
            "world_seed": "서열전 세계관",
            "compression_focus": "서열 장부와 확정 사건만 보존",
        },
    }).json()
    scene = session.get(SceneState, room["id"])
    scene.summary = "[Scene memory compact]\nCurrent situation: 대결 직전"
    session.add(CharacterMemory(
        id="mem_context_preview",
        conversation_id=room["id"],
        character_id="__room__",
        memory_type="user_note",
        content="세계관 규칙: 서열전 승패는 명시된 판정만 장부에 기록된다.",
        importance=5,
    ))
    session.add(ConversationRelationshipState(
        conversation_id=room["id"],
        character_id=character["id"],
        counterpart_type="user",
        counterpart_id="user_001",
        trust_level=1,
        current_dynamic="세아는 사용자의 판정을 공식 진행 신호로 신뢰한다.",
        unresolved_hooks=["다음 판정 대기"],
    ))
    for idx in range(20):
        session.add(Message(
            id=f"msg_preview_{idx}",
            conversation_id=room["id"],
            speaker_type="user" if idx in {0, 5, 10, 15} else "character",
            speaker_id="user_001" if idx in {0, 5, 10, 15} else character["id"],
            content=f"최근 대화 {idx}",
            thought=f"비공개 생각 {idx}",
        ))
    session.commit()

    response = client.get(f"/conversations/{room['id']}/context-preview")

    assert response.status_code == 200
    body = response.json()
    assert body["conversation_id"] == room["id"]
    assert body["recent_message_count"] == 20
    assert body["selected_recent_message_count"] <= 10
    assert body["model_key"] is None
    keys = {section["key"]: section for section in body["sections"]}
    for key in [
        "character_cards",
        "scene",
        "genre_mode",
        "room_memory",
        "recent_messages",
        "directive",
        "output_rules",
    ]:
        assert key in keys
        assert keys[key]["approx_tokens"] >= 0
        assert keys[key]["source"]
        assert keys[key]["included_reason"]
        assert keys[key]["budget_tokens"] > 0
    assert "서열전 세계관" in keys["scene"]["content"]
    assert "세아" in keys["character_cards"]["content"]
    assert "세계관 규칙" in keys["room_memory"]["content"]
    assert "Keep total replies between 2 and 3" in keys["output_rules"]["content"]
    assert "at most 120 characters" in keys["output_rules"]["content"]
    assert "visible action is at most 80 characters" not in keys["output_rules"]["content"]
    assert "Keep dialogue, action, and thought in separate JSON fields" in keys["output_rules"]["content"]
    assert "Never use Markdown markers to encode whether text is dialogue, action, or thought" in keys["output_rules"]["content"]
    assert "runtime_settings" not in keys
    assert "relationship_state" not in keys
    assert "external_memory_qc" not in keys
    assert "counterpart_cards" not in keys
    assert "최근 메시지 선택량이 많음" in body["warnings"]
    assert "Gemini chat_generation 사용 중" not in body["warnings"]


def test_context_preview_separates_durable_story_arc_from_live_raw_tail(client, session):
    reika = client.post("/characters", json={"name": "에코", "persona": "침착한 선수"}).json()
    yiju = client.post("/characters", json={"name": "이주", "persona": "도전적인 선수"}).json()
    room = client.post("/conversations", json={
        "mode": "character_character",
        "genre_mode": "battle",
        "participants": [
            {"type": "character", "id": reika["id"], "order_index": 0},
            {"type": "character", "id": yiju["id"], "order_index": 1},
        ],
        "scene": {"world_seed": "리그방", "current_conflict": "3경기 후 리뷰 및 다음 매치업 지목"},
    }).json()
    scene = session.get(SceneState, room["id"])
    scene.summary = "[Rolling Story Arc]\n- 에코와 이주는 이전 경기 리뷰를 마치고 다음 매치업을 준비했다."
    scene.last_event = "오래된 이벤트"
    session.add(BattleMatchRecord(
        id="match_preview_live_scene",
        conversation_id=room["id"],
        genre_mode="battle",
        matchup_key="reika_vs_yiju",
        participant_a_id=reika["id"],
        participant_b_id=yiju["id"],
        result_status="in_progress",
        process_summary="에코와 이주가 현재 12경기를 준비한다.",
        metadata_={"match_order": 12, "current_phase": "prelude"},
    ))
    session.add(Message(
        id="msg_preview_live_latest",
        conversation_id=room["id"],
        speaker_type="user",
        speaker_id="user_001",
        content="현재 두 선수 서로에게 한마디.",
    ))
    session.commit()

    response = client.get(f"/conversations/{room['id']}/context-preview")

    assert response.status_code == 200
    sections = {section["key"]: section for section in response.json()["sections"]}
    scene_content = sections["scene"]["content"]
    assert "이전 경기 리뷰를 마치고 다음 매치업을 준비했다" in scene_content
    assert "오래된 이벤트" not in scene_content
    assert "에코 vs 이주" not in scene_content
    assert "현재 두 선수 서로에게 한마디" not in scene_content
    current_input = sections["current_user_input"]["content"]
    assert "현재 두 선수 서로에게 한마디" in current_input
    assert "recent_messages" not in sections
    output_rules = sections["output_rules"]["content"]
    assert "Keep total replies between 2 and 3" in output_rules
    assert "Length preset: medium. Return 2-3 distinct reply bubbles" in output_rules
    assert "1-3 natural sentences" in output_rules
    assert "Available output tokens are a ceiling" not in output_rules
    assert "Target around" not in output_rules


def test_context_preview_missing_room_returns_404(client):
    response = client.get("/conversations/conv_missing/context-preview")

    assert response.status_code == 404


def test_automatic_context_preview_reports_dangling_boundary_as_conflict(
    client,
    session,
    monkeypatch,
):
    character = client.post(
        "/characters",
        json={"name": "세아", "persona": "침착하다."},
    ).json()
    room = client.post(
        "/conversations",
        json={
            "mode": "user_character",
            "participants": [
                {"type": "user", "id": "user_001"},
                {"type": "character", "id": character["id"]},
            ],
        },
    ).json()
    scene = SceneState(
        conversation_id=room["id"],
        last_compression_source_message_id="msg_missing_boundary",
        summary="[Rolling Story Arc]\n- 이전 사건",
    )
    session.add(scene)
    session.add(
        Message(
            id="msg_after_missing_boundary",
            conversation_id=room["id"],
            speaker_type="user",
            speaker_id="user_001",
            content="현재 대화",
        )
    )
    session.commit()

    monkeypatch.setenv("CONTEXT_MANAGEMENT_MODE", "automatic")
    get_settings.cache_clear()
    try:
        response = client.get(f"/conversations/{room['id']}/context-preview")
    finally:
        get_settings.cache_clear()

    assert response.status_code == 409
    assert response.json()["detail"] == {"code": "context_coverage_gap"}


def test_context_preview_explains_prompt_sections_and_flags_duplicates(client, session):
    first = client.post("/characters", json={"name": "아린", "persona": "리그 참가자", "description": "리그 참가자"}).json()
    second = client.post("/characters", json={"name": "도희", "persona": "리그 참가자", "description": "리그 참가자"}).json()
    room = client.post("/conversations", json={
        "mode": "character_character",
        "genre_mode": "battle",
        "participants": [
            {"type": "character", "id": first["id"], "order_index": 0},
            {"type": "character", "id": second["id"], "order_index": 1},
        ],
        "scene": {"world_seed": "리그방"},
    }).json()

    response = client.get(f"/conversations/{room['id']}/context-preview")

    assert response.status_code == 200
    body = response.json()
    sections = {section["key"]: section for section in body["sections"]}
    assert "character_cards" in sections
    assert "counterpart_cards" not in sections
    assert "context_section_purpose" not in sections
    assert "relationship_state" not in sections
    assert "external_memory_qc" not in sections
    assert "counterpart_cards_duplicate_preview" not in body["warnings"]


def test_context_preview_filters_noisy_relationship_dialogue_from_prompt_sections(client, session):
    first = client.post("/characters", json={"name": "아린", "persona": "리그 참가자", "description": "리그 참가자"}).json()
    second = client.post("/characters", json={"name": "미오", "persona": "리그 참가자", "description": "리그 참가자"}).json()
    room = client.post("/conversations", json={
        "mode": "character_character",
        "genre_mode": "battle",
        "participants": [
            {"type": "character", "id": first["id"], "order_index": 0},
            {"type": "character", "id": second["id"], "order_index": 1},
        ],
    }).json()
    session.add(ConversationRelationshipState(
        conversation_id=room["id"],
        character_id=first["id"],
        counterpart_type="character",
        counterpart_id=second["id"],
        affinity_level=5,
        current_dynamic="하앙! 그대로 대사를 이어가며 action=허리를 비튼다",
        unresolved_hooks=["하앙! 그대로 눌러줘 dialogue=이전 대사"],
    ))
    session.commit()

    response = client.get(f"/conversations/{room['id']}/context-preview")

    assert response.status_code == 200
    sections = {section["key"]: section for section in response.json()["sections"]}
    relationship_content = sections.get("relationship_state_by_character", {}).get("content", "")
    assert "하앙" not in relationship_content
    assert "relationship_state" not in sections
    assert "noisy_relationship_state" not in response.json()["warnings"]


def test_relationship_fact_filter_does_not_block_general_relationship_words():
    from app.services import conversation_service

    fact = "서로의 감각과 거리감을 잘 읽는 경쟁적 협력 관계."

    assert conversation_service.relationship_fact_for_display(fact) == fact


def test_context_preview_compacts_silent_character_card_without_relationship_context(client, session):
    speaker = client.post("/characters", json={
        "name": "발화자",
        "description": "말하는 캐릭터",
        "persona": "발화자의 긴 페르소나",
        "speech_style": "발화자 말투 예시",
    }).json()
    silent = client.post("/characters", json={
        "name": "침묵자",
        "description": "방 안에 있지만 말하지 않는 캐릭터",
        "persona": "침묵자의 아주 긴 비밀 페르소나가 여기에 들어간다.",
        "speech_style": "침묵자가 직접 말할 때 쓰는 말투 예시",
    }).json()
    room = client.post("/conversations", json={
        "mode": "character_character",
        "genre_mode": "battle",
        "participants": [
            {"type": "character", "id": speaker["id"], "order_index": 0, "role": "primary"},
            {"type": "character", "id": silent["id"], "order_index": 1, "role": "silent"},
        ],
        "scene": {"world_seed": "silent prompt test"},
    }).json()
    session.add(ConversationRelationshipState(
        conversation_id=room["id"],
        character_id=silent["id"],
        counterpart_type="character",
        counterpart_id=speaker["id"],
        current_dynamic="침묵자는 발화자의 선택을 조용히 관찰한다.",
    ))
    session.commit()

    response = client.get(f"/conversations/{room['id']}/context-preview")

    assert response.status_code == 200
    sections = {section["key"]: section for section in response.json()["sections"]}
    character_cards = sections["character_cards"]["content"]
    output_rules = sections["output_rules"]["content"]

    assert "침묵자" in character_cards
    assert "role=silent/context-only" in character_cards
    assert "침묵자의 아주 긴 비밀 페르소나" not in character_cards
    assert "침묵자가 직접 말할 때 쓰는 말투 예시" not in character_cards
    allowed_ids = output_rules.split("Allowed character IDs:", 1)[1].split("\n", 1)[0]
    assert silent["id"] not in allowed_ids
    assert "relationship_state_by_character" not in sections
