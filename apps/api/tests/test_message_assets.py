from sqlmodel import select

from app.db.models import CharacterAsset, MessageAsset
from app.schemas.dialogue import CharacterReply


def test_character_reply_auto_attaches_matching_asset(client, monkeypatch):
    character = client.post("/characters", json={"name": "미소", "persona": "밝은 캐릭터"}).json()
    conversation = client.post("/conversations", json={
        "mode": "user_character",
        "participants": [{"type": "user", "id": "user_001", "order_index": 0}, {"type": "character", "id": character["id"], "order_index": 1}],
        "scene": {"location": "카페", "mood": "일상"},
    }).json()
    asset = client.post(f"/characters/{character['id']}/assets", json={
        "label": "웃는 얼굴",
        "image_url": "/uploads/character-assets/smile.png",
        "mood_tags": ["happy"],
        "expression_tags": ["soft_smile"],
        "scene_tags": ["cafe"],
        "outfit_tags": ["casual"],
        "pose_tags": ["upper_body"],
        "priority": 90,
        "enabled": True,
    }).json()

    async def fake_generate_replies(self, **kwargs):
        return [
            CharacterReply(character_id=character["id"], text="좋아, 같이 웃자.", emotion="happy", action="환하게 미소 짓는다."),
            CharacterReply(character_id=character["id"], text="이렇게 웃으면 됐지?", emotion="happy", action="눈꼬리를 부드럽게 휘어 웃는다."),
        ]

    monkeypatch.setattr("app.engine.character_runtime.CharacterRuntime.generate_replies", fake_generate_replies)
    response = client.post(f"/conversations/{conversation['id']}/messages", json={"speaker_type": "user", "speaker_id": "user_001", "content": "웃어줘"})
    assert response.status_code == 200, response.text
    messages = response.json()
    generated = messages[1]
    assert generated["assets"][0]["id"] == asset["id"]
    assert generated["assets"][0]["image_url"] == "/uploads/character-assets/smile.png"
    assert generated["assets"][0]["outfit_tags"] == ["casual"]
    assert generated["assets"][0]["pose_tags"] == ["upper_body"]

    listed = client.get(f"/conversations/{conversation['id']}/messages").json()
    assert listed[1]["assets"][0]["label"] == "웃는 얼굴"
