import pytest
from datetime import datetime, timezone

from app.db.models import CharacterMemory, Conversation, ConversationParticipant, ConversationRelationshipState, Message, SceneState
from app.engine.prompts import messages_after_scene_summary_boundary
from app.services import conversation_service


def make_message(index: int, speaker_type: str = "character", content: str | None = None) -> Message:
    return Message(
        id=f"msg_{index:02d}",
        conversation_id="conv_compress",
        speaker_type=speaker_type,
        speaker_id=f"speaker_{index:02d}" if speaker_type == "character" else speaker_type,
        content=content or f"message {index}",
    )


def test_live_scene_preserves_previous_summary_after_compression_failure(session):
    conversation = Conversation(id="conv_stale_scene", title="stale", mode="user_character")
    scene = SceneState(
        conversation_id=conversation.id,
        summary="[Rolling Story Arc]\n- 다섯 턴 전까지 확정된 사건",
        last_compression_error="CharacterRuntimeError: invalid compression",
        last_compression_source_message_id="msg_boundary",
        compression_revision=7,
    )
    latest = Message(
        id="msg_stale_latest",
        conversation_id=conversation.id,
        speaker_type="user",
        speaker_id="user_001",
        content="지금 장면은 이미 다음 단계다",
    )
    session.add(conversation)
    session.add(scene)
    session.add(latest)
    session.commit()

    live = conversation_service.build_live_scene_state(session, conversation, scene)

    assert scene.summary == "[Rolling Story Arc]\n- 다섯 턴 전까지 확정된 사건"
    assert live.summary == scene.summary
    assert "지금 장면은 이미 다음 단계다" in live.current_conflict
    assert "지금 장면은 이미 다음 단계다" in live.last_event
    assert live.last_compression_error == scene.last_compression_error
    assert live.last_compression_source_message_id == "msg_boundary"
    assert live.compression_revision == 7


def test_compression_source_uses_previous_summary_and_only_supplied_transcript_batch():
    messages = [make_message(i, "character") for i in range(20)]
    messages[3] = make_message(3, "system", "초기 장면 지시 anchor")
    messages[7] = make_message(7, "user", "중요한 사용자 방향 anchor")
    scene = SceneState(
        conversation_id="conv_compress",
        world_seed="압축본에 다시 쓰면 안 되는 고정 세계관",
        user_description="압축본에 다시 쓰면 안 되는 유저 페르소나",
        summary="[Rolling Story Arc]\n- 대화에서 확정된 데뷔 준비 흐름과 char_a의 약속",
    )
    memories = [CharacterMemory(
        id="mem_compress",
        conversation_id="conv_compress",
        character_id="char_a",
        memory_type="fact",
        content="별도 저장소의 장기 기억",
        importance=5,
    )]

    source = conversation_service.build_scene_memory_source(scene, messages, memories=memories)

    assert "Previous Conversation Summary" in source
    assert "대화에서 확정된 데뷔 준비 흐름" in source
    assert "Chronological Messages To Fold" in source
    assert "초기 장면 지시 anchor" in source
    assert "중요한 사용자 방향 anchor" in source
    assert "message 19" in source
    assert source.count("speaker_type=") == 20
    assert "고정 세계관" not in source
    assert "유저 페르소나" not in source
    assert "별도 저장소의 장기 기억" not in source


def test_incremental_compression_folds_oldest_prefix_and_preserves_latest_raw_tail():
    messages = [make_message(i) for i in range(20)]
    scene = SceneState(conversation_id="conv_compress")

    batch, raw_tail = conversation_service.select_incremental_compression_batch(
        scene,
        messages,
        raw_tail_limit=12,
        batch_limit=48,
    )

    assert [message.id for message in batch] == [f"msg_{index:02d}" for index in range(8)]
    assert [message.id for message in raw_tail] == [f"msg_{index:02d}" for index in range(8, 20)]
    assert set(message.id for message in batch).isdisjoint(message.id for message in raw_tail)


def test_incremental_compression_resumes_strictly_after_persisted_boundary():
    messages = [make_message(i) for i in range(30)]
    scene = SceneState(
        conversation_id="conv_compress",
        summary="기존 대화 압축",
        last_compression_source_message_id="msg_07",
    )

    batch, raw_tail = conversation_service.select_incremental_compression_batch(
        scene,
        messages,
        raw_tail_limit=12,
        batch_limit=5,
    )

    assert [message.id for message in batch] == ["msg_08", "msg_09", "msg_10", "msg_11", "msg_12"]
    assert raw_tail[0].id == "msg_13"
    assert raw_tail[-1].id == "msg_29"


def test_generation_history_contains_only_messages_after_summary_boundary():
    messages = [make_message(i) for i in range(20)]
    scene = SceneState(
        conversation_id="conv_compress",
        summary="메시지 0~7을 포함한 압축",
        last_compression_source_message_id="msg_07",
    )

    raw_messages = messages_after_scene_summary_boundary(messages, scene)

    assert [message.id for message in raw_messages] == [f"msg_{index:02d}" for index in range(8, 20)]
    assert set(message.id for message in messages[:8]).isdisjoint(message.id for message in raw_messages)


def test_previous_story_arc_preserves_non_bulleted_reconstructed_block():
    summary = "\n".join([
        "[Current Scene State]",
        "연습실 퇴근 직전의 즉시 장면",
        "[Rolling Story Arc]",
        "아리아는 데뷔 준비 과정에서 사용자와 비밀 연애 관계를 형성했고, 사용자가 만든 곡으로 무대에서 인정받겠다는 목표를 갖고 있다.",
        "유나는 팀 내 센터 경쟁자로 긴장감을 만들었고, 서모아은 루미나 리더로 미나를 보호하고 훈련시키는 멘토 포지션이 되었다.",
        "신나비와 루미나 관련 사건 이후 미나는 인기가요 무대에서 자신이 사용자의 1순위임을 증명하려 한다.",
        "[Recent Events]",
        "- 최근 tail",
        "[Characters]",
        "- 아리아 • active",
    ])

    previous = conversation_service.previous_story_arc_for_compression(summary)

    assert any("비밀 연애 관계" in line for line in previous)
    assert any("서모아" in line for line in previous)
    assert any("인기가요 무대" in line for line in previous)


def test_scene_memory_summary_schema_excludes_advantage_and_relationship_slots():
    summary = "\n".join([
        "[Rolling Story Arc]",
        "- 두 인물이 복도에서 공개적으로 대치한 뒤 다음 질문에 답하기로 했다.",
    ])

    assert conversation_service.validate_scene_memory_summary(summary) == summary
    assert conversation_service.validate_scene_memory_summary(summary + "\nAdvantage: char_a 우위") is None
    assert conversation_service.validate_scene_memory_summary(summary + "\nRelationship/tension: 긴장 누적") is None
    assert conversation_service.validate_scene_memory_summary(summary + "\n[Recent Events]\n- 중복 섹션") is None


def test_relationship_dynamic_cleanup_rejects_abstract_placeholders():
    assert conversation_service.clean_relationship_dynamic_for_storage("긴장감과 호감이 누적된다") == ""
    assert conversation_service.clean_relationship_dynamic_for_storage("관계가 깊어지고 복잡해진다") == ""
    assert conversation_service.clean_relationship_dynamic_for_storage("char_a는 char_b를 경쟁자로 인정하지만 공개적으로 주도권을 양보하지 않는다")


def test_durable_memory_validation_rejects_official_battle_ledger_noise():
    update = conversation_service.validate_conversation_compression_update(
        {
            "scene": {},
            "memories": [
                {
                    "character_id": "__room__",
                    "memory_type": "event",
                    "content": "공식 승자 char_a, 패자 char_b, 리그 랭킹 1위로 확정",
                    "importance": 5,
                },
                {
                    "character_id": "char_a",
                    "memory_type": "fact",
                    "content": "char_a는 공개석상에서 약속을 어기지 않겠다고 확정했다",
                    "importance": 4,
                },
            ],
            "relationships": [],
        },
        {"char_a", "char_b"},
    )

    assert update is not None
    assert update["memories"] == []


def test_structured_scene_patch_updates_explicit_arrival_location():
    scene = SceneState(conversation_id="conv_compress", location="홍대 길거리")
    messages = [
        make_message(1, "system", "일과가 끝나고 저녁 미나가 유저의 집에 도착한다"),
        make_message(2, "character", "이제 들어가도 돼요?"),
    ]

    patch = conversation_service.build_structured_scene_patch(scene, messages)

    assert patch["location"] == "유저의 집"


def test_structured_scene_patch_detects_explicit_room_location_in_visible_text():
    scene = SceneState(conversation_id="conv_compress", location="집")
    messages = [
        make_message(1, "storytelling", "연습실의 거울 속에는 땀방울이 맺힌 채 두 멤버가 서 있다"),
        make_message(2, "character", "사용자, 봐주세요."),
    ]

    patch = conversation_service.build_structured_scene_patch(scene, messages)

    assert patch["location"] == "연습실"


def test_structured_scene_patch_recovers_location_from_existing_summary_when_tail_moved_on():
    scene = SceneState(
        conversation_id="conv_compress",
        location="홍대 길거리",
        summary="[Story Arc]\n- 직전 주요 사건은 System scene direction: 일과가 끝나고 저녁 미나가 유저의 집에 도착한다",
    )
    messages = [make_message(2, "character", "사용자, 저 왔어요.")]

    patch = conversation_service.build_structured_scene_patch(scene, messages)

    assert patch["location"] == "유저의 집"


def test_compact_scene_memory_fallback_refuses_rule_based_arc_rewrite():
    scene = SceneState(
        conversation_id="conv_compress",
        summary="[Story Arc]\n- 기존에는 홍대 길거리에서 초면 흐름이 시작됨\n[Recent Events]\n- 예전 사건",
        world_seed="유저와 초면인 아리아가 유저를 마주치는 스토리",
    )
    messages = [
        make_message(1, "system", "일과가 끝나고 저녁 미나가 유저의 집에 도착한다"),
        make_message(2, "character", "사용자, 저 왔어요."),
    ]

    summary = conversation_service.build_compact_scene_memory(scene, messages)

    assert summary == ""



@pytest.mark.asyncio
async def test_compression_graph_runs_existing_callbacks_in_order():
    from app.engine.compression_graph import run_compression_graph

    calls = []

    async def summarize_scene(state):
        calls.append("summarize_scene")
        return "scene summary"

    async def extract_memories(state):
        calls.append("extract_memories")
        return [{"content": "memory"}]


    def validate_update(state):
        calls.append("validate_update")
        assert state["scene_summary"] == "scene summary"
        return {
            "scene": {"summary": state["scene_summary"]},
            "memories": state["memory_updates"],
            "relationships": [],
            "battle_events": [],
        }

    result = await run_compression_graph(
        {
            "scene_state": SceneState(conversation_id="conv_compress"),
            "recent_messages": [],
            "character_ids": ["char_a"],
            "memories": [],
            "summarize_scene": summarize_scene,
            "extract_memories": extract_memories,
            "validate_update": validate_update,
        }
    )

    assert calls == [
        "summarize_scene",
        "extract_memories",
        "validate_update",
    ]
    assert result["validated_update"]["scene"]["summary"] == "scene summary"
    assert result["pipeline_steps"] == [
        "prepare_compression_source",
        "route_compression_tasks",
        "summarize_scene",
        "extract_durable_memory",
        "validate_update",
    ]


def test_compression_prompt_harness_exposes_budget_ledger():
    messages = [make_message(i, "character", f"recent beat {i}") for i in range(12)]
    scene = SceneState(
        conversation_id="conv_compress",
        summary="기존 장면 요약",
        compression_focus="관계와 장면만 압축하고 공식 결과는 제외",
    )

    memory = CharacterMemory(
        id="mem_compress",
        conversation_id="conv_compress",
        character_id="char_a",
        memory_type="fact",
        content="char_a는 약속을 공개적으로 확인했다",
        importance=4,
    )

    harness = conversation_service.build_compression_prompt_harness(
        scene_state=scene,
        recent_messages=messages,
        character_ids=["char_a", "char_b"],
        memories=[memory],
    )

    keys = [entry.key for entry in harness.ledger]
    assert keys == [
        "compression_scene_source",
        "compression_memory_source",
        "compression_constraints",
    ]
    assert harness.used_tokens > 0
    assert all(entry.source.startswith("compression.") for entry in harness.ledger)

    assert "message 0" not in harness.compiled_text


@pytest.mark.asyncio
async def test_compression_graph_attaches_harness_ledger_before_callbacks():
    from app.engine.compression_graph import run_compression_graph

    seen_ledger_keys = []

    async def summarize_scene(state):
        seen_ledger_keys.extend(entry.key for entry in state["compression_harness"].ledger)
        return "scene summary"

    result = await run_compression_graph({
        "scene_state": SceneState(conversation_id="conv_compress", summary="기존 요약"),
        "recent_messages": [make_message(i) for i in range(3)],
        "character_ids": ["char_a"],
        "relationship_states": [],
        "memories": [],
        "summarize_scene": summarize_scene,
        "extract_memories": lambda state: [],
        "extract_relationships": lambda state: [],
        "validate_update": lambda state: {"scene": {"summary": state["scene_summary"]}, "memories": [], "relationships": [], "battle_events": []},
    })

    assert seen_ledger_keys[:2] == ["compression_scene_source", "compression_memory_source"]
    assert result["compression_harness"].used_tokens > 0


def test_compression_preview_endpoint_returns_harness_ledger(client):
    create_response = client.post("/conversations", json={
        "mode": "character_character",
        "genre_mode": "battle",
        "title": "compression preview smoke",
        "scene": {
            "current_conflict": "둘이 공개적으로 대치 중",
            "compression_focus": "장면과 관계만 압축",
        },
    })
    assert create_response.status_code == 200
    conversation_id = create_response.json()["id"]

    response = client.get(f"/conversations/{conversation_id}/compression-preview")

    assert response.status_code == 200
    body = response.json()
    assert body["conversation_id"] == conversation_id
    assert body["pipeline_steps"] == ["prepare_compression_source", "route_compression_tasks"]
    assert body["selected_recent_message_count"] == 0
    assert "no_foldable_overflow" in body["warnings"]
    keys = [section["key"] for section in body["sections"]]
    assert "compression_scene_source" in keys
    assert "compression_relationship_source" not in keys
    assert all(section["source"].startswith("compression.") for section in body["sections"])


def _compression_relationship_update(*, trust_delta: int = 1) -> dict:
    return {
        "character_id": "char_a",
        "counterpart_type": "user",
        "counterpart_id": "user_001",
        "memories_to_add": [],
        "state_patch": {
            "trust_delta": trust_delta,
            "affinity_delta": 0,
            "tension_delta": 0,
            "conflict_delta": 0,
            "cooperation_delta": 0,
            "current_mood": "",
            "current_dynamic": "",
        },
        "unresolved_hooks": [],
    }


def test_compression_cutoff_is_idempotent_for_relationship_deltas(session):
    conversation = Conversation(id="conv_compression_claim", title="claim", mode="user_character")
    source = Message(
        id="msg_compression_source",
        conversation_id=conversation.id,
        speaker_type="character",
        speaker_id="char_a",
        content="현재 장면",
    )
    scene = SceneState(conversation_id=conversation.id, summary="이전 요약")
    relationship = ConversationRelationshipState(
        conversation_id=conversation.id,
        character_id="char_a",
        counterpart_type="user",
        counterpart_id="user_001",
    )
    session.add_all([conversation, source, scene, relationship])
    session.commit()
    session.refresh(relationship)
    expected_relationships = {("char_a", "user", "user_001"): relationship.updated_at}
    update = {
        "scene": {"summary": "새 요약"},
        "memories": [],
        "relationships": [_compression_relationship_update()],
        "battle_events": [],
    }

    first = conversation_service.apply_conversation_compression_update(
        session,
        conversation.id,
        update,
        source_message_id=source.id,
        expected_revision=0,
        expected_relationship_updated_at=expected_relationships,
    )
    second = conversation_service.apply_conversation_compression_update(
        session,
        conversation.id,
        update,
        source_message_id=source.id,
        expected_revision=0,
        expected_relationship_updated_at=expected_relationships,
    )

    session.expire_all()
    persisted_scene = session.get(SceneState, conversation.id)
    persisted_relationship = session.get(
        ConversationRelationshipState,
        (conversation.id, "char_a", "user", "user_001"),
    )
    assert first is True
    assert second is False
    assert persisted_scene.summary == "새 요약"
    assert persisted_scene.last_compression_source_message_id == source.id
    assert persisted_scene.compression_revision == 1
    assert persisted_relationship.trust_level == 0


def test_compression_rejects_result_when_folded_prefix_ids_changed(session):
    conversation = Conversation(id="conv_compression_prefix_guard", title="prefix guard", mode="user_character")
    messages = [Message(
        id=f"msg_prefix_{index:02d}",
        conversation_id=conversation.id,
        speaker_type="character",
        speaker_id="char_a",
        content=f"prefix {index}",
    ) for index in range(14)]
    scene = SceneState(conversation_id=conversation.id, summary="기존 요약")
    session.add_all([conversation, *messages, scene])
    session.commit()

    applied = conversation_service.apply_conversation_compression_update(
        session,
        conversation.id,
        {"scene": {"summary": "적용되면 안 되는 요약"}, "relationships": []},
        source_message_id="msg_prefix_01",
        expected_revision=0,
        expected_boundary_message_id=None,
        folded_message_ids=["msg_prefix_00", "msg_replaced_01"],
    )

    session.expire_all()
    persisted = session.get(SceneState, conversation.id)
    assert applied is False
    assert persisted.summary == "기존 요약"
    assert persisted.last_compression_source_message_id is None
    assert persisted.compression_revision == 0


@pytest.mark.asyncio
async def test_compression_applies_old_prefix_when_new_message_arrives_during_llm(session, monkeypatch):
    conversation = Conversation(id="conv_stale_write", title="stale write", mode="user_character")
    messages = [Message(
        id=f"msg_stale_{index:02d}",
        conversation_id=conversation.id,
        speaker_type="character",
        speaker_id="char_a",
        content=f"압축 대상 {index}",
    ) for index in range(14)]
    scene = SceneState(conversation_id=conversation.id, summary="보존할 이전 요약")
    relationship = ConversationRelationshipState(
        conversation_id=conversation.id,
        character_id="char_a",
        counterpart_type="user",
        counterpart_id="user_001",
    )
    session.add_all([conversation, *messages, scene, relationship])
    session.commit()

    async def delayed_compression(**kwargs):
        assert [message.id for message in kwargs["recent_messages"]] == ["msg_stale_00", "msg_stale_01"]
        session.add(Message(
            id="msg_stale_newer",
            conversation_id=conversation.id,
            speaker_type="user",
            speaker_id="user_001",
            content="LLM 대기 중 추가된 최신 메시지",
        ))
        session.commit()
        return {
            "scene": {"summary": "오래된 prefix를 병합한 새 요약"},
            "memories": [],
            "relationships": [_compression_relationship_update()],
            "battle_events": [],
        }

    monkeypatch.setattr(conversation_service, "summarize_conversation_state_with_llm", delayed_compression)

    result = await conversation_service.update_scene_orchestration_summary(
        session,
        conversation.id,
        messages,
        character_ids=["char_a"],
        llm_client=object(),
    )

    session.expire_all()
    persisted_scene = session.get(SceneState, conversation.id)
    persisted_relationship = session.get(
        ConversationRelationshipState,
        (conversation.id, "char_a", "user", "user_001"),
    )
    assert result.conversation_id == conversation.id
    assert persisted_scene.summary == "오래된 prefix를 병합한 새 요약"
    assert persisted_scene.last_compression_source_message_id == "msg_stale_01"
    assert persisted_scene.compression_revision == 1
    assert persisted_scene.last_compression_error is None
    assert persisted_relationship.trust_level == 0
    assert session.get(Message, "msg_stale_newer") is not None


def test_compression_skips_relationship_pair_changed_after_snapshot(session):
    conversation = Conversation(id="conv_relationship_revision", title="relationship revision", mode="user_character")
    source = Message(
        id="msg_relationship_source",
        conversation_id=conversation.id,
        speaker_type="character",
        speaker_id="char_a",
        content="관계 갱신 장면",
    )
    scene = SceneState(conversation_id=conversation.id, summary="이전 요약")
    relationship = ConversationRelationshipState(
        conversation_id=conversation.id,
        character_id="char_a",
        counterpart_type="user",
        counterpart_id="user_001",
    )
    session.add_all([conversation, source, scene, relationship])
    session.commit()
    session.refresh(relationship)
    expected_relationships = {("char_a", "user", "user_001"): relationship.updated_at}

    conversation_service.apply_continuity_update(
        session,
        conversation_id=conversation.id,
        character_id="char_a",
        counterpart_type="user",
        counterpart_id="user_001",
        update={
            "memories_to_add": [],
            "state_patch": {"trust_delta": 2, "current_dynamic": ""},
            "unresolved_hooks": [],
        },
    )

    applied = conversation_service.apply_conversation_compression_update(
        session,
        conversation.id,
        {
            "scene": {"summary": "새 장면 요약"},
            "memories": [],
            "relationships": [_compression_relationship_update(trust_delta=1)],
            "battle_events": [],
        },
        source_message_id=source.id,
        expected_revision=0,
        expected_relationship_updated_at=expected_relationships,
    )

    session.expire_all()
    persisted_scene = session.get(SceneState, conversation.id)
    persisted_relationship = session.get(
        ConversationRelationshipState,
        (conversation.id, "char_a", "user", "user_001"),
    )
    assert applied is True
    assert persisted_scene.summary == "새 장면 요약"
    assert persisted_relationship.trust_level == 2


@pytest.mark.asyncio
async def test_compression_retries_with_fallback_after_primary_parse_failure(session, monkeypatch):
    conversation = Conversation(id="conv_compression_fallback", title="fallback", mode="user_character")
    sources = [Message(
        id=f"msg_compression_fallback_{index:02d}",
        conversation_id=conversation.id,
        speaker_type="character",
        speaker_id="char_a",
        content=f"폴백 압축 대상 장면 {index}",
    ) for index in range(13)]
    scene = SceneState(conversation_id=conversation.id, summary="기존 압축 요약")
    session.add_all([conversation, *sources, scene])
    session.commit()
    primary_client = object()
    fallback_client = object()
    attempts = []

    async def fake_compression(**kwargs):
        attempts.append(kwargs["llm_client"])
        if kwargs["llm_client"] is primary_client:
            raise ValueError("HTTP 200 but invalid compression JSON")
        return {
            "scene": {"summary": "폴백으로 생성된 새 압축 요약"},
            "memories": [],
            "relationships": [],
            "battle_events": [],
        }

    monkeypatch.setattr(conversation_service, "summarize_conversation_state_with_llm", fake_compression)

    result = await conversation_service.update_scene_orchestration_summary(
        session,
        conversation.id,
        sources,
        llm_client=primary_client,
        fallback_llm_client=fallback_client,
        character_ids=["char_a"],
    )

    session.expire_all()
    persisted = session.get(SceneState, conversation.id)
    assert attempts == [primary_client, fallback_client]
    assert result.summary == "폴백으로 생성된 새 압축 요약"
    assert persisted.summary == "폴백으로 생성된 새 압축 요약"
    assert persisted.compression_revision == 1
    assert persisted.last_compression_source_message_id == sources[0].id
    assert persisted.last_compression_error is None


@pytest.mark.asyncio
async def test_compression_all_attempts_fail_preserves_existing_state(session, monkeypatch):
    previous_compressed_at = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)
    conversation = Conversation(id="conv_compression_preserve", title="preserve", mode="user_character")
    previous_source = Message(
        id="msg_previous_compression_source",
        conversation_id=conversation.id,
        speaker_type="character",
        speaker_id="char_a",
        content="이전 압축 경계",
    )
    sources = [Message(
        id=f"msg_compression_preserve_{index:02d}",
        conversation_id=conversation.id,
        speaker_type="character",
        speaker_id="char_a",
        content=f"기존 압축을 지워선 안 되는 장면 {index}",
    ) for index in range(13)]
    scene = SceneState(
        conversation_id=conversation.id,
        summary="절대 지우면 안 되는 기존 압축",
        compression_revision=7,
        last_compression_source_message_id="msg_previous_compression_source",
        last_compressed_at=previous_compressed_at,
    )
    session.add_all([conversation, previous_source, *sources, scene])
    session.commit()
    primary_client = object()
    fallback_client = object()
    attempts = []

    async def fake_compression(**kwargs):
        attempts.append(kwargs["llm_client"])
        if kwargs["llm_client"] is primary_client:
            return {
                "scene": {"summary": "   "},
                "memories": [],
                "relationships": [],
                "battle_events": [],
            }
        raise ValueError("fallback response JSON validation failed")

    monkeypatch.setattr(conversation_service, "summarize_conversation_state_with_llm", fake_compression)

    result = await conversation_service.update_scene_orchestration_summary(
        session,
        conversation.id,
        [previous_source, *sources],
        llm_client=primary_client,
        fallback_llm_client=fallback_client,
        character_ids=["char_a"],
    )

    session.expire_all()
    persisted = session.get(SceneState, conversation.id)
    live = conversation_service.build_live_scene_state(session, conversation, persisted)
    assert attempts == [primary_client, fallback_client]
    assert result.summary == "절대 지우면 안 되는 기존 압축"
    assert persisted.summary == "절대 지우면 안 되는 기존 압축"
    assert persisted.compression_revision == 7
    assert persisted.last_compression_source_message_id == "msg_previous_compression_source"
    assert persisted.last_compressed_at.replace(tzinfo=timezone.utc) == previous_compressed_at
    assert "primary" in persisted.last_compression_error
    assert "fallback" in persisted.last_compression_error
    assert live.summary == persisted.summary
