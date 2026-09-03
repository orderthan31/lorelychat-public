import json

from sqlmodel import select

from app.db.models import CharacterMemory, Message, SceneState
from app.engine.llm_client import LLMResponse
from app.services.conversation_service import (
    apply_continuity_update,
    apply_conversation_compression_update,
    build_compact_scene_memory,
    league_memory_key,
    scene_summary_copies_recent_raw_text,
    summarize_conversation_state_with_llm,
    summarize_scene_memory_with_llm,
    validate_conversation_compression_update,
    validate_continuity_update,
    validate_scene_memory_summary,
)


class FakeSceneSummaryLLM:
    async def chat(self, messages, *, response_format=None):
        assert response_format is None
        assert "Room-specific memory/relationship update contract" not in messages[1]["content"]
        assert "Room-specific compression focus" not in messages[1]["content"]
        assert "You receive exactly two sources" in messages[0]["content"]
        assert "Completely ignore private thought" in messages[0]["content"]
        assert "single-section structure" in messages[0]["content"]
        assert "[Rolling Story Arc]" in messages[0]["content"]
        assert "previous summary as durable history" in messages[0]["content"]
        return LLMResponse(content=(
            "[Rolling Story Arc]\n"
            "- 아리아와 루나이 방의 긴장 흐름을 공유했고, 아리아가 다음 진행을 정리하기로 했다."
        ))


class SemanticRepairSceneSummaryLLM:
    def __init__(self):
        self.calls = []

    async def chat(self, messages, *, response_format=None):
        assert response_format is None
        self.calls.append(messages)
        assert "FULL_PREVIOUS_ARC_TAIL" in messages[1]["content"]
        if len(self.calls) == 1:
            return LLMResponse(content="[Rolling Story Arc]\n- " + ("세부 장면을 나열한 문장 " * 20))
        assert messages[-2]["role"] == "assistant"
        repair = messages[-1]["content"]
        assert "Rewrite it semantically" in repair
        assert "Do not cut sentences, truncate bullets" in repair
        assert "Merge neighboring details into larger major events" in repair
        return LLMResponse(content=(
            "[Rolling Story Arc]\n"
            "- 아리아와 루나은 반복된 대치 끝에 서로의 입장을 확인하고 다음 진행을 함께 정리하기로 했다."
        ))


class FakeConversationCompressionLLM:
    def __init__(self):
        self.calls = []

    async def chat(self, messages, *, response_format=None):
        system_prompt = messages[0]["content"]
        user_prompt = messages[1]["content"]
        self.calls.append(system_prompt)
        if "incrementally compress character-chat transcript" in system_prompt:
            assert response_format is None
            assert "Room-specific memory/relationship update contract" not in user_prompt
            return LLMResponse(content=(
                "[Rolling Story Arc]\n"
                "- 미나와 에코의 경기 흐름이 이어졌고, 둘은 결과를 임의로 확정하지 않은 채 다음 대결 의지를 드러냈다."
            ))
        if "extract only durable long-term memory" in system_prompt:
            assert response_format == {"type": "json_object"}
            assert "Room-specific memory/relationship update contract: 리그 승점표만 장부로 저장" in user_prompt
            assert "Relationship archetype contract: obsessive_low_trust" in user_prompt
            assert "Allowed reward: visible reaction, attention, jealousy, curiosity, softened tone, or partial vulnerability." in user_prompt
            assert "Blocked outcome: instant stable commitment or fully secure trust unless the room history explicitly earns it." in user_prompt
            if "Room cast role contract:" in user_prompt:
                assert "char_mina: primary · 메인 캐릭터" in user_prompt
                assert "char_reika: rival · 라이벌/견제자" in user_prompt
            return LLMResponse(content=json.dumps({"memories": []}, ensure_ascii=False))
        if "person-to-person relationship state" in system_prompt:
            assert response_format == {"type": "json_object"}
            assert "Room-specific memory/relationship update contract: 리그 승점표만 장부로 저장" in user_prompt
            assert "Relationship archetype contract: obsessive_low_trust" in user_prompt
            assert "Allowed reward: visible reaction, attention, jealousy, curiosity, softened tone, or partial vulnerability." in user_prompt
            assert "Blocked outcome: instant stable commitment or fully secure trust unless the room history explicitly earns it." in user_prompt
            if "Room cast role contract:" in user_prompt:
                assert "char_mina: primary · 메인 캐릭터" in user_prompt
                assert "char_reika: rival · 라이벌/견제자" in user_prompt
            assert "Official domain state is read-only constraint" in user_prompt
            return LLMResponse(content=json.dumps({"relationships": []}, ensure_ascii=False))
        raise AssertionError(system_prompt)


def test_scene_memory_validation_preserves_structure_and_rejects_raw_labels():
    summary = (
        "[Rolling Story Arc]\n"
        "- 두 인물이 복도에서 공개적으로 대치했고, char_a는 다음 질문에 답하기로 했다."
    )

    validated = validate_scene_memory_summary(summary)

    assert validated == summary
    assert validated.startswith("[Rolling Story Arc]\n")
    assert validated.count("\n- ") == 1

    raw = summary.replace("두 인물이 복도에서 공개적으로 대치했고", "action=고개를 든다; stance=괜찮아")
    assert validate_scene_memory_summary(raw) is None
    mechanical = summary.replace("char_a는 다음 질문에 답하기로 했다.", "행동=복도 대치 / 대사방향=말의 여운")
    assert validate_scene_memory_summary(mechanical) is None


def test_scene_memory_validation_rejects_legacy_extra_sections():
    summary = (
        "[Story Arc]\n"
        "- 아린이 전학생으로 등장하여 강예나의 권위에 대립함\n"
        "[Recent Events]\n"
        "- 김채원이 과거 유지민과 센터 자리를 겨루던 기억을 되찾음\n"
        "[Characters]\n"
        "- 강예나 [학생] • recent\n"
        "  - 아린의 지도로 유지민 정복을 목표로 특훈함"
    )

    assert validate_scene_memory_summary(summary) is None


def test_deterministic_scene_memory_does_not_synthesize_or_truncate_arc():
    scene = SceneState(conversation_id="conv_1", mood="긴장", tension_level=1)
    recent = [
        Message(conversation_id="conv_1", speaker_type="character", speaker_id="char_a", content="그 말은 그냥 넘길 수 없어.", emotion="긴장", action="한 걸음 다가선다"),
        Message(conversation_id="conv_1", speaker_type="character", speaker_id="char_b", content="나도 물러설 생각 없어.", emotion="단호함"),
    ]

    summary = build_compact_scene_memory(scene, recent)

    assert summary == ""


def test_scene_memory_keeps_only_durable_rolling_story_arc():
    scene = SceneState(
        conversation_id="conv_1",
        mood="소란스러움",
        location="호텔 스위트룸",
        current_conflict="지금은 이미 지나간 질문",
        last_event="지금은 이미 지나간 장난",
        summary=(
            "[Rolling Story Arc]\n"
            "- 아이돌 연습생 아리아와 프로듀서 사용자는 비밀 연애와 데뷔 경쟁을 함께 버티는 관계다\n"
            "- 일본 직캠이 한국 알고리즘까지 퍼지며 미나의 인기가 급상승할 조짐이 생겼다"
        ),
    )
    recent = [
        Message(id="msg_timeline_user", conversation_id="conv_1", speaker_type="user", speaker_id="user_001", content="으악 뭐야 너네들"),
        Message(id="msg_timeline_mina", conversation_id="conv_1", speaker_type="character", speaker_id="char_mina", content="다들 씻고 리허설 준비해야지.", emotion="당황", action="멤버들을 욕실 쪽으로 민다"),
    ]

    summary = build_compact_scene_memory(scene, recent)

    assert summary.startswith("[Rolling Story Arc]")
    assert "[Current Scene State]" not in summary
    assert "[Recent Events]" not in summary
    assert "[Characters]" not in summary
    assert "비밀 연애" in summary
    assert "일본 직캠" in summary
    assert "으악 뭐야" not in summary
    assert "지나간 질문" not in summary
    assert "지나간 장난" not in summary


def test_scene_memory_does_not_promote_character_memories_into_rolling_story_arc():
    scene = SceneState(
        conversation_id="conv_1",
        summary=(
            "[Rolling Story Arc]\n"
            "- 미나와 사용자는 데뷔 준비를 함께 이어왔다\n"
            "- char_old 장기 기억: 이전에 잘못 섞인 오프스테이지 캐릭터 기억\n"
            "[Recent Events]\n"
            "- 이전 장면\n"
            "[Characters]\n"
            "- char_mina • recent\n"
            "  - 대기"
        ),
    )
    recent = [
        Message(id="msg_user", conversation_id="conv_1", speaker_type="user", speaker_id="user_001", content="리허설 가자"),
        Message(id="msg_mina", conversation_id="conv_1", speaker_type="character", speaker_id="char_mina", content="응, 준비됐어."),
    ]
    memories = [
        CharacterMemory(
            id="mem_room",
            conversation_id="conv_1",
            character_id="__room__",
            memory_type="user_note",
            content="방 전체 장기 기억 원문이 장면 요약에 직접 섞이면 안 된다",
            importance=5,
        ),
        CharacterMemory(
            id="mem_offstage",
            conversation_id="conv_1",
            character_id="char_offstage",
            memory_type="user_note",
            content="방에 없는 캐릭터의 장기 기억",
            importance=5,
        ),
    ]

    summary = build_compact_scene_memory(scene, recent, memories=memories)

    assert summary == ""


def test_compact_scene_memory_does_not_persist_transient_current_state():
    scene = SceneState(
        conversation_id="conv_1",
        current_conflict="아까 오래된 질문이 아직 남아있다",
        last_event="이전 장면의 낡은 이벤트",
        summary="[Rolling Story Arc]\n- 미나와 사용자는 데뷔 준비를 함께 이어왔다",
    )
    recent = [
        Message(id="msg_old_user", conversation_id="conv_1", speaker_type="user", speaker_id="user_001", content="아까 오래된 질문"),
        Message(id="msg_story", conversation_id="conv_1", speaker_type="storytelling", speaker_id="storyteller", content="연습실 거울 앞에서 두 멤버가 사용자의 반응을 기다린다"),
    ]

    summary = build_compact_scene_memory(scene, recent)

    assert summary.startswith("[Rolling Story Arc]")
    assert "미나와 사용자는 데뷔 준비" in summary
    assert "연습실 거울 앞" not in summary
    assert "아까 오래된 질문" not in summary
    assert "이전 장면의 낡은 이벤트" not in summary


def test_scene_memory_validation_accepts_only_single_rolling_arc_section():
    summary = (
        "[Rolling Story Arc]\n"
        "- 아리아와 프로듀서 사용자는 비밀 연애와 데뷔 경쟁을 함께 버텼고, 멤버들은 리허설 준비에 들어갔다."
    )

    assert validate_scene_memory_summary(summary) == summary
    assert validate_scene_memory_summary(summary + "\n[Characters]\n- char_mina") is None


def test_scene_summary_raw_copy_detector_rejects_label_stripped_transcript():
    scene = SceneState(conversation_id="conv_1")
    recent = [
        Message(
            conversation_id="conv_1",
            speaker_type="character",
            speaker_id="char_a",
            content="이 문장은 압축 요약에 그대로 들어가면 안 되는 아주 긴 직전 대화 원문입니다. 캐릭터가 방금 했던 말을 거의 그대로 복사한 상태입니다.",
            action="손을 뻗어 상대를 붙잡고 같은 문장을 다시 반복하려는 장면 묘사입니다.",
        )
    ]
    bad_summary = (
        "[Rolling Story Arc]\n"
        "- 이 문장은 압축 요약에 그대로 들어가면 안 되는 아주 긴 직전 대화 원문입니다. 캐릭터가 방금 했던 말을 거의 그대로 복사한 상태입니다."
    )

    assert validate_scene_memory_summary(bad_summary)
    assert scene_summary_copies_recent_raw_text(bad_summary, recent)
    fallback = build_compact_scene_memory(scene, recent)
    assert "아주 긴 직전 대화 원문" not in fallback


def test_scene_memory_source_excludes_thought_and_uses_visible_fields_only():
    from app.services.conversation_service import build_scene_memory_source

    scene = SceneState(conversation_id="conv_1")
    recent = [Message(
        conversation_id="conv_1",
        speaker_type="character",
        speaker_id="char_a",
        content="겉으로 하는 대사",
        action="눈앞에서 밀어붙인다",
        emotion="도발",
        thought="이 속마음은 압축에 들어가면 안 된다",
    )]

    source = build_scene_memory_source(scene, recent)

    assert "dialogue/directive=겉으로 하는 대사" in source
    assert "action=눈앞에서 밀어붙인다" in source
    assert "emotion=도발" in source
    assert "thought=" not in source
    assert "이 속마음은 압축에 들어가면 안 된다" not in source


async def test_fast_scene_summary_rejects_invalid_draft_without_second_primary_call_or_pretruncation():
    scene = SceneState(
        conversation_id="conv_semantic_repair",
        summary="[Rolling Story Arc]\n- " + ("이전 사건 전체 " * 260) + "FULL_PREVIOUS_ARC_TAIL",
    )
    recent = [
        Message(
            id="msg_semantic_repair",
            conversation_id=scene.conversation_id,
            speaker_type="character",
            speaker_id="char_aria",
            content="이제 다음 진행을 정리하자.",
        )
    ]
    llm = SemanticRepairSceneSummaryLLM()

    summary = await summarize_scene_memory_with_llm(scene, recent, llm_client=llm)  # type: ignore[arg-type]

    assert len(llm.calls) == 1
    assert summary is None


async def test_scene_summary_pass_ignores_custom_compression_focus():
    scene = SceneState(
        conversation_id="conv_1",
        compression_focus="리그 승점표만 장부로 저장",
        summary="[Scene memory compact]\nVisible situation: 이전 상황",
    )
    recent = [Message(conversation_id="conv_1", speaker_type="character", speaker_id="char_aria", content="일단 흐름부터 잡자.")]

    summary = await summarize_scene_memory_with_llm(scene, recent, llm_client=FakeSceneSummaryLLM())

    assert summary
    assert "리그 승점표" not in summary
    assert summary.startswith("[Rolling Story Arc]")
    assert "[Current Scene State]" not in summary
    assert "[Recent Events]" not in summary
    assert "[Characters]" not in summary


async def test_conversation_compression_prompt_splits_scene_summary_from_custom_memory_contract():
    scene = SceneState(
        conversation_id="conv_1",
        compression_focus="리그 승점표만 장부로 저장",
        relationship_archetype="obsessive_low_trust",
        summary="[Scene memory compact]\nVisible situation: 경기 전",
    )
    recent = [
        Message(conversation_id="conv_1", speaker_type="character", speaker_id="char_mina", content="이번 경기는 내가 가져갔어.", emotion="자신감"),
        Message(conversation_id="conv_1", speaker_type="character", speaker_id="char_reika", content="다음엔 안 져.", emotion="분함"),
    ]

    update = await summarize_conversation_state_with_llm(
        scene_state=scene,
        recent_messages=recent,
        character_ids=["char_mina", "char_reika"],
        relationship_states=[],
        memories=[],
        llm_client=FakeConversationCompressionLLM(),
        room_cast_roles={"char_mina": "primary", "char_reika": "rival"},
    )

    assert update
    assert update["memories"] == []
    assert update["scene"]["summary"].startswith("[Rolling Story Arc]")
    assert "[Current Scene State]" not in update["scene"]["summary"]
    assert "[Recent Events]" not in update["scene"]["summary"]
    assert "[Characters]" not in update["scene"]["summary"]
    assert "승점표" not in update["scene"]["summary"]


def test_relationship_archetype_guard_rejects_instant_stable_commitment_memories():
    result = validate_conversation_compression_update(
        {
            "scene": {},
            "memories": [
                {"character_id": "char_mina", "memory_type": "event", "content": "미나는 유저를 완전히 신뢰하고 안정적인 연인 관계를 확정했다.", "importance": 5},
                {"character_id": "char_mina", "memory_type": "fact", "content": "앞으로 미나는 유저가 관심을 보이면 질투와 호기심으로 반응하는 경향이 있다.", "importance": 5},
            ],
            "relationships": [],
        },
        {"char_mina"},
        relationship_archetype="obsessive_low_trust",
    )

    assert result["memories"] == []


async def test_conversation_compression_returns_structured_scene_fields_from_recent_messages():
    scene = SceneState(
        conversation_id="conv_1",
        summary="[Story Arc]\n- 이전 장면\n[Recent Events]\n- 이전 사건\n[Characters]\n- char_mina • recent\n  - 대기",
        location="홍대 길거리",
        mood="초면",
        current_conflict="",
        last_event="미나가 유저 집에 도착",
        compression_focus="리그 승점표만 장부로 저장",
        relationship_archetype="obsessive_low_trust",
    )
    recent = [
        Message(id="msg_scene_system", conversation_id="conv_1", speaker_type="system", speaker_id="system", content="일과가 끝나고 저녁, 미나가 카페 안쪽 자리로 이동한다."),
        Message(id="msg_scene_user", conversation_id="conv_1", speaker_type="user", speaker_id="user_001", content="왜 갑자기 여기로 부른 거야?"),
        Message(id="msg_scene_mina", conversation_id="conv_1", speaker_type="character", speaker_id="char_mina", content="나도 확실히 확인하고 싶은 게 있어.", emotion="조심스러움", action="컵을 감싼 손에 힘을 준다"),
    ]

    update = await summarize_conversation_state_with_llm(
        scene_state=scene,
        recent_messages=recent,
        character_ids=["char_mina"],
        relationship_states=[],
        memories=[],
        llm_client=FakeConversationCompressionLLM(),
        genre_mode="romance",
    )

    assert update
    assert update["scene"]["location"] == "카페"
    assert update["scene"]["mood"] == "초면"
    assert update["scene"]["current_conflict"]
    assert "확인" in update["scene"]["current_conflict"] or "왜" in update["scene"]["current_conflict"]
    assert update["scene"]["last_event"] != "미나가 유저 집에 도착"
    assert "확인" in update["scene"]["last_event"] or "컵" in update["scene"]["last_event"]


def test_relationship_hooks_drop_raw_transcript_markers(session):
    update = validate_continuity_update({
        "memories_to_add": [],
        "state_patch": {"current_dynamic": "action=고개를 든다 dialogue=뭐라고 말했다"},
        "unresolved_hooks": [
            "dialogue=다음에 말하기 | action=고개를 든다",
            "다음에는 둘의 약속을 확인한다",
        ],
    })

    assert update["state_patch"]["current_dynamic"] == ""
    assert update["unresolved_hooks"] == ["다음에는 둘의 약속을 확인한다"]

    state = apply_continuity_update(
        session,
        conversation_id="conv_1",
        character_id="char_a",
        update=update,
        counterpart_type="character",
        counterpart_id="char_b",
    )
    assert state.unresolved_hooks == ["다음에는 둘의 약속을 확인한다"]


def test_compression_update_does_not_create_automatic_long_term_memories(session):
    apply_conversation_compression_update(session, "conv_memory_gate", {
        "scene": {"current_conflict": "아이돌 활동 장면을 이어간다"},
        "relationships": [],
        "memories": [{
            "character_id": "char_mina",
            "memory_type": "event",
            "content": "압축이 만든 이벤트는 장기메모리로 자동 저장되면 안 된다.",
            "importance": 5,
        }],
    })

    memories = list(session.exec(select(CharacterMemory).where(CharacterMemory.conversation_id == "conv_memory_gate")).all())
    assert memories == []


def test_validate_compression_update_drops_llm_memory_payloads():
    update = validate_conversation_compression_update({
        "scene": {"current_conflict": "아이돌 활동 장면"},
        "relationships": [],
        "memories": [{
            "character_id": "char_mina",
            "memory_type": "fact",
            "content": "압축 LLM이 만든 팩트는 user_note가 아니므로 버린다.",
            "importance": 5,
        }],
    }, {"char_mina"})

    assert update["memories"] == []


def test_compression_rejects_common_room_as_relationship_counterpart():
    update = validate_conversation_compression_update({
        "scene": {
            "summary": (
                "[Scene memory compact]\n"
                "Visible situation: 테스트 장면의 공개 반응만 남아 있다.\n"
                "Recent visible beat: 테스트 장면의 눈에 보이는 반응만 이어간다.\n"
                "Character visible states:\n"
                "- char_a: 대기\n"
                "Carry forward: 이어가기"
            ),
        },
        "relationships": [{
            "character_id": "char_a",
            "counterpart_type": "character",
            "counterpart_id": "__room__",
            "current_dynamic": "방 전체와 관계가 있다",
        }],
    }, {"char_a"})

    assert update["relationships"] == []


def test_league_memories_no_longer_use_compression_ledger_slots(session):
    assert league_memory_key("리그 승점표: 아리아 승점3, 에코 승점0") == "league_standings"
    existing = CharacterMemory(
        id="mem_existing",
        conversation_id="conv_league",
        character_id="__room__",
        memory_type="fact",
        content="리그 승점표: 아리아 승점3, 에코 승점0",
        importance=4,
    )
    session.add(existing)
    session.commit()

    apply_conversation_compression_update(session, "conv_league", {
        "scene": {},
        "relationships": [],
        "memories": [{
            "character_id": "char_mina",
            "memory_type": "fact",
            "content": "리그 승점표: 아리아 승점6, 에코 승점0, 세라 승점3",
            "importance": 5,
        }],
    })

    memories = list(session.exec(select(CharacterMemory).where(CharacterMemory.conversation_id == "conv_league")).all())
    assert len(memories) == 1
    assert memories[0].id == "mem_existing"
    assert memories[0].character_id == "__room__"
    assert "아리아 승점3" in memories[0].content
    assert "아리아 승점6" not in memories[0].content
