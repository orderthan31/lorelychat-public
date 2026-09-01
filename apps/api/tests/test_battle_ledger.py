from app.api import conversations as conversations_api
from app.db.models import BattleMatchRecord, BattleStanding, Character, Conversation, ConversationParticipant
from app.services import battle_ledger_service, conversation_service, genre_domain_service


def _add_character(session, character_id: str, name: str) -> Character:
    character = Character(id=character_id, name=name, persona=f"{name} persona")
    session.add(character)
    return character


def _add_room(session, conversation_id: str, *, genre_mode: str) -> Conversation:
    room = Conversation(id=conversation_id, title=f"{genre_mode} room", mode="character_character", genre_mode=genre_mode)
    session.add(room)
    session.add(ConversationParticipant(conversation_id=conversation_id, participant_type="character", participant_id="char_a", order_index=0))
    session.add(ConversationParticipant(conversation_id=conversation_id, participant_type="character", participant_id="char_b", order_index=1))
    session.commit()
    return room


def test_battle_compression_update_creates_match_record_standings_and_mem0_card_only_for_battle(session, monkeypatch):
    _add_character(session, "char_a", "루나")
    _add_character(session, "char_b", "미아 미오")
    _add_room(session, "conv_battle", genre_mode="battle")
    captured_cards = []
    monkeypatch.setattr(battle_ledger_service.external_memory_service, "sync_battle_memory_card_to_external", lambda card, genre_mode="battle": captured_cards.append((card, genre_mode)))

    conversation_service.apply_conversation_compression_update(session, "conv_battle", {
        "scene": {},
        "memories": [],
        "relationships": [],
        "battle_events": [{
            "event_type": "match_result",
            "matchup_key": "park_arin_vs_sakura_mio",
            "participant_a_id": "char_a",
            "participant_b_id": "char_b",
            "winner_id": "char_a",
            "loser_id": "char_b",
            "result_status": "completed",
            "process_summary": "루나은 초반 압박을 버티고 후반 반격으로 승리했다.",
            "decisive_moment": "후반 반격 KO",
            "source_message_start_id": "msg_001",
            "source_message_end_id": "msg_132",
        }],
    })

    records = battle_ledger_service.list_match_records(session, "conv_battle")
    assert len(records) == 1
    record = records[0]
    assert record.genre_mode == "battle"
    assert record.matchup_key == "park_arin_vs_sakura_mio"
    assert record.winner_id == "char_a"
    assert record.loser_id == "char_b"
    assert "후반 반격" in record.process_summary

    winner = session.get(BattleStanding, ("conv_battle", "char_a"))
    loser = session.get(BattleStanding, ("conv_battle", "char_b"))
    assert winner.wins == 1
    assert winner.losses == 0
    assert winner.points == 3
    assert loser.wins == 0
    assert loser.losses == 1
    assert loser.points == 0

    assert len(captured_cards) == 1
    card, genre_mode = captured_cards[0]
    assert genre_mode == "battle"
    assert card["metadata"]["matchup_key"] == "park_arin_vs_sakura_mio"
    assert card["metadata"]["winner_id"] == "char_a"
    assert "루나" in card["content"]
    assert "미아 미오" in card["content"]


def test_battle_ledger_skips_non_battle_conversations(session, monkeypatch):
    _add_character(session, "char_a", "차다나")
    _add_character(session, "char_b", "최도아")
    _add_room(session, "conv_romance", genre_mode="romance")
    captured_cards = []
    monkeypatch.setattr(battle_ledger_service.external_memory_service, "sync_battle_memory_card_to_external", lambda card, genre_mode="battle": captured_cards.append((card, genre_mode)))

    conversation_service.apply_conversation_compression_update(session, "conv_romance", {
        "scene": {},
        "memories": [],
        "relationships": [],
        "battle_events": [{
            "event_type": "match_result",
            "matchup_key": "seoyoon_vs_doa",
            "participant_a_id": "char_a",
            "participant_b_id": "char_b",
            "winner_id": "char_a",
            "loser_id": "char_b",
            "process_summary": "연애방에서는 이 배틀 장부가 작동하면 안 된다.",
        }],
    })

    assert battle_ledger_service.list_match_records(session, "conv_romance") == []
    assert session.get(BattleStanding, ("conv_romance", "char_a")) is None
    assert captured_cards == []


def test_validate_compression_update_accepts_battle_events_only_with_allowed_participants():
    update = conversation_service.validate_conversation_compression_update({
        "scene": {},
        "memories": [],
        "relationships": [],
        "battle_events": [
            {
                "event_type": "match_result",
                "matchup_key": "char_a_vs_char_b",
                "participant_a_id": "char_a",
                "participant_b_id": "char_b",
                "winner_id": "char_a",
                "loser_id": "char_b",
                "process_summary": "char_a가 후반 반격으로 승리했다.",
                "decisive_moment": "후반 반격",
            },
            {
                "event_type": "match_result",
                "matchup_key": "bad",
                "participant_a_id": "char_a",
                "participant_b_id": "char_x",
                "winner_id": "char_x",
                "loser_id": "char_a",
                "process_summary": "허용되지 않은 캐릭터는 버려야 한다.",
            },
        ],
    }, {"char_a", "char_b"})

    assert update is not None
    assert update["battle_events"] == [{
        "event_type": "match_result",
        "matchup_key": "char_a_vs_char_b",
        "participant_a_id": "char_a",
        "participant_b_id": "char_b",
        "winner_id": "char_a",
        "loser_id": "char_b",
        "result_status": "completed",
        "process_summary": "char_a가 후반 반격으로 승리했다.",
        "decisive_moment": "후반 반격",
        "source_message_start_id": "",
        "source_message_end_id": "",
        "metadata": {},
    }]


def test_genre_domain_registry_routes_specialized_features_by_room_genre(session, monkeypatch):
    _add_character(session, "char_a", "차다나")
    _add_character(session, "char_b", "최도아")
    romance_room = _add_room(session, "conv_dynamic_romance", genre_mode="romance")
    battle_room = _add_room(session, "conv_dynamic_battle", genre_mode="battle")
    calls = []

    def fake_romance_context(session_arg, conversation):
        return f"[Official romance state]\nroom={conversation.id}" if conversation and conversation.genre_mode == "romance" else ""

    def fake_romance_apply(session_arg, conversation, update) -> list[object]:
        calls.append((conversation.id, update.get("romance_events")))
        return ["romance-applied"]

    handlers = dict(genre_domain_service.HANDLERS)
    handlers["romance"] = genre_domain_service.GenreDomainHandler(
        genre_mode="romance",
        context_key="official_romance_state",
        context_title="Official romance state",
        format_official_state=fake_romance_context,
        apply_events=fake_romance_apply,
    )
    monkeypatch.setattr(genre_domain_service, "HANDLERS", handlers)

    assert genre_domain_service.get_official_context(session, romance_room).startswith("[Official romance state]")
    assert genre_domain_service.get_official_context(session, battle_room) == ""
    assert genre_domain_service.apply_genre_domain_update(session, romance_room, {"romance_events": [{"event_type": "promise"}]}) == ["romance-applied"]
    assert calls == [("conv_dynamic_romance", [{"event_type": "promise"}])]

    sections = genre_domain_service.get_context_sections(session, romance_room)
    by_key = {section.key: section for section in sections}
    assert by_key["official_romance_state"].included is True
    assert by_key["official_battle_state"].included is False


def test_context_preview_uses_one_official_domain_section_without_cross_genre_battle_data(client, session):
    _add_character(session, "char_a", "루나")
    _add_character(session, "char_b", "미아 미오")
    battle_room = _add_room(session, "conv_battle_preview", genre_mode="battle")
    romance_room = _add_room(session, "conv_romance_preview", genre_mode="romance")
    battle_ledger_service.upsert_match_result(session, battle_room, {
        "matchup_key": "park_arin_vs_sakura_mio",
        "participant_a_id": "char_a",
        "participant_b_id": "char_b",
        "winner_id": "char_a",
        "loser_id": "char_b",
        "process_summary": "루나이 후반 반격으로 승리했다.",
    }, sync_external=False)

    battle_response = client.get("/conversations/conv_battle_preview/context-preview")
    romance_response = client.get("/conversations/conv_romance_preview/context-preview")

    battle_sections = {section["key"]: section for section in battle_response.json()["sections"]}
    romance_sections = {section["key"]: section for section in romance_response.json()["sections"]}
    assert "official_domain_state" in battle_sections
    assert battle_sections["official_domain_state"]["included"] is True
    assert romance_sections["official_domain_state"]["included"] is True

    assert "승점=3" in battle_sections["official_domain_state"]["content"]
    assert "승점=3" not in romance_sections["official_domain_state"]["content"]
    assert "official_battle_state" not in battle_sections


def test_battle_state_api_returns_participants_active_match_recent_records_and_standings(client, session):
    _add_character(session, "char_a", "유나")
    _add_character(session, "char_b", "네리 로드리게스")
    _add_character(session, "char_c", "이브")
    battle_room = _add_room(session, "conv_battle_state", genre_mode="battle")
    session.add(ConversationParticipant(conversation_id="conv_battle_state", participant_type="character", participant_id="char_c", order_index=2))
    session.commit()
    battle_ledger_service.upsert_match_result(session, battle_room, {
        "matchup_key": "kim_rina_vs_maya_rodriguez",
        "participant_a_id": "char_a",
        "participant_b_id": "char_b",
        "winner_id": "char_b",
        "loser_id": "char_a",
        "result_status": "completed",
        "process_summary": "네리가 후반 역전 흐름을 완성하며 유나를 KO로 제압했다.",
        "decisive_moment": "후반 역전 KO",
        "metadata": {"match_order": 8, "round_number": 1},
    }, sync_external=False)

    response = client.get("/conversations/conv_battle_state/battle-state")

    assert response.status_code == 200
    payload = response.json()
    assert payload["conversation_id"] == "conv_battle_state"
    assert payload["genre_mode"] == "battle"
    assert [item["name"] for item in payload["participants"]] == ["유나", "네리 로드리게스", "이브"]
    assert payload["active_match"] is None
    assert payload["recent_matches"][0]["match_order"] == 8
    assert payload["recent_matches"][0]["participant_a_name"] == "유나"
    assert payload["recent_matches"][0]["participant_b_name"] == "네리 로드리게스"
    assert payload["recent_matches"][0]["winner_name"] == "네리 로드리게스"
    assert payload["recent_matches"][0]["loser_name"] == "유나"
    standings_by_name = {item["name"]: item for item in payload["standings"]}
    assert standings_by_name["네리 로드리게스"]["wins"] == 1
    assert standings_by_name["네리 로드리게스"]["points"] == 3
    assert standings_by_name["유나"]["losses"] == 1


def test_message_api_battle_control_start_progress_end_updates_official_ledger(client, session, monkeypatch):
    _add_character(session, "char_a", "유나")
    _add_character(session, "char_b", "네리 로드리게스")
    _add_room(session, "conv_battle_control", genre_mode="battle")

    async def fake_generate_replies_from_message(**kwargs):
        return [kwargs["incoming_message"]]

    monkeypatch.setattr(conversations_api, "generate_replies_from_message", fake_generate_replies_from_message)
    monkeypatch.setattr(battle_ledger_service.external_memory_service, "sync_battle_memory_card_to_external", lambda *args, **kwargs: None)

    start_response = client.post("/conversations/conv_battle_control/messages", json={
        "speaker_type": "user",
        "speaker_id": "user_001",
        "content": "유나와 네리가 링 중앙에서 마주 선다.",
        "battle_control": {
            "action": "start",
            "participant_a_id": "char_a",
            "participant_b_id": "char_b",
            "advantage": 0.5,
            "current_phase": "opening",
        },
    })
    assert start_response.status_code == 200
    records = battle_ledger_service.list_match_records(session, "conv_battle_control")
    assert len(records) == 1
    record = records[0]
    assert record.result_status == "in_progress"
    assert record.participant_a_id == "char_a"
    assert record.participant_b_id == "char_b"
    assert record.source_message_start_id == start_response.json()[0]["id"]
    assert record.metadata_["match_order"] == 1
    assert record.metadata_["advantage"] == {"favored_character_id": None, "value": 0.5}

    progress_response = client.post("/conversations/conv_battle_control/messages", json={
        "speaker_type": "user",
        "speaker_id": "user_001",
        "content": "네리가 중반 주도권을 잡는다.",
        "battle_control": {
            "action": "progress",
            "match_id": record.id,
            "favored_character_id": "char_b",
            "advantage": 0.65,
            "current_phase": "middle",
            "process_summary": "네리가 중반 주도권을 잡았다.",
        },
    })
    assert progress_response.status_code == 200
    session.refresh(record)
    assert record.result_status == "in_progress"
    assert record.source_message_end_id == progress_response.json()[0]["id"]
    assert record.process_summary == "네리가 중반 주도권을 잡았다."
    assert record.metadata_["current_phase"] == "middle"
    assert record.metadata_["advantage"] == {"favored_character_id": "char_b", "value": 0.65}

    end_response = client.post("/conversations/conv_battle_control/messages", json={
        "speaker_type": "user",
        "speaker_id": "user_001",
        "content": "네리가 마지막 카운터로 승부를 끝낸다.",
        "battle_control": {
            "action": "end",
            "match_id": record.id,
            "winner_id": "char_b",
            "decisive_moment": "네리의 마지막 카운터",
            "process_summary": "네리가 후반 카운터로 유나를 제압했다.",
        },
    })
    assert end_response.status_code == 200
    session.refresh(record)
    assert record.result_status == "completed"
    assert record.winner_id == "char_b"
    assert record.loser_id == "char_a"
    assert record.source_message_end_id == end_response.json()[0]["id"]
    assert record.decisive_moment == "네리의 마지막 카운터"
    winner = session.get(BattleStanding, ("conv_battle_control", "char_b"))
    loser = session.get(BattleStanding, ("conv_battle_control", "char_a"))
    assert winner.wins == 1
    assert winner.points == 3
    assert loser.losses == 1


def test_battle_control_rejects_invalid_active_pair(client, session, monkeypatch):
    _add_character(session, "char_a", "유나")
    _add_character(session, "char_b", "네리 로드리게스")
    _add_room(session, "conv_battle_invalid_pair", genre_mode="battle")

    async def fake_generate_replies_from_message(**kwargs):
        return [kwargs["incoming_message"]]

    monkeypatch.setattr(conversations_api, "generate_replies_from_message", fake_generate_replies_from_message)
    response = client.post("/conversations/conv_battle_invalid_pair/messages", json={
        "speaker_type": "user",
        "speaker_id": "user_001",
        "content": "같은 캐릭터끼리 배틀 시작 시도",
        "battle_control": {
            "action": "start",
            "participant_a_id": "char_a",
            "participant_b_id": "char_a",
        },
    })
    assert response.status_code == 422
    assert battle_ledger_service.list_match_records(session, "conv_battle_invalid_pair") == []


async def test_battle_control_survives_async_job_rehydration(client, session, monkeypatch):
    _add_character(session, "char_a", "유나")
    _add_character(session, "char_b", "네리 로드리게스")
    _add_room(session, "conv_battle_async_control", genre_mode="battle")
    monkeypatch.setattr(conversations_api, "_start_message_generation_worker", lambda _job_id: None)
    captured = {}

    async def fake_generate_replies_from_message(**kwargs):
        captured["payload"] = kwargs["payload"]
        captured["runtime_context"] = conversations_api.build_battle_control_runtime_context(
            kwargs["session"],
            kwargs["payload"],
            conversation_id=kwargs["conversation_id"],
        )
        return []

    monkeypatch.setattr(conversations_api, "generate_replies_from_message", fake_generate_replies_from_message)
    response = client.post("/conversations/conv_battle_async_control/messages/jobs", json={
        "speaker_type": "user",
        "speaker_id": "user_001",
        "content": "유나와 네리가 배틀을 시작한다.",
        "battle_control": {
            "action": "start",
            "participant_a_id": "char_a",
            "participant_b_id": "char_b",
            "advantage": 0.5,
            "current_phase": "opening",
        },
    })

    assert response.status_code == 200
    body = response.json()
    assert body["incoming_message"]["metadata"]["battle_control"]["action"] == "start"
    await conversations_api._run_message_generation_job_in_session(session, body["job"]["id"])

    assert captured["payload"].battle_control.action == "start"
    assert captured["runtime_context"]["action"] == "start"
    assert captured["runtime_context"]["active_pair"] == {
        "participant_a_id": "char_a",
        "participant_a_name": "유나",
        "participant_b_id": "char_b",
        "participant_b_name": "네리 로드리게스",
    }
    state = client.get("/conversations/conv_battle_async_control/battle-state").json()
    assert state["active_match"]["result_status"] == "in_progress"
