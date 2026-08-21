from sqlmodel import select

from app.db.models import CharacterMemory, ConversationRelationshipState
from app.schemas.dialogue import CharacterReply


def test_user_character_message_generates_mock_reply(client, monkeypatch):
    monkeypatch.setenv("LLM_MOCK", "true")
    char = client.post("/characters", json={"name": "아리아", "persona": "장난스럽다."}).json()
    conv = client.post("/conversations", json={
        "mode": "user_character",
        "participants": [
            {"type": "user", "id": "user_001"},
            {"type": "character", "id": char["id"]},
        ],
    }).json()

    response = client.post(f"/conversations/{conv['id']}/messages", json={
        "speaker_type": "user",
        "speaker_id": "user_001",
        "content": "오늘 좀 힘들었어.",
    })
    assert response.status_code == 200
    messages = response.json()
    assert len(messages) == 3
    assert messages[0]["speaker_type"] == "user"
    assert [message["speaker_type"] for message in messages[1:]] == ["character", "character"]
    assert all(message["speaker_id"] == char["id"] for message in messages[1:])

    stored = client.get(f"/conversations/{conv['id']}/messages").json()
    assert len(stored) == 3


def test_user_message_triple_dot_markup_splits_action_and_dialogue(client, monkeypatch):
    monkeypatch.setenv("LLM_MOCK", "true")
    char = client.post("/characters", json={"name": "아리아", "persona": "장난스럽다."}).json()
    conv = client.post("/conversations", json={
        "mode": "user_character",
        "participants": [
            {"type": "user", "id": "user_001"},
            {"type": "character", "id": char["id"]},
        ],
    }).json()

    response = client.post(f"/conversations/{conv['id']}/messages", json={
        "speaker_type": "user",
        "speaker_id": "user_001",
        "content": "*아리아 옆에 조용히 앉는다* 오늘 좀 지쳤어",
    })

    assert response.status_code == 200
    messages = response.json()
    assert messages[0]["speaker_type"] == "user"
    assert messages[0]["action"] == "아리아 옆에 조용히 앉는다"
    assert messages[0]["content"] == "오늘 좀 지쳤어"

    stored = client.get(f"/conversations/{conv['id']}/messages").json()
    assert stored[0]["action"] == "아리아 옆에 조용히 앉는다"
    assert stored[0]["content"] == "오늘 좀 지쳤어"


def test_user_message_triple_dot_markup_allows_action_only(client, monkeypatch):
    monkeypatch.setenv("LLM_MOCK", "true")
    char = client.post("/characters", json={"name": "아리아", "persona": "장난스럽다."}).json()
    conv = client.post("/conversations", json={
        "mode": "user_character",
        "participants": [
            {"type": "user", "id": "user_001"},
            {"type": "character", "id": char["id"]},
        ],
    }).json()

    response = client.post(f"/conversations/{conv['id']}/messages", json={
        "speaker_type": "user",
        "speaker_id": "user_001",
        "content": "*말없이 손을 잡는다*",
    })

    assert response.status_code == 200
    messages = response.json()
    assert messages[0]["action"] == "말없이 손을 잡는다"
    assert messages[0]["content"] == ""


def test_user_character_turn_creates_no_auto_memory_or_relationship_state(client, session, monkeypatch):
    monkeypatch.setenv("LLM_MOCK", "true")
    char = client.post("/characters", json={"name": "아리아", "persona": "장난스럽다."}).json()
    conv = client.post("/conversations", json={
        "mode": "user_character",
        "participants": [
            {"type": "user", "id": "user_001"},
            {"type": "character", "id": char["id"]},
        ],
    }).json()

    response = client.post(f"/conversations/{conv['id']}/messages", json={
        "speaker_type": "user",
        "speaker_id": "user_001",
        "content": "*조용히 옆에 앉는다* 오늘은 괜찮아. 고마워",
    })

    assert response.status_code == 200
    memories = list(session.exec(select(CharacterMemory).where(
        CharacterMemory.conversation_id == conv["id"],
        CharacterMemory.character_id == char["id"],
    )).all())
    assert memories == []

    state = session.get(ConversationRelationshipState, (conv["id"], char["id"], "user", "user_001"))
    assert state is None


def test_user_character_message_can_persist_multiple_bubbles_from_one_model_call(client, monkeypatch):
    async def fake_generate_replies(self, **kwargs):
        assert len(kwargs["characters"]) == 1
        return [
            CharacterReply(character_id=kwargs["characters"][0].id, text="첫 번째 답장", emotion="warm", action="손을 흔든다", thought="반갑다"),
            CharacterReply(character_id=kwargs["characters"][0].id, text="두 번째 답장", emotion="warm", action="웃는다", thought="조금 더 말하고 싶다"),
        ]

    monkeypatch.setenv("LLM_MOCK", "true")
    monkeypatch.setattr("app.engine.character_runtime.CharacterRuntime.generate_replies", fake_generate_replies)
    char = client.post("/characters", json={"name": "아리아", "persona": "장난스럽다."}).json()
    conv = client.post("/conversations", json={
        "mode": "user_character",
        "participants": [
            {"type": "user", "id": "user_001"},
            {"type": "character", "id": char["id"]},
        ],
    }).json()

    response = client.post(f"/conversations/{conv['id']}/messages", json={
        "speaker_type": "user",
        "speaker_id": "user_001",
        "content": "안녕",
    })

    assert response.status_code == 200
    messages = response.json()
    assert [message["content"] for message in messages] == ["안녕", "첫 번째 답장", "두 번째 답장"]
    assert messages[1]["speaker_id"] == char["id"]
    assert messages[2]["speaker_id"] == char["id"]


def test_message_generation_defers_database_writes_to_atomic_finalizer(client, monkeypatch):
    captured = {"atomic_boundary_seen": False}

    async def fake_generate_replies(self, **kwargs):
        assert len(kwargs["characters"]) == 1
        assert kwargs.get("persist_replies") is None
        captured["atomic_boundary_seen"] = True
        return [
            CharacterReply(character_id=kwargs["characters"][0].id, text="그래프 persist 첫 답장", emotion="warm", action="고개를 끄덕인다", thought="저장 경로를 확인한다"),
            CharacterReply(character_id=kwargs["characters"][0].id, text="그래프 persist 둘째 답장", emotion="warm", action="", thought=""),
        ]

    monkeypatch.setenv("LLM_MOCK", "true")
    monkeypatch.setattr("app.engine.character_runtime.CharacterRuntime.generate_replies", fake_generate_replies)
    char = client.post("/characters", json={"name": "아리아", "persona": "장난스럽다."}).json()
    conv = client.post("/conversations", json={
        "mode": "user_character",
        "participants": [
            {"type": "user", "id": "user_001"},
            {"type": "character", "id": char["id"]},
        ],
    }).json()

    response = client.post(f"/conversations/{conv['id']}/messages", json={
        "speaker_type": "user",
        "speaker_id": "user_001",
        "content": "안녕",
    })

    assert response.status_code == 200
    messages = response.json()
    assert captured["atomic_boundary_seen"] is True
    assert [message["content"] for message in messages] == ["안녕", "그래프 persist 첫 답장", "그래프 persist 둘째 답장"]
    stored = client.get(f"/conversations/{conv['id']}/messages").json()
    assert [message["content"] for message in stored] == ["안녕", "그래프 persist 첫 답장", "그래프 persist 둘째 답장"]
    assert stored[1]["action"] == "고개를 끄덕인다"


def test_multi_character_room_generates_all_character_bubbles_in_one_model_call(client, monkeypatch):
    calls = {"count": 0}

    async def fake_generate_replies(self, **kwargs):
        calls["count"] += 1
        assert kwargs["conversation_mode"] == "character_character"
        ids = [character.id for character in kwargs["characters"]]
        assert len(ids) == 2
        return [
            CharacterReply(character_id=ids[0], text="내가 먼저 말할게.", emotion="calm", action="시선을 든다", thought="흐름을 잡자"),
            CharacterReply(character_id=ids[1], text="응, 나도 이어갈게!", emotion="bright", action="고개를 끄덕인다", thought="재밌다"),
        ]

    monkeypatch.setenv("LLM_MOCK", "true")
    monkeypatch.setattr("app.engine.character_runtime.CharacterRuntime.generate_replies", fake_generate_replies)
    aria = client.post("/characters", json={"name": "아리아", "persona": "차분하다."}).json()
    luna = client.post("/characters", json={"name": "루나", "persona": "밝다."}).json()
    conv = client.post("/conversations", json={
        "mode": "character_character",
        "participants": [
            {"type": "character", "id": aria["id"], "order_index": 0},
            {"type": "character", "id": luna["id"], "order_index": 1},
        ],
    }).json()

    response = client.post(f"/conversations/{conv['id']}/messages", json={
        "speaker_type": "user",
        "speaker_id": "user_001",
        "content": "*둘을 바라본다* 같이 얘기해봐",
    })

    assert response.status_code == 200
    messages = response.json()
    assert calls["count"] == 1
    assert [message["speaker_type"] for message in messages] == ["user", "character", "character"]
    assert [message["speaker_id"] for message in messages[1:]] == [aria["id"], luna["id"]]
    assert messages[1]["content"] == "내가 먼저 말할게."
    assert messages[2]["content"] == "응, 나도 이어갈게!"
