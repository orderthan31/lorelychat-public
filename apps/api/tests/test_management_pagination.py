def test_characters_paginated_list_keeps_legacy_list_contract(client):
    for name in ["가람", "나래", "다온"]:
        response = client.post("/characters", json={"name": name, "persona": f"{name} 페르소나"})
        assert response.status_code == 200

    legacy = client.get("/characters")
    assert legacy.status_code == 200
    assert isinstance(legacy.json(), list)

    page = client.get("/characters?paginated=true&page=1&page_size=2")
    assert page.status_code == 200
    body = page.json()
    assert body["total"] == 3
    assert body["page"] == 1
    assert body["page_size"] == 2
    assert body["pages"] == 2
    assert len(body["items"]) == 2

    searched = client.get("/characters?paginated=true&q=나래")
    assert searched.status_code == 200
    assert searched.json()["total"] == 1
    assert searched.json()["items"][0]["name"] == "나래"


def test_world_settings_paginated_list_filters_query_and_disabled(client):
    active = client.post("/world-settings", json={"title": "학교 세계관", "world_seed": "교실과 복도", "enabled": True})
    assert active.status_code == 200
    hidden = client.post("/world-settings", json={"title": "비활성 세계관", "world_seed": "숨김", "enabled": False})
    assert hidden.status_code == 200

    visible_page = client.get("/world-settings?paginated=true&page=1&page_size=10")
    assert visible_page.status_code == 200
    assert visible_page.json()["total"] == 1
    assert visible_page.json()["items"][0]["title"] == "학교 세계관"

    all_page = client.get("/world-settings?paginated=true&include_disabled=true&q=세계관")
    assert all_page.status_code == 200
    assert all_page.json()["total"] == 2


def test_chat_commands_paginated_list_keeps_legacy_seed_contract(client):
    legacy = client.get("/chat-commands?include_disabled=true")
    assert legacy.status_code == 200
    assert isinstance(legacy.json(), list)
    assert any(item["name"] == "방송" for item in legacy.json())

    page = client.get("/chat-commands?paginated=true&include_disabled=true&page=1&page_size=3&q=방송")
    assert page.status_code == 200
    body = page.json()
    assert body["page"] == 1
    assert body["page_size"] == 3
    assert body["total"] >= 1
    assert 1 <= len(body["items"]) <= 3
    assert all("방송" in f"{item['name']} {item['description']} {item['prompt']}" for item in body["items"])
