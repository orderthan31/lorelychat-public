from sqlalchemy import text


def test_create_and_list_character(client):
    response = client.post("/characters", json={
        "name": "아리아",
        "persona": "장난스럽고 직설적이지만 속정이 깊다.",
        "appearance": "24세, 166cm",
        "behavior_style": "상대가 지치면 먼저 물을 건네고 거리를 좁힌다.",
        "speech_style": "짧고 자연스럽게 말한다.",
    })
    assert response.status_code == 200
    data = response.json()
    assert data["id"].startswith("char_")
    assert data["name"] == "아리아"
    assert data["appearance"] == "24세, 166cm"
    assert data["behavior_style"] == "상대가 지치면 먼저 물을 건네고 거리를 좁힌다."
    assert "다른 캐릭터의 대사를 대신 쓰지 않는다." in data["forbidden_rules"]

    list_response = client.get("/characters")
    assert list_response.status_code == 200
    assert len(list_response.json()) == 1


def test_update_character(client):
    created = client.post("/characters", json={"name": "루나", "persona": "밝고 야무지다."}).json()
    response = client.patch(f"/characters/{created['id']}", json={"behavior_style": "먼저 다가가고 상황을 빠르게 정리한다.", "speech_style": "발랄하고 짧게 말한다."})
    assert response.status_code == 200
    assert response.json()["behavior_style"] == "먼저 다가가고 상황을 빠르게 정리한다."
    assert response.json()["speech_style"] == "발랄하고 짧게 말한다."


def test_create_character_accepts_trait_scores(client):
    response = client.post("/characters", json={
        "name": "아리아",
        "persona": "장난스럽고 직설적이지만 속정이 깊다.",
        "trait_scores": {"confidence": 5, "eros": 3, "jealousy": 4, "playfulness": 5},
    })
    assert response.status_code == 200
    data = response.json()
    assert data["trait_scores"]["confidence"] == 5
    assert data["trait_scores"]["eros"] == 3
    assert data["trait_scores"]["jealousy"] == 4
    assert data["trait_scores"]["playfulness"] == 5


def test_character_trait_scores_must_be_one_to_five(client):
    response = client.post("/characters", json={
        "name": "아리아",
        "persona": "장난스럽고 직설적이지만 속정이 깊다.",
        "trait_scores": {"confidence": 6},
    })
    assert response.status_code == 422


def test_update_character_trait_scores(client):
    created = client.post("/characters", json={"name": "루나", "persona": "밝고 야무지다."}).json()
    response = client.patch(f"/characters/{created['id']}", json={"trait_scores": {"shyness": 2, "initiative": 5}})
    assert response.status_code == 200
    assert response.json()["trait_scores"]["shyness"] == 2
    assert response.json()["trait_scores"]["initiative"] == 5


def test_list_characters_repairs_legacy_null_trait_scores(client, session):
    created = client.post("/characters", json={"name": "기존캐릭터", "persona": "예전 데이터"}).json()
    session.execute(text("UPDATE characters SET trait_scores = NULL WHERE id = :id"), {"id": created["id"]})
    session.commit()

    response = client.get("/characters")

    assert response.status_code == 200
    legacy = next(item for item in response.json() if item["id"] == created["id"])
    assert legacy["trait_scores"]["confidence"] == 3
    assert legacy["trait_scores"]["initiative"] == 3
