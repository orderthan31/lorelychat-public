from app.db.models import Message, SceneState


VALID_ARC = """[Rolling Story Arc]
- 사용자와 아리아는 압축 결과를 직접 검토하고 수정하기로 합의했다.
- 수정된 사건 기록은 다음 대화의 연속성 기준으로 사용된다."""


def create_room(client, title="수정 API 테스트"):
    response = client.post("/conversations", json={
        "mode": "user_character",
        "title": title,
        "participants": [{"type": "user", "id": "user_001"}],
        "scene": {"world_seed": "수동 수정 검증"},
    })
    assert response.status_code == 200
    return response.json()


def test_manual_scene_summary_update_is_revision_guarded_and_preserves_boundary(client, session):
    room = create_room(client)
    scene = session.get(SceneState, room["id"])
    scene.summary = "[Rolling Story Arc]\n- 이전 사건"
    scene.compression_revision = 7
    scene.last_compression_source_message_id = "msg_boundary"
    scene.last_compression_error = "old error"
    session.add(scene)
    session.commit()

    response = client.patch(f"/conversations/{room['id']}/scene-summary", json={
        "summary": VALID_ARC,
        "expected_revision": 7,
    })

    assert response.status_code == 200
    body = response.json()
    assert body["summary"] == VALID_ARC
    assert body["compression_revision"] == 8
    assert body["last_compression_source_message_id"] == "msg_boundary"
    assert body["last_compression_error"] is None

    stale = client.patch(f"/conversations/{room['id']}/scene-summary", json={
        "summary": VALID_ARC,
        "expected_revision": 7,
    })
    assert stale.status_code == 409


def test_manual_scene_summary_update_rejects_invalid_arc(client):
    room = create_room(client)

    response = client.patch(f"/conversations/{room['id']}/scene-summary", json={
        "summary": "그냥 자유 형식 요약",
        "expected_revision": 0,
    })

    assert response.status_code == 422
    assert "Rolling Story Arc" in response.json()["detail"]


def test_message_bubble_update_syncs_render_parts_and_invalidates_tts(client, session):
    room = create_room(client)
    message = Message(
        id="msg_manual_edit",
        conversation_id=room["id"],
        speaker_type="character",
        speaker_id="char_aria",
        content="수정 전 대사",
        action="수정 전 행동",
        thought="수정 전 생각",
        emotion="calm",
        metadata_={
            "render_parts": [{"type": "dialogue", "text": "수정 전 대사"}],
            "command_blocks": [{"type": "text", "text": "보존할 블록"}],
            "tts_audio_url": "/uploads/old.wav",
            "tts_text": "수정 전 대사",
            "tts_provider": "test",
        },
    )
    session.add(message)
    session.commit()

    response = client.patch(f"/conversations/{room['id']}/messages/{message.id}", json={
        "content": "수정한 대사",
        "action": "고개를 끄덕인다",
        "thought": "이제 맞다",
        "emotion": "relieved",
    })

    assert response.status_code == 200
    body = response.json()
    assert body["content"] == "수정한 대사"
    assert body["action"] == "고개를 끄덕인다"
    assert body["thought"] == "이제 맞다"
    assert body["emotion"] == "relieved"
    assert body["metadata"]["render_parts"] == [
        {"type": "action", "text": "고개를 끄덕인다"},
        {"type": "dialogue", "text": "수정한 대사"},
        {"type": "thought", "text": "이제 맞다"},
    ]
    assert body["metadata"]["command_blocks"] == [{"type": "text", "text": "보존할 블록"}]
    assert body["metadata"]["manual_edit"]["fields"] == ["action", "content", "emotion", "thought"]
    assert body["tts_audio_url"] is None
    assert body["tts_text"] is None


def test_message_bubble_update_rejects_empty_or_cross_room_edit(client, session):
    room = create_room(client, "원본 방")
    other_room = create_room(client, "다른 방")
    message = Message(
        id="msg_edit_scope",
        conversation_id=room["id"],
        speaker_type="user",
        speaker_id="user_001",
        content="남겨둘 대사",
    )
    session.add(message)
    session.commit()

    empty = client.patch(f"/conversations/{room['id']}/messages/{message.id}", json={
        "content": "",
        "action": "",
        "thought": "",
    })
    assert empty.status_code == 422

    wrong_room = client.patch(f"/conversations/{other_room['id']}/messages/{message.id}", json={
        "content": "다른 방에서 수정 시도",
    })
    assert wrong_room.status_code == 404
