from app.db.models import PostCommitTask, SceneState
from sqlmodel import select
from app.schemas.dialogue import CharacterReply


async def fake_room_replies(self, **kwargs):
    characters = kwargs["characters"]
    return [
        CharacterReply(character_id=characters[0].id, text="아리아 한마디", action="고개를 든다"),
        CharacterReply(reply_type="storytelling", text="공기가 잠깐 부드럽게 가라앉는다."),
        CharacterReply(character_id=characters[1].id, text="루나 한마디"),
        CharacterReply(character_id=characters[0].id, text="아리아 추가 버블"),
    ]


async def fake_six_replies(self, **kwargs):
    characters = kwargs["characters"]
    return [CharacterReply(character_id=characters[index % len(characters)].id, text=f"버블 {index}") for index in range(6)]


def create_character_room(client):
    aria = client.post("/characters", json={"name": "아리아", "persona": "차분하고 다정하다."}).json()
    luna = client.post("/characters", json={"name": "루나", "persona": "밝고 야무지다."}).json()
    conv = client.post("/conversations", json={
        "mode": "character_character",
        "participants": [
            {"type": "character", "id": aria["id"], "order_index": 0},
            {"type": "character", "id": luna["id"], "order_index": 1},
        ],
        "scene": {"location": "카페", "mood": "가벼운 수다", "current_conflict": "대화 흐름 잡기"},
    }).json()
    return aria, luna, conv


def test_character_room_generates_multiple_bubbles_in_one_message_request(client, monkeypatch):
    from app.engine.character_runtime import CharacterRuntime

    monkeypatch.setattr(CharacterRuntime, "generate_replies", fake_room_replies)
    aria, luna, conv = create_character_room(client)

    response = client.post(f"/conversations/{conv['id']}/messages", json={
        "speaker_type": "user",
        "speaker_id": "user_001",
        "content": "*손을 흔든다* 둘이 짧게 얘기해봐",
    })

    assert response.status_code == 200
    messages = response.json()
    assert len(messages) == 5
    assert messages[0]["speaker_type"] == "user"
    assert messages[0]["action"] == "손을 흔든다"
    assert [m["speaker_type"] for m in messages[1:]] == ["character", "storytelling", "character", "character"]
    assert [m["speaker_id"] for m in messages[1:]] == [aria["id"], "storyteller", luna["id"], aria["id"]]
    assert messages[1]["action"] == "고개를 든다"
    assert messages[2]["content"] == "공기가 잠깐 부드럽게 가라앉는다."

    stored = client.get(f"/conversations/{conv['id']}/messages").json()
    assert len(stored) == 5

    context = client.get(f"/conversations/{conv['id']}/context").json()
    relationships = context["relationships"]
    assert relationships == []


def test_character_character_conversation_uses_default_title_when_blank(client):
    aria = client.post("/characters", json={"name": "아리아", "persona": "차분하고 다정하다."}).json()
    luna = client.post("/characters", json={"name": "루나", "persona": "밝고 야무지다."}).json()

    response = client.post("/conversations", json={
        "mode": "character_character",
        "title": "   ",
        "participants": [
            {"type": "character", "id": aria["id"], "order_index": 0},
            {"type": "character", "id": luna["id"], "order_index": 1},
        ],
    })

    assert response.status_code == 200
    assert response.json()["title"] == "아리아-루나 자동대화"


def test_auto_turn_endpoints_are_removed(client):
    _, _, conv = create_character_room(client)

    post_response = client.post(f"/conversations/{conv['id']}/auto-turns", json={"seed_message": "시작", "max_turns": 2})
    stream_response = client.get(f"/conversations/{conv['id']}/auto-turns/stream?seed_message=시작&max_turns=2")

    assert post_response.status_code == 404
    assert stream_response.status_code == 404


def test_orchestration_summary_waits_for_interval_instead_of_first_turn_fallback(client, session, monkeypatch):
    from app.engine.character_runtime import CharacterRuntime

    monkeypatch.setattr(CharacterRuntime, "generate_replies", fake_six_replies)
    _, _, conv = create_character_room(client)

    response = client.post(f"/conversations/{conv['id']}/messages", json={
        "speaker_type": "user",
        "speaker_id": "user_001",
        "content": "시작",
    })

    assert response.status_code == 200
    assert len(response.json()) == 7
    session.expire_all()
    scene = session.get(SceneState, conv["id"])
    assert scene.summary is None


def test_post_message_enqueues_due_compression_after_reply_commit(client, session, monkeypatch):
    from app.api import conversations as conversation_api
    from app.engine.character_runtime import CharacterRuntime

    monkeypatch.setattr(CharacterRuntime, "generate_replies", fake_six_replies)
    monkeypatch.setattr(conversation_api.conversation_service, "should_update_scene_orchestration_summary", lambda *args, **kwargs: True)
    _, _, conv = create_character_room(client)

    response = client.post(f"/conversations/{conv['id']}/messages", json={
        "speaker_type": "system",
        "speaker_id": "system",
        "content": "테스트 장면 지시",
    })

    assert response.status_code == 200
    body = response.json()
    assert body[0]["speaker_type"] == "system"
    assert body[0]["content"] == "테스트 장면 지시"
    assert len(body) == 7
    session.expire_all()
    compression_tasks = session.exec(
        select(PostCommitTask).where(PostCommitTask.task_type == "compression")
    ).all()
    assert len(compression_tasks) == 1
    assert compression_tasks[0].status == "queued"
    assert compression_tasks[0].payload_["conversation_id"] == conv["id"]
    assert compression_tasks[0].payload_["generated_character_count"] == 6

    stored = client.get(f"/conversations/{conv['id']}/messages").json()
    assert any(item["speaker_type"] == "system" and item["content"] == "테스트 장면 지시" for item in stored)
    assert sum(1 for item in stored if item["speaker_type"] == "character") == 6
