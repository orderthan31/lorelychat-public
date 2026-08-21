from app.db.models import CharacterMemory, Conversation, Message, SceneState


def test_conversation_genre_mode_round_trips_through_api(client, session):
    room = client.post("/conversations", json={
        "mode": "user_character",
        "genre_mode": "romance",
        "participants": [{"type": "user", "id": "user_001", "order_index": 0}],
        "scene": {"world_seed": "학교 선후배 감정선"},
    }).json()

    assert room["genre_mode"] == "romance"
    stored = session.get(Conversation, room["id"])
    assert stored.genre_mode == "romance"

    patched = client.patch(f"/conversations/{room['id']}", json={"genre_mode": "slice_of_life"}).json()

    assert patched["genre_mode"] == "slice_of_life"
    session.refresh(stored)
    assert stored.genre_mode == "slice_of_life"


def test_prompt_genre_policy_is_available_in_context_preview(client):
    room = client.post("/conversations", json={
        "mode": "user_character",
        "genre_mode": "fantasy",
        "participants": [{"type": "user", "id": "user_001", "order_index": 0}],
        "scene": {"world_seed": "왕국 원정"},
    }).json()

    response = client.get(f"/conversations/{room['id']}/context-preview")

    assert response.status_code == 200
    sections = {section["key"]: section for section in response.json()["sections"]}
    assert "genre_mode" in sections
    assert "Genre mode: fantasy" in sections["genre_mode"]["content"]
    assert "quest progress" in sections["genre_mode"]["content"]


def test_external_memory_default_noop_and_ledger_skip_metadata():
    from app.services import external_memory_service

    regular_memory = CharacterMemory(
        id="mem_regular",
        conversation_id="conv_1",
        character_id="char_1",
        memory_type="fact",
        content="다나는 비 오는 날의 약속을 중요하게 여긴다.",
        importance=4,
    )
    metadata = external_memory_service.build_memory_metadata(
        regular_memory,
        genre_mode="romance",
    )

    assert metadata["app_id"] == "lorechat"
    assert metadata["conversation_id"] == "conv_1"
    assert metadata["character_id"] == "char_1"
    assert metadata["genre_mode"] == "romance"
    assert metadata["local_memory_id"] == "mem_regular"
    assert metadata["is_authoritative_domain_state"] is False

    authoritative_memory = CharacterMemory(
        id="mem_ledger",
        conversation_id="conv_1",
        character_id="__room__",
        memory_type="fact",
        content="현재 공개된 공식 서열: 1위 솔, 2위 라나.",
        importance=5,
    )
    assert external_memory_service.should_sync_memory_to_external(authoritative_memory) is False

    battle_result_memory = CharacterMemory(
        id="mem_battle_result",
        conversation_id="conv_1",
        character_id="char_1",
        memory_type="event",
        content="로아이 6경기에서 베라를 상대로 승리해 승점 3점을 확보했다.",
        importance=5,
    )
    battle_decision = external_memory_service.explain_local_memory_sync(battle_result_memory, genre_mode="battle")
    assert battle_decision.should_sync is False
    assert battle_decision.reason == "battle_result_or_score_belongs_to_ledger"


def test_message_window_recall_card_metadata_and_sync_marker(session, monkeypatch):
    from app.core.config import get_settings
    from app.services import external_memory_service

    monkeypatch.setenv("MEM0_ENABLED", "true")
    monkeypatch.setenv("MEM0_WRITE_ENABLED", "true")
    monkeypatch.setenv("MEMORY_PROVIDER", "mem0")
    get_settings.cache_clear()

    conversation = Conversation(id="conv_window", title="리그방", mode="character_character", genre_mode="battle")
    session.add(conversation)
    first = Message(id="msg_1", conversation_id="conv_window", speaker_type="user", speaker_id="user_001", content="8경기 시작합니다")
    second = Message(id="msg_2", conversation_id="conv_window", speaker_type="character", speaker_id="char_a", content="상대 리듬을 보겠습니다")
    session.add(first)
    session.add(second)
    session.commit()

    added = {}

    class FakeProvider:
        def add_memory(self, content, *, metadata):
            added["content"] = content
            added["metadata"] = metadata
            return "mem0_window_1"

    monkeypatch.setattr(external_memory_service, "get_provider", lambda settings=None: FakeProvider())

    memory_id = external_memory_service.sync_message_window_to_external(
        session,
        conversation,
        [first, second],
        genre_mode="battle",
    )

    assert memory_id == "mem0_window_1"
    assert added["metadata"]["source"] == "auto_message_window"
    assert added["metadata"]["memory_type"] == "message_window_recall"
    assert added["metadata"]["message_start_id"] == "msg_1"
    assert added["metadata"]["message_end_id"] == "msg_2"
    session.refresh(second)
    assert second.metadata_[external_memory_service.MESSAGE_WINDOW_INGEST_KEY]["memory_id"] == "mem0_window_1"

    get_settings.cache_clear()


def test_external_memory_sync_decision_explains_durable_and_skipped_memories():
    from app.services import external_memory_service

    durable_memory = CharacterMemory(
        id="mem_durable",
        conversation_id="conv_1",
        character_id="char_1",
        memory_type="fact",
        content="다나는 비 오는 날의 약속을 중요하게 여긴다.",
        importance=4,
    )
    raw_memory = CharacterMemory(
        id="mem_raw",
        conversation_id="conv_1",
        character_id="char_1",
        memory_type="turn",
        content="speaker_id=char_1 dialogue=오늘은 그냥 지나갈게 action=고개를 돌림",
        importance=5,
    )
    low_memory = CharacterMemory(
        id="mem_low",
        conversation_id="conv_1",
        character_id="char_1",
        memory_type="fact",
        content="잠깐 지나간 농담",
        importance=2,
    )

    durable = external_memory_service.explain_local_memory_sync(durable_memory, genre_mode="romance")
    raw = external_memory_service.explain_local_memory_sync(raw_memory, genre_mode="romance")
    low = external_memory_service.explain_local_memory_sync(low_memory, genre_mode="romance")

    assert durable.should_sync is True
    assert durable.reason == "syncable"
    assert durable.missing_metadata == []
    assert durable.metadata["conversation_id"] == "conv_1"
    assert durable.metadata["genre_mode"] == "romance"
    assert durable.metadata["local_memory_id"] == "mem_durable"

    assert raw.should_sync is False
    assert raw.reason == "raw_dialogue_marker"
    assert low.should_sync is False
    assert low.reason == "low_importance"


def test_context_preview_excludes_external_memory_qc_section(client, session):
    character = client.post("/characters", json={
        "name": "차다나",
        "description": "학교 일상과 감정선을 가진 캐릭터",
        "persona": "겉으론 세지만 가까운 약속을 오래 기억한다.",
    }).json()
    room = client.post("/conversations", json={
        "mode": "user_character",
        "genre_mode": "romance",
        "participants": [
            {"type": "user", "id": "user_001", "order_index": 0},
            {"type": "character", "id": character["id"], "order_index": 1},
        ],
        "scene": {"world_seed": "비 오는 학교 복도"},
    }).json()
    session.add(CharacterMemory(
        id="mem_qc_durable",
        conversation_id=room["id"],
        character_id=character["id"],
        memory_type="user_note",
        content="다나는 비 오는 날의 약속을 중요하게 여긴다.",
        importance=4,
    ))
    session.add(CharacterMemory(
        id="mem_qc_raw",
        conversation_id=room["id"],
        character_id=character["id"],
        memory_type="turn",
        content="speaker_id=char_1 dialogue=그냥 지나갈게 action=시선을 피함",
        importance=5,
    ))
    session.commit()

    response = client.get(f"/conversations/{room['id']}/context-preview")

    assert response.status_code == 200
    sections = {section["key"]: section for section in response.json()["sections"]}
    assert "external_memory_qc" not in sections
    assert "continuity_state_by_character" in sections
    assert "다나는 비 오는 날의 약속" in sections["continuity_state_by_character"]["content"]
    assert "speaker_id=" not in sections["continuity_state_by_character"]["content"]


def test_context_preview_uses_local_db_memory_instead_of_mem0_runtime_recall(client, session, monkeypatch):
    from app.core.config import get_settings
    from app.db.models import CharacterMemory
    from app.services import external_memory_service

    monkeypatch.setenv("MEM0_ENABLED", "true")
    monkeypatch.setenv("MEM0_READ_ENABLED", "true")
    monkeypatch.setenv("MEMORY_PROVIDER", "mem0")
    get_settings.cache_clear()

    character = client.post("/characters", json={
        "name": "차다나",
        "description": "학교 일상과 감정선을 가진 캐릭터",
        "persona": "겉으론 세지만 가까운 약속을 오래 기억한다.",
    }).json()
    room = client.post("/conversations", json={
        "mode": "user_character",
        "genre_mode": "romance",
        "participants": [
            {"type": "user", "id": "user_001", "order_index": 0},
            {"type": "character", "id": character["id"], "order_index": 1},
        ],
        "scene": {"world_seed": "비 오는 학교 복도"},
    }).json()
    scene = session.get(SceneState, room["id"])
    scene.summary = "[Scene memory compact]\nCurrent situation: 복도에서 마주침"
    session.add(scene)
    session.add(CharacterMemory(
        id="mem_local_promise",
        conversation_id=room["id"],
        character_id=character["id"],
        memory_type="user_note",
        content="다나는 비 오는 날의 약속을 중요하게 여긴다.",
        importance=5,
    ))
    session.commit()

    captured = {"called": False}

    class FakeProvider:
        def search(self, query, *, filters, limit):
            captured["called"] = True
            return [external_memory_service.ExternalMemoryItem(
                content="mem0 쪽 기억은 런타임 프롬프트에 들어가면 안 된다.",
                score=0.91,
                metadata={
                    "app_id": "lorechat",
                    "conversation_id": room["id"],
                    "character_id": character["id"],
                    "genre_mode": "romance",
                    "memory_type": "fact",
                    "importance": 4,
                },
            )]

    monkeypatch.setattr(external_memory_service, "get_provider", lambda settings=None: FakeProvider())

    response = client.get(f"/conversations/{room['id']}/context-preview")

    assert response.status_code == 200
    sections = {section["key"]: section for section in response.json()["sections"]}
    assert "external_memory_by_character" not in sections
    assert "continuity_state_by_character" in sections
    assert "다나는 비 오는 날의 약속" in sections["continuity_state_by_character"]["content"]
    assert "Retrieved semantic memory" not in sections["continuity_state_by_character"]["content"]
    assert captured["called"] is False

    get_settings.cache_clear()


def test_external_memory_prompt_context_filters_generic_index_cards():
    from app.services import external_memory_service

    items = [
        external_memory_service.ExternalMemoryItem(
            content="League standings index recall card. conversation_id=conv_1 genre_mode=battle. Query aliases...",
            metadata={"memory_type": "league_index_recall", "source": "league_index_rechunk_v1"},
        ),
        external_memory_service.ExternalMemoryItem(
            content="루나은 개막전에서 미아 미오를 상대로 공식 승리를 거뒀다.",
            metadata={"memory_type": "match_result_recall", "source": "battle_ledger_rechunk_v1", "importance": 5},
        ),
    ]

    formatted = external_memory_service.format_external_memory_context(items)

    assert "League standings index recall card" not in formatted
    assert "루나은 개막전" in formatted


def test_context_preview_never_shows_qc_only_external_memory_report(client, session):
    character = client.post("/characters", json={"name": "차다나", "persona": "리그 참가자"}).json()
    room = client.post("/conversations", json={
        "mode": "user_character",
        "genre_mode": "battle",
        "participants": [
            {"type": "user", "id": "user_001", "order_index": 0},
            {"type": "character", "id": character["id"], "order_index": 1},
        ],
    }).json()

    response = client.get(f"/conversations/{room['id']}/context-preview")

    assert response.status_code == 200
    sections = {section["key"]: section for section in response.json()["sections"]}
    assert "external_memory_qc" not in sections
    assert all("prompt_injection=no" not in section["content"] for section in sections.values())
