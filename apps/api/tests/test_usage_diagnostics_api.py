from app.db.models import LLMUsageEvent


def _room(client):
    character = client.post("/characters", json={"name": "아리아", "persona": "차분하다."}).json()
    return client.post("/conversations", json={
        "mode": "user_character",
        "participants": [
            {"type": "user", "id": "user_001", "order_index": 0},
            {"type": "character", "id": character["id"], "order_index": 1},
        ],
    }).json()


def test_conversation_usage_summary_groups_by_provider_model_and_purpose(client, session):
    room = _room(client)
    session.add(LLMUsageEvent(
        id="usage_chat",
        provider="gemini",
        model="gemini-3-flash",
        purpose="chat_generation",
        conversation_id=room["id"],
        prompt_tokens=100,
        completion_tokens=30,
        total_tokens=130,
    ))
    session.add(LLMUsageEvent(
        id="usage_local_compression",
        provider="openai_compatible",
        model="local-gemma",
        purpose="conversation_compression",
        conversation_id=room["id"],
        prompt_tokens=None,
        completion_tokens=None,
        total_tokens=None,
        estimated=True,
    ))
    session.add(LLMUsageEvent(
        id="usage_gemini_compression",
        provider="gemini",
        model="gemini-3-flash",
        purpose="conversation_compression",
        conversation_id=room["id"],
        prompt_tokens=20,
        completion_tokens=10,
        total_tokens=30,
    ))
    session.add(LLMUsageEvent(
        id="usage_other_room",
        provider="gemini",
        model="gemini-3-flash",
        purpose="chat_generation",
        conversation_id="conv_other",
        prompt_tokens=999,
        completion_tokens=999,
        total_tokens=1998,
    ))
    session.commit()

    response = client.get(f"/conversations/{room['id']}/usage")

    assert response.status_code == 200
    body = response.json()
    assert body["conversation_id"] == room["id"]
    assert body["total_calls"] == 3
    assert body["prompt_tokens"] == 120
    assert body["completion_tokens"] == 40
    assert body["total_tokens"] == 160
    by_key = {(bucket["provider"], bucket["model"], bucket["purpose"]): bucket for bucket in body["buckets"]}
    assert by_key[("gemini", "gemini-3-flash", "chat_generation")]["calls"] == 1
    assert by_key[("gemini", "gemini-3-flash", "conversation_compression")]["total_tokens"] == 30
    assert by_key[("openai_compatible", "local-gemma", "conversation_compression")]["total_tokens"] == 0
    assert by_key[("openai_compatible", "local-gemma", "conversation_compression")]["estimated_calls"] == 1


def test_conversation_usage_missing_room_returns_404(client):
    response = client.get("/conversations/conv_missing/usage")

    assert response.status_code == 404
