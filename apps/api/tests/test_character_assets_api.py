from app.db.models import Character


def create_character(client, name="Asset Test"):
    response = client.post("/characters", json={"name": name, "persona": "테스트 캐릭터", "avatar_url": "/uploads/avatars/base.png"})
    assert response.status_code == 200
    return response.json()


def test_character_asset_crud_and_default_avatar(client):
    character = create_character(client)
    create = client.post(f"/characters/{character['id']}/assets", json={
        "label": "웃는 얼굴",
        "description": "밝게 웃는 상반신 이미지",
        "image_url": "/uploads/character-assets/happy.png",
        "tags": ["daily"],
        "mood_tags": ["happy"],
        "expression_tags": ["soft_smile"],
        "priority": 80,
        "enabled": True,
    })
    assert create.status_code == 200, create.text
    asset = create.json()
    assert asset["character_id"] == character["id"]
    assert asset["mood_tags"] == ["happy"]

    listed = client.get(f"/characters/{character['id']}/assets").json()
    assert any(item["id"] == asset["id"] for item in listed)

    patched = client.patch(f"/character-assets/{asset['id']}", json={"label": "환한 미소", "priority": 95})
    assert patched.status_code == 200
    assert patched.json()["label"] == "환한 미소"
    assert patched.json()["priority"] == 95

    defaulted = client.post(f"/character-assets/{asset['id']}/set-default-avatar")
    assert defaulted.status_code == 200
    assert defaulted.json()["is_default"] is True
    updated_character = client.get(f"/characters/{character['id']}").json()
    assert updated_character["avatar_url"] == "/uploads/character-assets/happy.png"

    deleted = client.delete(f"/character-assets/{asset['id']}")
    assert deleted.status_code == 204
    assert client.get(f"/character-assets/{asset['id']}").status_code == 404


def test_existing_avatar_is_migrated_to_default_asset(session):
    from app.services.asset_service import migrate_existing_avatars, list_assets

    character = Character(id="char_existing", name="기존", persona="테스트", avatar_url="/uploads/avatars/existing.png")
    session.add(character)
    session.commit()

    created = migrate_existing_avatars(session)
    assert created == 1
    assets = list_assets(session, character.id)
    assert len(assets) == 1
    assert assets[0].label == "기본 프사"
    assert assets[0].is_default is True
    assert assets[0].image_url == "/uploads/avatars/existing.png"
