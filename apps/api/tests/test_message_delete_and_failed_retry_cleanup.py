from datetime import datetime, timedelta, timezone
from sqlmodel import select

from app.db.models import CharacterMemory, Message, MessageAsset, SceneState
from app.engine.llm_client import LLMUnavailableError


def test_failed_generation_keeps_incoming_message_with_error_metadata(client, session, monkeypatch):
    character = client.post("/characters", json={"name": "아리아", "persona": "사용자를 챙긴다."}).json()
    room = client.post("/conversations", json={
        "mode": "user_character",
        "participants": [
            {"type": "user", "id": "user_001", "order_index": 0},
            {"type": "character", "id": character["id"], "order_index": 1},
        ],
    }).json()

    async def boom(*args, **kwargs):
        raise LLMUnavailableError("Gemini endpoint unavailable: HTTP 429 credits depleted")

    monkeypatch.setattr("app.engine.character_runtime.CharacterRuntime.generate_replies", boom)

    response = client.post(f"/conversations/{room['id']}/messages", json={
        "speaker_type": "user",
        "speaker_id": "user_001",
        "content": "실패해도 이 입력은 reload 후 보여야 함",
    })

    assert response.status_code == 503
    assert "Gemini endpoint unavailable" in response.json()["detail"]
    messages = client.get(f"/conversations/{room['id']}/messages").json()
    assert len(messages) == 1
    assert messages[0]["speaker_type"] == "user"
    assert messages[0]["content"] == "실패해도 이 입력은 reload 후 보여야 함"
    saved = session.get(Message, messages[0]["id"])
    assert saved is not None
    assert saved.metadata_["generation_status"] == "failed"
    assert saved.metadata_["generation_error_type"] == "LLMUnavailableError"
    assert "credits depleted" in saved.metadata_["generation_error_message"]


def test_delete_message_removes_message_and_asset_links(client, session):
    character = client.post("/characters", json={"name": "아리아", "persona": "사용자를 챙긴다."}).json()
    room = client.post("/conversations", json={
        "mode": "user_character",
        "participants": [
            {"type": "user", "id": "user_001", "order_index": 0},
            {"type": "character", "id": character["id"], "order_index": 1},
        ],
    }).json()
    turn = client.post(f"/conversations/{room['id']}/messages", json={
        "speaker_type": "user",
        "speaker_id": "user_001",
        "content": "삭제 테스트",
    }).json()
    target_id = turn[0]["id"]
    session.add(MessageAsset(id="msgasset_delete_test", message_id=target_id, asset_id="asset_missing_for_link", display_order=0))
    session.commit()

    response = client.delete(f"/conversations/{room['id']}/messages/{target_id}")
    assert response.status_code == 204
    assert session.get(Message, target_id) is None
    assert list(session.exec(select(MessageAsset).where(MessageAsset.message_id == target_id)).all()) == []
    remaining = client.get(f"/conversations/{room['id']}/messages").json()
    assert all(message["id"] != target_id for message in remaining)


def test_delete_message_missing_returns_404(client):
    response = client.delete("/conversations/conv_missing/messages/msg_missing")
    assert response.status_code == 404


def test_bulk_delete_messages_removes_multiple_messages_and_asset_links(client, session):
    character = client.post("/characters", json={"name": "아리아", "persona": "사용자를 챙긴다."}).json()
    room = client.post("/conversations", json={
        "mode": "user_character",
        "participants": [
            {"type": "user", "id": "user_001", "order_index": 0},
            {"type": "character", "id": character["id"], "order_index": 1},
        ],
    }).json()
    base = datetime(2026, 5, 19, 0, 0, tzinfo=timezone.utc)
    for index, message_id in enumerate(["bulk_1", "bulk_2", "bulk_3"]):
        session.add(Message(
            id=message_id,
            conversation_id=room["id"],
            speaker_type="user" if index % 2 == 0 else "character",
            speaker_id="user_001" if index % 2 == 0 else character["id"],
            content=f"bulk {index}",
            created_at=base + timedelta(seconds=index),
        ))
    session.add(MessageAsset(id="msgasset_bulk_delete_test", message_id="bulk_2", asset_id="asset_missing_for_bulk_link", display_order=0))
    session.commit()

    response = client.post(f"/conversations/{room['id']}/messages/bulk-delete", json={"message_ids": ["bulk_1", "bulk_2", "bulk_missing"]})
    assert response.status_code == 200
    assert response.json() == {"deleted_ids": ["bulk_1", "bulk_2"], "missing_ids": ["bulk_missing"]}
    assert session.get(Message, "bulk_1") is None
    assert session.get(Message, "bulk_2") is None
    assert session.get(Message, "bulk_3") is not None
    assert list(session.exec(select(MessageAsset).where(MessageAsset.message_id == "bulk_2")).all()) == []


def test_manual_compress_now_updates_scene_state(client, monkeypatch):
    character = client.post("/characters", json={"name": "아리아", "persona": "사용자를 챙긴다."}).json()
    room = client.post("/conversations", json={
        "mode": "user_character",
        "participants": [
            {"type": "user", "id": "user_001", "order_index": 0},
            {"type": "character", "id": character["id"], "order_index": 1},
        ],
    }).json()

    async def fake_update(session, conversation_id, recent_messages, llm_client=None, fallback_llm_client=None, character_ids=None):
        scene = session.get(SceneState, conversation_id) or SceneState(conversation_id=conversation_id)
        scene.summary = "수동 압축 테스트 요약"
        scene.last_event = "manual compress"
        session.add(scene)
        session.commit()
        session.refresh(scene)
        return scene

    monkeypatch.setattr("app.services.conversation_service.update_scene_orchestration_summary", fake_update)
    response = client.post(f"/conversations/{room['id']}/compress-now")
    assert response.status_code == 200
    data = response.json()
    assert data["summary"] == "수동 압축 테스트 요약"
    assert data["last_event"] == "manual compress"


def test_user_memory_create_update_and_delete(client, session):
    character = client.post("/characters", json={"name": "아리아", "persona": "사용자를 챙긴다."}).json()
    room = client.post("/conversations", json={
        "mode": "user_character",
        "participants": [
            {"type": "user", "id": "user_001", "order_index": 0},
            {"type": "character", "id": character["id"], "order_index": 1},
        ],
    }).json()
    response = client.post(f"/conversations/{room['id']}/memories", json={
        "character_id": "__room__",
        "memory_type": "fact",
        "content": "사용자가 직접 고정한 유저노트",
        "importance": 5,
    })
    assert response.status_code == 200
    memory = response.json()
    assert memory["character_id"] == "__room__"
    assert memory["memory_type"] == "user_note"
    assert memory["content"] == "사용자가 직접 고정한 유저노트"

    update_response = client.patch(f"/conversations/{room['id']}/memories/{memory['id']}", json={
        "character_id": character["id"],
        "memory_type": "preference",
        "content": "수정된 캐릭터별 유저노트",
        "importance": 4,
    })
    assert update_response.status_code == 200
    updated = update_response.json()
    assert updated["id"] == memory["id"]
    assert updated["character_id"] == character["id"]
    assert updated["memory_type"] == "user_note"
    assert updated["content"] == "수정된 캐릭터별 유저노트"
    assert updated["importance"] == 4

    session.add(CharacterMemory(
        id="mem_legacy_auto_fact",
        conversation_id=room["id"],
        character_id=character["id"],
        memory_type="fact",
        content="예전 자동 fact 메모리는 유저노트 목록에 보이면 안 된다.",
        importance=5,
    ))
    session.commit()

    context = client.get(f"/conversations/{room['id']}/context").json()
    assert any(item["id"] == memory["id"] and item["content"] == "수정된 캐릭터별 유저노트" for item in context["memories"])
    assert all(item["memory_type"] == "user_note" for item in context["memories"])
    assert all(item["id"] != "mem_legacy_auto_fact" for item in context["memories"])

    delete_response = client.delete(f"/conversations/{room['id']}/memories/{memory['id']}")
    assert delete_response.status_code == 204
    assert session.get(CharacterMemory, memory["id"]) is None


def test_list_messages_recent_turns_and_before_id_returns_reverse_chat_pages(client, session):
    character = client.post("/characters", json={"name": "아리아", "persona": "사용자를 챙긴다."}).json()
    room = client.post("/conversations", json={
        "mode": "user_character",
        "participants": [
            {"type": "user", "id": "user_001", "order_index": 0},
            {"type": "character", "id": character["id"], "order_index": 1},
        ],
    }).json()
    base = datetime(2026, 5, 19, 0, 0, tzinfo=timezone.utc)
    rows = [
        ("m1", "user", "user_001", "u1"),
        ("m2", "character", character["id"], "c1"),
        ("m3", "character", character["id"], "c2"),
        ("m4", "user", "user_001", "u2"),
        ("m5", "character", character["id"], "c3"),
        ("m6", "system", "system", "s3"),
        ("m7", "character", character["id"], "c4"),
    ]
    for index, (message_id, speaker_type, speaker_id, content) in enumerate(rows):
        session.add(Message(
            id=message_id,
            conversation_id=room["id"],
            speaker_type=speaker_type,
            speaker_id=speaker_id,
            content=content,
            created_at=base + timedelta(seconds=index),
        ))
    session.commit()

    latest = client.get(f"/conversations/{room['id']}/messages?recent_turns=2").json()
    assert [message["id"] for message in latest] == ["m4", "m5", "m6", "m7"]

    older = client.get(f"/conversations/{room['id']}/messages?recent_turns=1&before_id=m4").json()
    assert [message["id"] for message in older] == ["m1", "m2", "m3"]

    missing = client.get(f"/conversations/{room['id']}/messages?recent_turns=1&before_id=m1").json()
    assert missing == []
