from app.core.config import get_settings
from app.db.models import Message


def test_automatic_compression_preview_selects_nothing_at_low_pressure(
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
    for index in range(4):
        session.add(Message(
            id=f"msg_low_preview_{index}",
            conversation_id=room["id"],
            speaker_type="user" if index % 2 == 0 else "character",
            speaker_id="user_001" if index % 2 == 0 else character["id"],
            content=f"짧은 대화 {index}",
        ))
    session.commit()

    monkeypatch.setenv("CONTEXT_MANAGEMENT_MODE", "automatic")
    get_settings.cache_clear()
    try:
        response = client.get(f"/conversations/{room['id']}/compression-preview")
    finally:
        get_settings.cache_clear()

    assert response.status_code == 200
    assert response.json()["selected_recent_message_count"] == 0
