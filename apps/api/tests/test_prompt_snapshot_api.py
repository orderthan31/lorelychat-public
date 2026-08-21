from sqlmodel import select

from app.db.models import LLMPromptSnapshot


def test_post_message_persists_prompt_snapshot_for_generation(client, session):
    aria = client.post("/characters", json={"name": "아리아", "persona": "사용자를 챙긴다."}).json()
    luna = client.post("/characters", json={"name": "루나", "persona": "밝게 반응한다."}).json()
    room = client.post("/conversations", json={
        "mode": "character_character",
        "participants": [
            {"type": "character", "id": aria["id"], "order_index": 0},
            {"type": "character", "id": luna["id"], "order_index": 1},
        ],
        "scene": {"world_seed": "테스트 방"},
    }).json()

    response = client.post(f"/conversations/{room['id']}/messages", json={
        "speaker_type": "user",
        "speaker_id": "user_001",
        "content": "다음 대화 이어줘.",
    })

    assert response.status_code == 200
    snapshots = session.exec(select(LLMPromptSnapshot).where(LLMPromptSnapshot.conversation_id == room["id"])).all()
    assert len(snapshots) == 1
    snapshot = snapshots[0]
    assert snapshot.source_message_id
    assert snapshot.messages_json[0]["role"] == "system"
    assert snapshot.messages_json[1] == {"role": "user", "content": "[User dialogue]\n다음 대화 이어줘."}
    assert snapshot.compiled_text == snapshot.messages_json[0]["content"]
    assert snapshot.ledger_json
    assert "generate" in snapshot.pipeline_steps


def test_post_character_message_persists_character_turn_as_assistant_role(client, session):
    aria = client.post("/characters", json={"name": "아리아", "persona": "사용자를 챙긴다."}).json()
    luna = client.post("/characters", json={"name": "루나", "persona": "밝게 반응한다."}).json()
    room = client.post("/conversations", json={
        "mode": "character_character",
        "participants": [
            {"type": "character", "id": aria["id"], "order_index": 0},
            {"type": "character", "id": luna["id"], "order_index": 1},
        ],
    }).json()

    response = client.post(f"/conversations/{room['id']}/messages", json={
        "speaker_type": "character",
        "speaker_id": aria["id"],
        "content": "내가 먼저 말했어.",
    })

    assert response.status_code == 200
    snapshot = session.exec(
        select(LLMPromptSnapshot).where(LLMPromptSnapshot.conversation_id == room["id"])
    ).one()
    assert [message["role"] for message in snapshot.messages_json] == ["system", "assistant", "user"]
    assert snapshot.messages_json[1]["content"] == f"[Character dialogue: 아리아({aria['id']})]\n내가 먼저 말했어."
    assert "not by the human user" in snapshot.messages_json[2]["content"]
    assert "human user did not speak or act in this turn" in snapshot.messages_json[2]["content"]
