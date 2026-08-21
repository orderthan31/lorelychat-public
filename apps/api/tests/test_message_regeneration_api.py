from sqlmodel import select

from app.db.models import Message


def test_regenerate_after_message_appends_generated_reply_without_deleting_existing(client, session):
    character = client.post("/characters", json={"name": "아리아", "persona": "사용자를 챙긴다."}).json()
    room = client.post("/conversations", json={
        "mode": "user_character",
        "participants": [
            {"type": "user", "id": "user_001", "order_index": 0},
            {"type": "character", "id": character["id"], "order_index": 1},
        ],
    }).json()
    first_turn = client.post(f"/conversations/{room['id']}/messages", json={
        "speaker_type": "user",
        "speaker_id": "user_001",
        "content": "처음 입력",
    }).json()
    selected_user_message = first_turn[0]
    existing_character_message = first_turn[1]
    session.add(Message(
        id="msg_later_user",
        conversation_id=room["id"],
        speaker_type="user",
        speaker_id="user_001",
        content="이후에 붙은 메시지",
    ))
    session.commit()

    response = client.post(
        f"/conversations/{room['id']}/messages/{selected_user_message['id']}/regenerate",
        json={"replace_existing": False},
    )

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 2
    assert [message["speaker_type"] for message in body] == ["character", "character"]
    assert all(message["speaker_id"] == character["id"] for message in body)
    assert all(message["id"] != existing_character_message["id"] for message in body)
    assert all("처음 입력" in message["content"] for message in body)
    all_messages = session.exec(select(Message).where(Message.conversation_id == room["id"])).all()
    ids = {message.id for message in all_messages}
    assert selected_user_message["id"] in ids
    assert existing_character_message["id"] in ids
    assert "msg_later_user" in ids
    assert {message["id"] for message in body} <= ids


def test_regenerate_selected_character_message_appends_alternative_in_one_to_one_room(client):
    character = client.post("/characters", json={"name": "아리아", "persona": "사용자를 챙긴다."}).json()
    room = client.post("/conversations", json={
        "mode": "user_character",
        "participants": [
            {"type": "user", "id": "user_001", "order_index": 0},
            {"type": "character", "id": character["id"], "order_index": 1},
        ],
    }).json()
    first_turn = client.post(f"/conversations/{room['id']}/messages", json={
        "speaker_type": "user",
        "speaker_id": "user_001",
        "content": "이 답변을 다시 뽑아줘",
    }).json()

    response = client.post(
        f"/conversations/{room['id']}/messages/{first_turn[1]['id']}/regenerate",
        json={"replace_existing": False},
    )

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 2
    assert [message["speaker_type"] for message in body] == ["character", "character"]
    assert all(message["id"] != first_turn[1]["id"] for message in body)
    assert all("이 답변을 다시 뽑아줘" in message["content"] for message in body)


def test_regenerate_missing_conversation_or_message_returns_404(client):
    response = client.post("/conversations/conv_missing/messages/msg_missing/regenerate", json={})
    assert response.status_code == 404

    character = client.post("/characters", json={"name": "아리아", "persona": "사용자를 챙긴다."}).json()
    room = client.post("/conversations", json={
        "mode": "user_character",
        "participants": [{"type": "character", "id": character["id"], "order_index": 0}],
    }).json()
    response = client.post(f"/conversations/{room['id']}/messages/msg_missing/regenerate", json={})
    assert response.status_code == 404


def test_regenerate_rejects_replace_existing_for_append_only_safety(client):
    character = client.post("/characters", json={"name": "아리아", "persona": "사용자를 챙긴다."}).json()
    room = client.post("/conversations", json={
        "mode": "user_character",
        "participants": [{"type": "character", "id": character["id"], "order_index": 0}],
    }).json()
    turn = client.post(f"/conversations/{room['id']}/messages", json={
        "speaker_type": "user",
        "speaker_id": "user_001",
        "content": "재생성 기준",
    }).json()

    response = client.post(
        f"/conversations/{room['id']}/messages/{turn[0]['id']}/regenerate",
        json={"replace_existing": True},
    )

    assert response.status_code == 422
