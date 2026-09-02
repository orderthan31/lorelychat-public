import asyncio
import json

import pytest
from sqlmodel import select

from app.db.models import BattleMatchRecord, Character, CharacterMemory, Conversation, ConversationParticipant, ConversationRelationshipState, GenerationJobEvent, Message, MessageAsset, MessageGenerationJob, RuntimeSetting, SceneState
from app.core.config import get_settings
from app.engine.character_runtime import CHARACTER_TURN_CONTINUATION_INSTRUCTION, CharacterRuntimeError, build_chat_replies_response_format, character_retry_instruction
from app.engine.prompt_harness import approx_tokens
from app.services import conversation_service
from app.services.conversation_service import apply_conversation_compression_update
from app.services.context_management_service import ContextCapacity
from app.api.conversations import ContextMaintenanceRequired, _run_message_generation_job_in_session, _serialize_generation_sse_event, assign_generated_turn_group, generation_control_reserve_tokens, normalize_incoming_message, persist_generated_reply_messages, runtime_user_message, select_runtime_room_characters
from app.schemas.conversations import MessageCreate
from app.schemas.dialogue import CharacterReply


def test_runtime_user_message_labels_character_speaker_as_character_not_user():
    payload = MessageCreate(speaker_type="character", speaker_id="char_harin", content="리나가 1번을 맡아야 해", action="침대 옆에 선다")

    text = runtime_user_message(payload, speaker_name="서모아")

    assert "[Character action: 서모아(char_harin)]" in text
    assert "[Character dialogue: 서모아(char_harin)]" in text
    assert "[User dialogue]" not in text
    assert "리나가 1번을 맡아야 해" in text


def test_generation_control_reserve_covers_retry_and_character_continuation():
    user_tokens = generation_control_reserve_tokens(
        speaker_type="user",
        min_bubbles=2,
        max_bubbles=4,
    )
    character_tokens = generation_control_reserve_tokens(
        speaker_type="character",
        min_bubbles=2,
        max_bubbles=4,
    )

    retry_instruction = character_retry_instruction(2)
    initial_format_tokens = approx_tokens(json.dumps(
        build_chat_replies_response_format(min_bubbles=2, max_bubbles=4),
        ensure_ascii=False,
        sort_keys=True,
    ))
    retry_format_tokens = approx_tokens(json.dumps(
        build_chat_replies_response_format(min_bubbles=2, max_bubbles=4, retry=True),
        ensure_ascii=False,
        sort_keys=True,
    ))
    sample_user_message = "계속 이야기해줘"
    actual_user_retry_delta = (
        approx_tokens(f"{sample_user_message}\n\n{retry_instruction}")
        - approx_tokens(sample_user_message)
        + retry_format_tokens
    )
    actual_character_max = max(
        approx_tokens(CHARACTER_TURN_CONTINUATION_INSTRUCTION) + initial_format_tokens,
        approx_tokens(f"{CHARACTER_TURN_CONTINUATION_INSTRUCTION}\n\n{retry_instruction}")
        + retry_format_tokens,
    )

    assert user_tokens >= actual_user_retry_delta
    assert character_tokens >= actual_character_max
    assert character_tokens > user_tokens


def test_normalize_incoming_message_parses_character_action_markup_with_render_order():
    payload = MessageCreate(speaker_type="character", speaker_id="char_harin", content="미안해 *고개를 숙인다* 그래도 말할게")

    normalized = normalize_incoming_message(payload)

    assert normalized.speaker_type == "character"
    assert normalized.content == "미안해 그래도 말할게"
    assert normalized.action == "고개를 숙인다"
    assert normalized.metadata["render_parts"] == [
        {"type": "dialogue", "text": "미안해"},
        {"type": "action", "text": "고개를 숙인다"},
        {"type": "dialogue", "text": "그래도 말할게"},
    ]


def test_persistence_rejects_filtered_reply_before_any_database_write(client, session):
    character_data = client.post("/characters", json={"name": "아리아", "persona": "짧게 답한다."}).json()
    room = client.post("/conversations", json={
        "mode": "user_character",
        "participants": [
            {"type": "user", "id": "user_001"},
            {"type": "character", "id": character_data["id"]},
        ],
    }).json()
    conversation = session.get(Conversation, room["id"])
    character = session.get(Character, character_data["id"])
    payload = MessageCreate(speaker_type="user", speaker_id="user_001", content="다음 장면")
    incoming = conversation_service.add_message(session, room["id"], payload)
    before_ids = {
        message.id for message in session.exec(select(Message).where(Message.conversation_id == room["id"])).all()
    }

    with pytest.raises(CharacterRuntimeError, match="filtered before persistence"):
        asyncio.run(persist_generated_reply_messages(
            session=session,
            conversation_id=room["id"],
            conversation=conversation,
            payload=payload,
            incoming_message=incoming,
            generated_replies=[
                CharacterReply(character_id=character.id, text="정상 대사"),
                CharacterReply(reply_type="storytelling", character_id="storyteller", text="command_overlay: fake"),
            ],
            room_characters=[character],
            scene_state=None,
            is_multi_room=False,
            min_bubbles=2,
        ))

    session.expire_all()
    after_ids = {
        message.id for message in session.exec(select(Message).where(Message.conversation_id == room["id"])).all()
    }
    assert after_ids == before_ids


def test_sync_generated_reply_batch_persists_as_one_complete_turn(
    client,
    session,
):
    character_data = client.post(
        "/characters",
        json={"name": "아리아", "persona": "짧게 답한다."},
    ).json()
    room = client.post(
        "/conversations",
        json={
            "mode": "user_character",
            "participants": [
                {"type": "user", "id": "user_001"},
                {"type": "character", "id": character_data["id"]},
            ],
        },
    ).json()
    conversation = session.get(Conversation, room["id"])
    character = session.get(Character, character_data["id"])
    payload = MessageCreate(speaker_type="user", speaker_id="user_001", content="두 번 답해")
    incoming = conversation_service.add_message(session, room["id"], payload)

    generated = asyncio.run(persist_generated_reply_messages(
        session=session,
        conversation_id=room["id"],
        conversation=conversation,
        payload=payload,
        incoming_message=incoming,
        generated_replies=[
            CharacterReply(character_id=character.id, text="첫 번째"),
            CharacterReply(character_id=character.id, text="두 번째"),
        ],
        room_characters=[character],
        scene_state=None,
        is_multi_room=False,
        min_bubbles=2,
    ))

    turn_group_id = assign_generated_turn_group(
        generated,
        incoming_message=incoming,
        generation_job=None,
    )
    assert turn_group_id == f"sync_turn:{incoming.id}"
    assert [message.content for message in generated] == ["첫 번째", "두 번째"]
    assert {message.generation_job_id for message in generated} == {turn_group_id}
    assert [message.reply_index for message in generated] == [0, 1]


def test_post_message_returns_recoverable_context_maintenance_error_and_preserves_raw_input(
    client,
    session,
    monkeypatch,
):
    character_data = client.post(
        "/characters",
        json={"name": "아리아", "persona": "짧게 답한다."},
    ).json()
    room = client.post(
        "/conversations",
        json={
            "mode": "user_character",
            "participants": [
                {"type": "user", "id": "user_001"},
                {"type": "character", "id": character_data["id"]},
            ],
        },
    ).json()

    async def fail_before_provider(**_kwargs):
        raise ContextMaintenanceRequired(
            projected_tokens=18_500,
            working_tokens=16_000,
            backlog_groups=4,
            batches=3,
        )

    monkeypatch.setattr(
        "app.api.conversations.generate_replies_from_message",
        fail_before_provider,
    )

    response = client.post(
        f"/conversations/{room['id']}/messages",
        json={
            "speaker_type": "user",
            "speaker_id": "user_001",
            "content": "압축 실패여도 이 원문은 남겨줘",
        },
    )

    assert response.status_code == 503
    assert response.json()["detail"] == {
        "code": "context_maintenance_required",
        "projected_tokens": 18_500,
        "working_tokens": 16_000,
        "backlog_groups": 4,
        "catchup_batches": 3,
    }
    persisted = session.exec(
        select(Message).where(Message.conversation_id == room["id"])
    ).all()
    incoming = next(message for message in persisted if message.speaker_type == "user")
    assert incoming.content == "압축 실패여도 이 원문은 남겨줘"
    assert incoming.metadata_.get("generation_status") == "failed"
    assert incoming.metadata_.get("generation_error_code") == "context_maintenance_required"


def test_automatic_hard_pressure_no_progress_never_calls_chat_provider(
    client,
    session,
    monkeypatch,
):
    character_data = client.post(
        "/characters",
        json={"name": "아리아", "persona": "짧게 답한다."},
    ).json()
    room = client.post(
        "/conversations",
        json={
            "mode": "user_character",
            "participants": [
                {"type": "user", "id": "user_001"},
                {"type": "character", "id": character_data["id"]},
            ],
        },
    ).json()
    conversation_service.add_message(
        session,
        room["id"],
        MessageCreate(speaker_type="user", speaker_id="user_001", content="이전 질문"),
    )
    conversation_service.add_message(
        session,
        room["id"],
        MessageCreate(
            speaker_type="character",
            speaker_id=character_data["id"],
            content="이전 답변",
        ),
    )

    monkeypatch.setenv("CONTEXT_MANAGEMENT_MODE", "automatic")
    get_settings.cache_clear()
    tiny_capacity = ContextCapacity(
        model_option_key="model_tiny",
        context_window_tokens=200,
        max_output_tokens=50,
        completion_reserve_tokens=50,
        mandatory_reserve_tokens=0,
        available_input_tokens=150,
        working_input_tokens=100,
        source="model_metadata",
        used_fallback=False,
    )
    monkeypatch.setattr(
        "app.api.conversations.resolve_model_context_capacity",
        lambda *_args, **_kwargs: tiny_capacity,
    )
    provider_calls = 0

    async def no_progress(*_args, **_kwargs):
        return session.get(SceneState, room["id"])

    async def unexpected_provider_call(*_args, **_kwargs):
        nonlocal provider_calls
        provider_calls += 1
        raise AssertionError("chat provider must not run under unrecoverable hard pressure")

    monkeypatch.setattr(
        conversation_service,
        "update_scene_orchestration_summary",
        no_progress,
    )
    monkeypatch.setattr(
        "app.api.conversations.CharacterRuntime.generate_replies",
        unexpected_provider_call,
    )

    try:
        response = client.post(
            f"/conversations/{room['id']}/messages",
            json={
                "speaker_type": "user",
                "speaker_id": "user_001",
                "content": "새 질문은 원문으로 보존해",
            },
        )
    finally:
        get_settings.cache_clear()

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "context_maintenance_required"
    assert response.json()["detail"]["catchup_batches"] == 1
    assert provider_calls == 0
    persisted = session.exec(
        select(Message).where(Message.conversation_id == room["id"])
    ).all()
    assert any(message.content == "새 질문은 원문으로 보존해" for message in persisted)


def test_async_generation_job_preserves_context_maintenance_code(
    client,
    session,
    monkeypatch,
):
    character = client.post(
        "/characters",
        json={"name": "세아", "persona": "침착하다."},
    ).json()
    room = client.post(
        "/conversations",
        json={
            "mode": "user_character",
            "participants": [
                {"type": "user", "id": "user_001"},
                {"type": "character", "id": character["id"]},
            ],
        },
    ).json()
    monkeypatch.setattr(
        "app.api.conversations._start_message_generation_worker",
        lambda _job_id: None,
    )

    async def fail_with_maintenance(**_kwargs):
        raise ContextMaintenanceRequired(
            projected_tokens=250,
            working_tokens=100,
            backlog_groups=2,
            batches=1,
        )

    monkeypatch.setattr(
        "app.api.conversations.generate_replies_from_message",
        fail_with_maintenance,
    )
    response = client.post(
        f"/conversations/{room['id']}/messages/jobs",
        json={
            "speaker_type": "user",
            "speaker_id": "user_001",
            "content": "비동기 원문",
        },
    )
    assert response.status_code == 200
    body = response.json()
    job_id = body["job"]["id"]
    incoming_id = body["incoming_message"]["id"]

    asyncio.run(_run_message_generation_job_in_session(session, job_id))
    session.expire_all()

    job = session.get(MessageGenerationJob, job_id)
    incoming = session.get(Message, incoming_id)
    failed_event = session.exec(
        select(GenerationJobEvent)
        .where(GenerationJobEvent.job_id == job_id)
        .where(GenerationJobEvent.status == "failed")
    ).one()
    assert job.status == "failed"
    assert "context_maintenance_required" in job.error_message
    assert incoming.content == "비동기 원문"
    assert incoming.metadata_.get("generation_error_code") == "context_maintenance_required"
    assert failed_event.payload_.get("error_code") == "context_maintenance_required"

    public_job = client.get(
        f"/conversations/{room['id']}/generation-jobs/{job_id}"
    )
    assert public_job.status_code == 200
    assert public_job.json()["error_code"] == "context_maintenance_required"

    event_name, event_payload = _serialize_generation_sse_event(failed_event, job)
    assert event_name == "failed"
    assert event_payload["error_code"] == "context_maintenance_required"


def test_create_conversation_with_scene(client):
    char = client.post("/characters", json={"name": "아리아", "persona": "장난스럽다."}).json()
    response = client.post("/conversations", json={
        "mode": "user_character",
        "title": "아리아와 대화",
        "thumbnail_url": "/uploads/conversation-thumbnails/created.png",
        "participants": [
            {"type": "user", "id": "user_001"},
            {"type": "character", "id": char["id"]},
        ],
        "scene": {"location": "방 안", "mood": "편안함"},
    })
    assert response.status_code == 200
    assert response.json()["id"].startswith("conv_")
    assert response.json()["thumbnail_url"] is None


def test_user_character_conversation_uses_default_title_when_blank(client):
    aria = client.post("/characters", json={"name": "아리아", "persona": "차분하고 다정하다."}).json()

    response = client.post("/conversations", json={
        "mode": "user_character",
        "title": "",
        "participants": [
            {"type": "user", "id": "user_001", "order_index": 0},
            {"type": "character", "id": aria["id"], "order_index": 1},
        ],
    })

    assert response.status_code == 200
    assert response.json()["title"] == "아리아 대화방"


def test_user_character_conversation_stores_user_description_in_scene(client, session):
    aria = client.post("/characters", json={"name": "아리아", "persona": "차분하고 다정하다."}).json()

    room = client.post("/conversations", json={
        "mode": "user_character",
        "participants": [
            {"type": "user", "id": "user_001", "order_index": 0},
            {"type": "character", "id": aria["id"], "order_index": 1},
        ],
        "scene": {
            "location": "작업방",
            "mood": "편한 대화",
            "world_seed": "개발 방향 잡기",
            "opening_scene": "사용자가 늦은 밤 작업방 문을 열고 들어온다.",
            "opening_line": "사용자, 또 혼자 붙잡고 있었어? 내가 같이 볼게.",
            "tone_preset": "slow_burn",
            "relationship_archetype": "tsundere_hidden_affection",
            "compression_focus": "관계 변화와 다음 작업 앵커를 우선 보존",
            "user_description": "사용자는 개발자이고 캐릭터와 가까운 선후배 관계다.",
        },
    }).json()

    scene = session.get(SceneState, room["id"])
    assert scene.user_description == "사용자는 개발자이고 캐릭터와 가까운 선후배 관계다."
    assert scene.summary is None
    assert scene.world_seed == "개발 방향 잡기"
    assert scene.opening_scene == "사용자가 늦은 밤 작업방 문을 열고 들어온다."
    assert scene.opening_line == "사용자, 또 혼자 붙잡고 있었어? 내가 같이 볼게."
    assert scene.tone_preset == "slow_burn"
    assert scene.relationship_archetype == "tsundere_hidden_affection"
    assert scene.current_conflict is None
    assert scene.compression_focus == "관계 변화와 다음 작업 앵커를 우선 보존"
    context = client.get(f"/conversations/{room['id']}/context").json()
    assert context["scene"]["world_seed"] == "개발 방향 잡기"
    assert context["scene"]["opening_scene"] == "사용자가 늦은 밤 작업방 문을 열고 들어온다."
    assert context["scene"]["opening_line"] == "사용자, 또 혼자 붙잡고 있었어? 내가 같이 볼게."
    assert context["scene"]["tone_preset"] == "slow_burn"
    assert context["scene"]["relationship_archetype"] == "tsundere_hidden_affection"
    assert context["scene"]["compression_focus"] == "관계 변화와 다음 작업 앵커를 우선 보존"
    assert context["scene"]["user_description"] == "사용자는 개발자이고 캐릭터와 가까운 선후배 관계다."
    messages = client.get(f"/conversations/{room['id']}/messages").json()
    assert messages[0]["speaker_type"] == "character"
    assert messages[0]["speaker_id"] == aria["id"]
    assert messages[0]["content"] == "사용자, 또 혼자 붙잡고 있었어? 내가 같이 볼게."
    recent_messages = client.get(f"/conversations/{room['id']}/messages?recent_turns=10").json()
    assert recent_messages[0]["content"] == "사용자, 또 혼자 붙잡고 있었어? 내가 같이 볼게."


def test_context_uses_persisted_scene_state_and_relationships_without_rebuilding_live_tail(client, session):
    reika = client.post("/characters", json={"name": "에코", "persona": "침착한 선수"}).json()
    yiju = client.post("/characters", json={"name": "이주", "persona": "도전적인 선수"}).json()
    room = client.post("/conversations", json={
        "mode": "character_character",
        "genre_mode": "battle",
        "participants": [
            {"type": "character", "id": reika["id"], "order_index": 0},
            {"type": "character", "id": yiju["id"], "order_index": 1},
        ],
        "scene": {
            "location": "낡은 라운지",
            "mood": "낡은 분위기",
            "world_seed": "리그 세계관",
            "current_conflict": "3경기 후 리뷰 및 다음 매치업 지목",
        },
    }).json()
    scene = session.get(SceneState, room["id"])
    scene.summary = "[Scene memory compact]\nVisible situation: 3경기 후 리뷰 및 다음 매치업 지목"
    scene.last_event = "오래된 이벤트"
    session.add(BattleMatchRecord(
        id="match_live_scene",
        conversation_id=room["id"],
        genre_mode="battle",
        matchup_key="reika_vs_yiju",
        participant_a_id=reika["id"],
        participant_b_id=yiju["id"],
        result_status="in_progress",
        process_summary="에코와 이주가 본경기 직전 예열 규칙을 확인한다.",
        metadata_={"match_order": 12, "current_phase": "prelude"},
    ))
    session.add(Message(
        id="msg_live_latest",
        conversation_id=room["id"],
        speaker_type="user",
        speaker_id="user_001",
        content="두 선수 서로에게 한마디 하고 시작합시다.",
    ))
    session.add(ConversationRelationshipState(
        conversation_id=room["id"],
        character_id=reika["id"],
        counterpart_type="character",
        counterpart_id=yiju["id"],
        tension_level=4,
        current_dynamic="에코와 이주는 방금 시작된 경기에서 팽팽하게 맞선다.",
    ))
    session.commit()

    response = client.get(f"/conversations/{room['id']}/context")

    assert response.status_code == 200
    body = response.json()
    assert body["scene"]["current_conflict"] == "진행 중: #12 에코 vs 이주 · phase=prelude"
    assert "두 선수 서로에게 한마디" in body["scene"]["last_event"]
    assert body["relationships"] == []


def test_context_hides_offstage_character_memories_until_character_rejoins(client, session):
    active = client.post("/characters", json={"name": "미나", "persona": "현 참여 캐릭터"}).json()
    offstage = client.post("/characters", json={"name": "모아", "persona": "나중에 재합류할 캐릭터"}).json()
    room = client.post("/conversations", json={
        "mode": "user_character",
        "participants": [
            {"type": "user", "id": "user_001", "order_index": 0},
            {"type": "character", "id": active["id"], "order_index": 1},
        ],
    }).json()
    session.add(CharacterMemory(
        id="mem_room_visible",
        conversation_id=room["id"],
        character_id="__room__",
        memory_type="user_note",
        content="방 전체 장기 기억은 현재 방의 공개 설정으로 유지된다",
        importance=5,
    ))
    session.add(CharacterMemory(
        id="mem_active_visible",
        conversation_id=room["id"],
        character_id=active["id"],
        memory_type="user_note",
        content="현 참여 캐릭터 장기 기억",
        importance=5,
    ))
    session.add(CharacterMemory(
        id="mem_offstage_hidden",
        conversation_id=room["id"],
        character_id=offstage["id"],
        memory_type="user_note",
        content="방에 없는 캐릭터 장기 기억",
        importance=5,
    ))
    session.commit()

    body = client.get(f"/conversations/{room['id']}/context").json()

    contents = [item["content"] for item in body["memories"]]
    assert "방 전체 장기 기억은 현재 방의 공개 설정으로 유지된다" in contents
    assert "현 참여 캐릭터 장기 기억" in contents
    assert "방에 없는 캐릭터 장기 기억" not in contents

    compression_preview = client.get(f"/conversations/{room['id']}/compression-preview").json()
    compression_source = next(section["content"] for section in compression_preview["sections"] if section["key"] == "compression_scene_source")
    assert "방 전체 장기 기억은 현재 방의 공개 설정으로 유지된다" not in compression_source
    assert "현 참여 캐릭터 장기 기억" not in compression_source
    assert "방에 없는 캐릭터 장기 기억" not in compression_source
    assert "Chronological Messages To Fold Into The Summary" in compression_source

    client.post(f"/conversations/{room['id']}/participants", json={"type": "character", "id": offstage["id"], "order_index": 2})
    body_after_rejoin = client.get(f"/conversations/{room['id']}/context").json()

    assert "방에 없는 캐릭터 장기 기억" in [item["content"] for item in body_after_rejoin["memories"]]


def test_oversized_league_room_generation_uses_recent_active_characters_only():
    characters = [type("RuntimeChar", (), {"id": f"char_{idx}"})() for idx in range(1, 9)]
    recent_messages = [
        Message(id="m1", conversation_id="conv", speaker_type="character", speaker_id="char_5", content="다섯 번째 반응"),
        Message(id="m2", conversation_id="conv", speaker_type="character", speaker_id="char_7", content="일곱 번째 반응"),
        Message(id="m3", conversation_id="conv", speaker_type="user", speaker_id="user_001", content="둘 다 이어가"),
    ]

    selected = select_runtime_room_characters(
        room_characters=characters,
        recent_messages=recent_messages,
        payload=MessageCreate(speaker_type="user", speaker_id="user_001", content="둘 다 이어가"),
    )

    assert [character.id for character in selected] == ["char_5", "char_7"]


def test_user_message_in_character_character_room_gets_character_feedback(client):
    aria = client.post("/characters", json={"name": "아리아", "persona": "사용자를 챙긴다."}).json()
    luna = client.post("/characters", json={"name": "루나", "persona": "밝게 반응한다."}).json()
    room = client.post("/conversations", json={
        "mode": "character_character",
        "title": "자동대화였던 방",
        "participants": [
            {"type": "character", "id": aria["id"], "order_index": 0},
            {"type": "character", "id": luna["id"], "order_index": 1},
        ],
    }).json()

    response = client.post(f"/conversations/{room['id']}/messages", json={
        "speaker_type": "user",
        "speaker_id": "user_001",
        "content": "여기서 이어서 얘기해줘.",
    })

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 3
    assert body[0]["speaker_type"] == "user"
    assert [item["speaker_type"] for item in body[1:]] == ["character", "character"]
    assert body[1]["speaker_id"] == aria["id"]
    assert "Mock reply 1 to" in body[1]["content"]
    assert "Mock reply 2 to" in body[2]["content"]


def test_system_message_in_user_character_room_is_scene_direction(client, session):
    aria = client.post("/characters", json={"name": "아리아", "persona": "상황을 빠르게 받아들인다."}).json()
    room = client.post("/conversations", json={
        "mode": "user_character",
        "participants": [
            {"type": "user", "id": "user_001", "order_index": 0},
            {"type": "character", "id": aria["id"], "order_index": 1},
        ],
    }).json()

    response = client.post(f"/conversations/{room['id']}/messages", json={
        "speaker_type": "system",
        "speaker_id": "system",
        "content": "창밖에서 비가 거세지고 둘은 그 상황을 알아차린다.",
    })

    assert response.status_code == 200
    body = response.json()
    assert body[0]["speaker_type"] == "system"
    assert body[0]["speaker_id"] == "system"
    assert body[1]["speaker_type"] == "character"
    scene = session.get(SceneState, room["id"])
    assert scene.summary in {None, ""}
    assert "시스템 지시" in scene.last_event
    assert "창밖에서 비가 거세지고" in scene.last_event


def test_system_message_in_character_room_targets_next_character(client):
    aria = client.post("/characters", json={"name": "아리아", "persona": "차분하다."}).json()
    luna = client.post("/characters", json={"name": "루나", "persona": "밝다."}).json()
    room = client.post("/conversations", json={
        "mode": "character_character",
        "participants": [
            {"type": "character", "id": aria["id"], "order_index": 0},
            {"type": "character", "id": luna["id"], "order_index": 1},
        ],
    }).json()
    client.post(f"/conversations/{room['id']}/messages", json={
        "speaker_type": "character",
        "speaker_id": aria["id"],
        "content": "먼저 말을 건다.",
    })

    response = client.post(f"/conversations/{room['id']}/messages", json={
        "speaker_type": "system",
        "speaker_id": "system",
        "content": "방 안 조명이 갑자기 어두워진다.",
    })

    assert response.status_code == 200
    body = response.json()
    assert body[0]["speaker_type"] == "system"
    assert body[1]["speaker_type"] == "character"
    assert body[1]["speaker_id"] == aria["id"]


def test_conversation_participants_are_listed_in_order(client):
    aria = client.post("/characters", json={"name": "아리아", "persona": "사용자를 챙긴다."}).json()
    luna = client.post("/characters", json={"name": "루나", "persona": "밝게 반응한다."}).json()
    room = client.post("/conversations", json={
        "mode": "character_character",
        "participants": [
            {"type": "character", "id": luna["id"], "order_index": 1},
            {"type": "character", "id": aria["id"], "order_index": 0},
        ],
    }).json()

    response = client.get(f"/conversations/{room['id']}/participants")

    assert response.status_code == 200
    assert [item["id"] for item in response.json()] == [aria["id"], luna["id"]]


def test_update_conversation_title(client):
    aria = client.post("/characters", json={"name": "아리아", "persona": "사용자를 챙긴다."}).json()
    room = client.post("/conversations", json={
        "mode": "user_character",
        "title": "처음 이름",
        "participants": [
            {"type": "user", "id": "user_001", "order_index": 0},
            {"type": "character", "id": aria["id"], "order_index": 1},
        ],
    }).json()

    response = client.patch(f"/conversations/{room['id']}", json={"title": "바꾼 대화방", "thumbnail_url": "/uploads/conversation-thumbnails/room.png"})

    assert response.status_code == 200
    assert response.json()["title"] == "바꾼 대화방"
    assert response.json()["thumbnail_url"] is None
    saved = client.get(f"/conversations/{room['id']}").json()
    assert saved["title"] == "바꾼 대화방"
    assert saved["thumbnail_url"] is None


def test_list_conversations_uses_linked_world_thumbnail(client):
    aria = client.post("/characters", json={"name": "아리아", "persona": "사용자를 챙긴다."}).json()
    world = client.post("/world-settings", json={
        "title": "썸네일 세계관",
        "thumbnail_url": "/uploads/world-thumbnails/world.png",
        "world_seed": "세계관 썸네일을 목록에 보여준다.",
    }).json()
    room = client.post("/conversations", json={
        "mode": "user_character",
        "title": "세계관 연결 방",
        "world_setting_id": world["id"],
        "thumbnail_url": "/uploads/conversation-thumbnails/legacy-room.png",
        "participants": [
            {"type": "user", "id": "user_001", "order_index": 0},
            {"type": "character", "id": aria["id"], "order_index": 1},
        ],
    }).json()

    rooms = client.get("/conversations").json()
    item = next(conversation for conversation in rooms if conversation["id"] == room["id"])
    assert item["thumbnail_url"] == "/uploads/world-thumbnails/world.png"
    assert item["participants"] == [
        {"type": "user", "id": "user_001", "role": None, "order_index": 0},
        {"type": "character", "id": aria["id"], "role": None, "order_index": 1},
    ]


def test_list_conversations_orders_by_recent_update(client):
    aria = client.post("/characters", json={"name": "아리아", "persona": "사용자를 챙긴다."}).json()
    first = client.post("/conversations", json={
        "mode": "user_character",
        "title": "먼저 만든 방",
        "participants": [
            {"type": "user", "id": "user_001", "order_index": 0},
            {"type": "character", "id": aria["id"], "order_index": 1},
        ],
    }).json()
    second = client.post("/conversations", json={
        "mode": "user_character",
        "title": "나중 만든 방",
        "participants": [
            {"type": "user", "id": "user_001", "order_index": 0},
            {"type": "character", "id": aria["id"], "order_index": 1},
        ],
    }).json()

    client.patch(f"/conversations/{first['id']}", json={"title": "최근 수정된 방"})

    rooms = client.get("/conversations").json()
    assert rooms[0]["id"] == first["id"]
    assert rooms[1]["id"] == second["id"]
    assert rooms[0]["updated_at"] >= rooms[1]["updated_at"]


def test_update_conversation_scene_editable_from_room_list(client, session):
    aria = client.post("/characters", json={"name": "아리아", "persona": "사용자를 챙긴다."}).json()
    room = client.post("/conversations", json={
        "mode": "user_character",
        "title": "처음 방",
        "participants": [
            {"type": "user", "id": "user_001", "order_index": 0},
            {"type": "character", "id": aria["id"], "order_index": 1},
        ],
        "scene": {
            "location": "작업방",
            "mood": "테스트",
            "world_seed": "처음 세계관",
            "tone_preset": "fast_banter",
            "compression_focus": "처음 압축포인트",
            "user_description": "처음 유저설정",
        },
    }).json()

    response = client.patch(f"/conversations/{room['id']}", json={
        "title": "수정한 방",
        "scene": {
            "location": "카페",
            "mood": "편한 분위기",
            "world_seed": "수정한 세계관",
            "tone_preset": "slow_burn",
            "compression_focus": "수정한 압축포인트",
            "user_description": "수정한 유저설정",
        },
    })

    assert response.status_code == 200
    assert response.json()["title"] == "수정한 방"
    context = client.get(f"/conversations/{room['id']}/context").json()
    assert context["scene"]["location"] == "카페"
    assert context["scene"]["mood"] == "편한 분위기"
    assert context["scene"]["world_seed"] == "수정한 세계관"
    assert context["scene"]["tone_preset"] == "slow_burn"
    assert context["scene"]["current_conflict"] is None
    assert context["scene"]["compression_focus"] == "수정한 압축포인트"
    assert context["scene"]["user_description"] == "수정한 유저설정"
    scene = session.get(SceneState, room["id"])
    assert scene.user_description == "수정한 유저설정"
    assert scene.summary is None


def test_update_conversation_room_settings_does_not_clear_compression_summary(client, session):
    aria = client.post("/characters", json={"name": "아리아", "persona": "사용자를 챙긴다."}).json()
    room = client.post("/conversations", json={
        "mode": "user_character",
        "title": "요약 보존 방",
        "participants": [
            {"type": "user", "id": "user_001", "order_index": 0},
            {"type": "character", "id": aria["id"], "order_index": 1},
        ],
        "scene": {
            "location": "작업방",
            "mood": "테스트",
            "world_seed": "처음 세계관",
            "compression_focus": "처음 압축포인트",
        },
    }).json()
    scene = session.get(SceneState, room["id"])
    scene.summary = "[Scene memory compact]\nCurrent situation: 중요한 진행 요약"
    session.add(scene)
    session.commit()

    response = client.patch(f"/conversations/{room['id']}", json={
        "title": "요약 보존 방",
        "scene": {
            "location": "작업방",
            "mood": "테스트",
            "world_seed": "수정한 세계관",
            "compression_focus": "수정한 압축포인트",
            "user_description": None,
        },
    })

    assert response.status_code == 200
    scene = session.get(SceneState, room["id"])
    assert scene.world_seed == "수정한 세계관"
    assert scene.summary == "[Scene memory compact]\nCurrent situation: 중요한 진행 요약"


def test_compression_update_preserves_fixed_world_seed(client, session):
    aria = client.post("/characters", json={"name": "아리아", "persona": "사용자를 챙긴다."}).json()
    room = client.post("/conversations", json={
        "mode": "user_character",
        "participants": [
            {"type": "user", "id": "user_001", "order_index": 0},
            {"type": "character", "id": aria["id"], "order_index": 1},
        ],
        "scene": {
            "location": "작업방",
            "mood": "테스트",
            "world_seed": "고정 세계관: 아리아는 사용자의 개발 파트너다.",
            "compression_focus": "관계 변화 보존",
        },
    }).json()

    apply_conversation_compression_update(session, room["id"], {
        "scene": {
            "summary": "[Scene memory compact]\nCurrent situation: 기능 검증 중",
            "location": "카페",
            "mood": "집중",
            "current_conflict": "현재 상황: 세계관 보존 버그를 확인한다.",
            "last_event": "압축이 한 번 실행됐다.",
        }
    })
    session.commit()

    scene = session.get(SceneState, room["id"])
    assert scene.world_seed == "고정 세계관: 아리아는 사용자의 개발 파트너다."
    assert scene.current_conflict == "현재 상황: 세계관 보존 버그를 확인한다."
    context = client.get(f"/conversations/{room['id']}/context").json()
    assert context["scene"]["world_seed"] == "고정 세계관: 아리아는 사용자의 개발 파트너다."
    assert context["scene"]["current_conflict"] == "현재 상황: 세계관 보존 버그를 확인한다."


def test_add_character_participant_to_existing_room_promotes_multi_character(client):
    aria = client.post("/characters", json={"name": "아리아", "persona": "사용자를 챙긴다."}).json()
    luna = client.post("/characters", json={"name": "루나", "persona": "밝게 반응한다."}).json()
    room = client.post("/conversations", json={
        "mode": "user_character",
        "participants": [
            {"type": "user", "id": "user_001", "order_index": 0},
            {"type": "character", "id": aria["id"], "order_index": 1},
        ],
    }).json()

    response = client.post(f"/conversations/{room['id']}/participants", json={"type": "character", "id": luna["id"]})

    assert response.status_code == 200
    assert response.json()["id"] == luna["id"]
    participants = client.get(f"/conversations/{room['id']}/participants").json()
    assert [item["id"] for item in participants] == ["user_001", aria["id"], luna["id"]]
    assert client.get(f"/conversations/{room['id']}").json()["mode"] == "character_character"
    messages = client.get(f"/conversations/{room['id']}/messages").json()
    assert messages[-1]["speaker_type"] == "system"
    assert messages[-1]["speaker_id"] == "system"
    assert messages[-1]["content"] == "루나님이 들어왔습니다."


def test_remove_character_participant_demotes_room_when_one_character_remains(client):
    aria = client.post("/characters", json={"name": "아리아", "persona": "사용자를 챙긴다."}).json()
    luna = client.post("/characters", json={"name": "루나", "persona": "밝게 반응한다."}).json()
    room = client.post("/conversations", json={
        "mode": "character_character",
        "participants": [
            {"type": "user", "id": "user_001", "order_index": 0},
            {"type": "character", "id": aria["id"], "order_index": 1},
            {"type": "character", "id": luna["id"], "order_index": 2},
        ],
    }).json()

    response = client.delete(f"/conversations/{room['id']}/participants/character/{luna['id']}")

    assert response.status_code == 204
    participants = client.get(f"/conversations/{room['id']}/participants").json()
    assert [item["id"] for item in participants] == ["user_001", aria["id"]]
    assert client.get(f"/conversations/{room['id']}").json()["mode"] == "user_character"
    messages = client.get(f"/conversations/{room['id']}/messages").json()
    assert messages[-1]["speaker_type"] == "system"
    assert messages[-1]["speaker_id"] == "system"
    assert messages[-1]["content"] == "루나님이 나갔습니다."


def test_conversation_context_hides_duplicate_and_non_durable_memories(client, session):
    aria = client.post("/characters", json={"name": "아리아", "persona": "사용자를 챙긴다."}).json()
    room = client.post("/conversations", json={
        "mode": "user_character",
        "participants": [
            {"type": "user", "id": "user_001", "order_index": 0},
            {"type": "character", "id": aria["id"], "order_index": 1},
        ],
    }).json()
    durable = "세계관 규칙: 이 방에서는 카페가 비밀 모임 장소로 확정됐다."
    common = "세계관 규칙: 이 방의 비밀 암호는 파란 장미다."
    session.add(CharacterMemory(id="mem_common", conversation_id=room["id"], character_id="__room__", memory_type="user_note", content=common, importance=5))
    session.add(CharacterMemory(id="mem_keep_1", conversation_id=room["id"], character_id=aria["id"], memory_type="user_note", content=durable, importance=4))
    session.add(CharacterMemory(id="mem_keep_2", conversation_id=room["id"], character_id=aria["id"], memory_type="user_note", content=durable, importance=4))
    session.add(CharacterMemory(id="mem_noise", conversation_id=room["id"], character_id=aria["id"], memory_type="event", content="아리아가 살짝 웃었다.", importance=2))
    session.commit()

    response = client.get(f"/conversations/{room['id']}/context")

    assert response.status_code == 200
    memories = response.json()["memories"]
    assert [item["content"] for item in memories] == [common, durable]
    assert memories[0]["character_id"] == "__room__"


def test_conversation_context_keeps_short_low_importance_user_notes(client):
    aria = client.post("/characters", json={"name": "아리아", "persona": "사용자를 챙긴다."}).json()
    room = client.post("/conversations", json={
        "mode": "user_character",
        "participants": [
            {"type": "user", "id": "user_001", "order_index": 0},
            {"type": "character", "id": aria["id"], "order_index": 1},
        ],
    }).json()

    common_response = client.post(f"/conversations/{room['id']}/memories", json={
        "character_id": "__room__",
        "memory_type": "user_note",
        "content": "이름은 재우",
        "importance": 1,
    })
    character_response = client.post(f"/conversations/{room['id']}/memories", json={
        "character_id": aria["id"],
        "memory_type": "user_note",
        "content": "커피 좋아함",
        "importance": 2,
    })

    assert common_response.status_code == 200
    assert character_response.status_code == 200
    response = client.get(f"/conversations/{room['id']}/context")
    assert response.status_code == 200
    memories = response.json()["memories"]
    assert {(item["content"], item["importance"]) for item in memories} == {
        ("이름은 재우", 1),
        ("커피 좋아함", 2),
    }


def test_conversation_context_relationship_fact_hides_raw_action_and_next_question(client, session):
    aria = client.post("/characters", json={"name": "아리아", "persona": "사용자를 챙긴다."}).json()
    luna = client.post("/characters", json={"name": "루나", "persona": "밝게 돕는다."}).json()
    room = client.post("/conversations", json={
        "mode": "character_character",
        "participants": [
            {"type": "user", "id": "user_001", "order_index": 0},
            {"type": "character", "id": aria["id"], "order_index": 1},
            {"type": "character", "id": luna["id"], "order_index": 2},
        ],
    }).json()
    session.add(ConversationRelationshipState(
        conversation_id=room["id"],
        character_id=aria["id"],
        counterpart_type="character",
        counterpart_id=luna["id"],
        current_dynamic="아리아는 루나을 믿고 작전 진행을 맡길 만큼 협력적이다.",
        current_mood="안정적 신뢰",
    ))
    session.add(ConversationRelationshipState(
        conversation_id=room["id"],
        character_id=luna["id"],
        counterpart_type="character",
        counterpart_id=aria["id"],
        current_dynamic="복도에서 걸음을 멈추고 웃는다. 이전 압축에 원문 대사가 섞였다. 다음 진행 판단: 사용자가 승인할 것인가?",
        current_mood="기대",
        unresolved_hooks=["다음 진행 판단: 사용자가 승인할 것인가?"],
    ))
    session.commit()

    response = client.get(f"/conversations/{room['id']}/context")

    assert response.status_code == 200
    assert response.json()["relationships"] == []


def test_delete_conversation_removes_room_messages_participants_and_scene(client, session):
    aria = client.post("/characters", json={"name": "아리아", "persona": "사용자를 챙긴다."}).json()
    room = client.post("/conversations", json={
        "mode": "user_character",
        "title": "삭제할 대화",
        "participants": [
            {"type": "user", "id": "user_001", "order_index": 0},
            {"type": "character", "id": aria["id"], "order_index": 1},
        ],
        "scene": {"location": "테스트룸", "mood": "삭제 검증"},
    }).json()
    created_messages = client.post(f"/conversations/{room['id']}/messages", json={
        "speaker_type": "user",
        "speaker_id": "user_001",
        "content": "삭제 전 메시지",
    }).json()
    created_message_id = created_messages[0]["id"]
    session.add(MessageAsset(id="msgasset_room_delete_test", message_id=created_message_id, asset_id="asset_missing_for_room_delete", display_order=0))
    session.add(RuntimeSetting(id=room["id"], conversation_id=room["id"], model_key="gemini-3-flash", compression_model_key="local-gemma"))
    session.commit()

    response = client.delete(f"/conversations/{room['id']}")

    assert response.status_code == 204
    assert client.get(f"/conversations/{room['id']}").status_code == 404
    assert session.exec(select(Message).where(Message.conversation_id == room["id"])).first() is None
    assert session.exec(select(MessageAsset).where(MessageAsset.message_id == created_message_id)).first() is None
    assert session.exec(select(RuntimeSetting).where(RuntimeSetting.conversation_id == room["id"])).first() is None
    assert session.exec(select(ConversationParticipant).where(ConversationParticipant.conversation_id == room["id"])).first() is None
    assert session.get(SceneState, room["id"]) is None


def test_conversation_read_marker_controls_unread_fields(client, session):
    aria = client.post("/characters", json={"name": "아리아", "persona": "사용자를 챙긴다."}).json()
    room = client.post("/conversations", json={
        "mode": "user_character",
        "title": "읽음 테스트",
        "participants": [
            {"type": "user", "id": "user_001", "order_index": 0},
            {"type": "character", "id": aria["id"], "order_index": 1},
        ],
    }).json()
    assert room["has_unread"] is False
    assert room["unread_count"] == 0

    session.add(Message(id="msg_user_read_test", conversation_id=room["id"], speaker_type="user", speaker_id="user_001", content="내 메시지"))
    session.add(Message(id="msg_char_read_test", conversation_id=room["id"], speaker_type="character", speaker_id=aria["id"], content="사용자 답장 왔어"))
    session.commit()

    listed = client.get("/conversations").json()
    item = next(conversation for conversation in listed if conversation["id"] == room["id"])
    assert item["has_unread"] is True
    assert item["unread_count"] == 1
    assert item["last_message_id"] == "msg_char_read_test"

    marker = client.post(f"/conversations/{room['id']}/read")
    assert marker.status_code == 200
    assert marker.json()["last_read_message_id"] == "msg_char_read_test"

    refreshed = client.get(f"/conversations/{room['id']}").json()
    assert refreshed["has_unread"] is False
    assert refreshed["unread_count"] == 0


def test_message_generation_job_creates_incoming_message_and_tracks_completion(client, session, monkeypatch):
    aria = client.post("/characters", json={"name": "아리아", "persona": "사용자를 챙긴다."}).json()
    room = client.post("/conversations", json={
        "mode": "user_character",
        "title": "잡 테스트",
        "participants": [
            {"type": "user", "id": "user_001", "order_index": 0},
            {"type": "character", "id": aria["id"], "order_index": 1},
        ],
    }).json()

    async def fake_generate_replies_from_message(**kwargs):
        reply_payload = MessageCreate(speaker_type="character", speaker_id=aria["id"], content="잡으로 생성 완료")
        return [conversation_service.add_message(kwargs["session"], kwargs["conversation_id"], reply_payload)]

    monkeypatch.setattr("app.api.conversations.generate_replies_from_message", fake_generate_replies_from_message)

    response = client.post(f"/conversations/{room['id']}/messages/jobs", json={
        "speaker_type": "user",
        "speaker_id": "user_001",
        "content": "천천히 답해줘",
    })

    assert response.status_code == 200
    body = response.json()
    assert body["incoming_message"]["content"] == "천천히 답해줘"
    job_id = body["job"]["id"]
    job = session.get(MessageGenerationJob, job_id)
    assert job is not None
    assert job.status in {"queued", "running", "completed"}
    assert job.incoming_message_id == body["incoming_message"]["id"]

    if job.status != "completed":
        asyncio.run(_run_message_generation_job_in_session(session, job_id))
        session.expire_all()
        job = session.get(MessageGenerationJob, job_id)

    assert job is not None
    assert job.status == "completed"
    assert len(job.generated_message_ids) == 1

    fetched = client.get(f"/conversations/{room['id']}/generation-jobs/{job_id}")
    assert fetched.status_code == 200
    assert fetched.json()["status"] == "completed"
    assert fetched.json()["generated_message_ids"] == job.generated_message_ids

    messages = client.get(f"/conversations/{room['id']}/messages").json()
    assert any(message["content"] == "천천히 답해줘" for message in messages)
    assert any(message["content"] == "잡으로 생성 완료" for message in messages)


def test_message_generation_job_reuses_client_request_id(client, session, monkeypatch):
    aria = client.post("/characters", json={"name": "아리아", "persona": "사용자를 챙긴다."}).json()
    room = client.post("/conversations", json={
        "mode": "user_character",
        "title": "잡 중복 방지 테스트",
        "participants": [
            {"type": "user", "id": "user_001", "order_index": 0},
            {"type": "character", "id": aria["id"], "order_index": 1},
        ],
    }).json()
    monkeypatch.setattr("app.api.conversations._start_message_generation_worker", lambda job_id: None)
    payload = {
        "speaker_type": "user",
        "speaker_id": "user_001",
        "content": "한 번만 저장돼야 해",
        "metadata": {"client_request_id": "req-once-001"},
    }

    first = client.post(f"/conversations/{room['id']}/messages/jobs", json=payload)
    second = client.post(f"/conversations/{room['id']}/messages/jobs", json=payload)

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["incoming_message"]["id"] == second.json()["incoming_message"]["id"]
    assert first.json()["job"]["id"] == second.json()["job"]["id"]
    messages = client.get(f"/conversations/{room['id']}/messages").json()
    user_messages = [message for message in messages if message["speaker_type"] == "user" and message["content"] == "한 번만 저장돼야 해"]
    assert len(user_messages) == 1


def test_message_generation_job_can_be_cancelled_before_claim(client, session, monkeypatch):
    aria = client.post("/characters", json={"name": "아리아", "persona": "사용자를 챙긴다."}).json()
    room = client.post("/conversations", json={
        "mode": "user_character",
        "title": "잡 취소 테스트",
        "participants": [
            {"type": "user", "id": "user_001", "order_index": 0},
            {"type": "character", "id": aria["id"], "order_index": 1},
        ],
    }).json()
    monkeypatch.setattr("app.api.conversations._start_message_generation_worker", lambda job_id: None)

    created = client.post(f"/conversations/{room['id']}/messages/jobs", json={
        "speaker_type": "user",
        "speaker_id": "user_001",
        "content": "아직 생성하지 마",
    })
    assert created.status_code == 200
    job_body = created.json()["job"]
    assert job_body["status"] == "queued"
    assert job_body["attempt_count"] == 0
    assert job_body["state_version"] == 0

    cancelled = client.post(
        f"/conversations/{room['id']}/generation-jobs/{job_body['id']}/cancel"
    )
    assert cancelled.status_code == 200
    cancelled_body = cancelled.json()
    assert cancelled_body["status"] == "cancelled"
    assert cancelled_body["cancel_requested_at"] is not None
    assert cancelled_body["completed_at"] is not None
    assert cancelled_body["state_version"] == 1

    fetched = client.get(
        f"/conversations/{room['id']}/generation-jobs/{job_body['id']}"
    )
    assert fetched.status_code == 200
    assert fetched.json()["status"] == "cancelled"
