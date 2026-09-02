from app.engine.prompt_harness import PromptSection, approx_tokens, compile_prompt_harness
import pytest

from app.engine.prompts import ContextCoverageError, build_character_prompt_harness, build_multi_character_prompt_harness, format_message_for_context, messages_after_scene_summary_boundary, prompt_budget_for_context, select_prompt_recent_messages
from app.db.models import Character, Message, SceneState


def test_prompt_harness_ledger_keeps_complete_sections_even_when_diagnostic_budget_is_small():
    long_memory = "DB long-term memory " * 200
    harness = compile_prompt_harness(
        sections=[
            PromptSection(
                key="identity",
                title="Identity",
                content="아리아 identity lock",
                source="character.card",
                included_reason="active_character",
                budget_tokens=40,
                required=True,
            ),
            PromptSection(
                key="continuity_state_by_character",
                title="Continuity state",
                content=long_memory,
                source="conversation.local_memory",
                included_reason="db_long_term_memory",
                budget_tokens=40,
                required=False,
            ),
            PromptSection(
                key="recent_messages",
                title="Recent messages",
                content="latest user turn and reply anchor",
                source="conversation.messages.tail",
                included_reason="recent_tail",
                budget_tokens=40,
                required=True,
            ),
        ],
        total_budget_tokens=35,
    )

    compiled = harness.compiled_text
    entries = {entry.key: entry for entry in harness.ledger}
    assert "[Identity]" in compiled
    assert "[Recent messages]" in compiled
    assert "[Continuity state]" not in compiled
    assert entries["continuity_state_by_character"].included is False
    assert entries["continuity_state_by_character"].excluded_reason == "total_budget_exhausted"
    assert harness.used_tokens <= 35 + entries["identity"].used_tokens + entries["recent_messages"].used_tokens

    continuity_entry = entries["continuity_state_by_character"]
    assert continuity_entry.included is False
    assert continuity_entry.excluded_reason == "total_budget_exhausted"
    assert continuity_entry.source == "conversation.local_memory"
    assert continuity_entry.included_reason == "db_long_term_memory"
    assert continuity_entry.approx_tokens > 40
    assert continuity_entry.budget_tokens == 40

    assert harness.total_budget_tokens == 35
    assert harness.used_tokens == approx_tokens(harness.compiled_text)
    assert harness.used_tokens > harness.total_budget_tokens


def test_recent_character_messages_include_character_name_to_prevent_user_misattribution():
    message = Message(id="m_harin", conversation_id="conv", speaker_type="character", speaker_id="char_harin", content="리나가 1번을 맡아야 해")

    line = format_message_for_context(message, character_names={"char_harin": "서모아"})

    assert "character:서모아(char_harin)" in line
    assert "character:char_harin |" not in line


def test_automatic_context_keeps_contiguous_boundary_suffix_instead_of_count_tail():
    messages = [
        Message(
            id=f"m_{index:02d}",
            conversation_id="conv",
            speaker_type="user" if index % 2 == 0 else "character",
            speaker_id="user_001" if index % 2 == 0 else "char_aria",
            content=f"message {index}",
        )
        for index in range(16)
    ]

    legacy = select_prompt_recent_messages(messages)
    automatic = select_prompt_recent_messages(messages, context_management_mode="automatic")

    assert len(legacy) == 10
    assert [message.id for message in automatic] == [message.id for message in messages]


def test_automatic_prompt_harness_never_compacts_middle_of_verified_suffix():
    character = Character(id="char_aria", name="아리아", persona="차분하다")
    messages = [
        Message(
            id=f"m_long_{index:02d}",
            conversation_id="conv",
            speaker_type="user" if index % 2 == 0 else "character",
            speaker_id="user_001" if index % 2 == 0 else character.id,
            content=f"UNIQUE_{index:02d}_" + ("긴 대화 내용 " * 35),
        )
        for index in range(20)
    ]

    harness = build_multi_character_prompt_harness(
        characters=[character],
        recent_messages=messages,
        user_message="현재 입력",
        context_management_mode="automatic",
    )

    recent_entry = next(entry for entry in harness.ledger if entry.key == "recent_messages")
    assert recent_entry.approx_tokens > 1800
    assert "section compacted to prompt budget" not in harness.compiled_text
    for index in range(20):
        assert f"UNIQUE_{index:02d}_" in harness.compiled_text


def test_automatic_context_rejects_dangling_summary_boundary_instead_of_failing_open():
    messages = [
        Message(
            id="m_current",
            conversation_id="conv",
            speaker_type="user",
            speaker_id="user_001",
            content="current",
        )
    ]
    scene = SceneState(
        conversation_id="conv",
        summary="[Rolling Story Arc]\n- prior covered event",
        last_compression_source_message_id="m_missing",
    )

    assert messages_after_scene_summary_boundary(messages, scene) == messages
    with pytest.raises(ContextCoverageError, match="context coverage boundary is missing"):
        messages_after_scene_summary_boundary(
            messages,
            scene,
            context_management_mode="automatic",
        )


def test_multi_character_prompt_harness_routes_sections_with_ledger_metadata():
    aria = Character(id="char_aria", name="아리아", persona="차분하게 사용자를 챙긴다")
    luna = Character(id="char_luna", name="루나", persona="밝고 야무진 동생이다")
    messages = [
        Message(id="m1", conversation_id="conv", speaker_type="character", speaker_id="char_aria", content="첫 흐름"),
        Message(id="m2", conversation_id="conv", speaker_type="user", speaker_id="user_001", content="지금 공식 경기 상태로 반응해줘"),
    ]
    scene = SceneState(conversation_id="conv", mood="긴장", current_conflict="결승전")

    harness = build_multi_character_prompt_harness(
        characters=[aria, luna],
        recent_messages=messages,
        user_message="이어가",
        scene_state=scene,
        conversation_mode="character_character",
        genre_mode="battle",
        official_domain_context="official active match: 아리아 vs 루나",
        continuity_context_by_character={"char_aria": "최근 우위를 잡았다"},
        total_budget_tokens=2200,
    )

    keys = [entry.key for entry in harness.ledger]
    assert keys[:5] == ["base_rules", "output_rules", "character_cards", "scene", "recent_messages"]
    assert "official_domain_state" in keys
    assert "continuity_state_by_character" in keys
    assert "recent_messages" in keys
    assert "output_rules" in keys

    official = next(entry for entry in harness.ledger if entry.key == "official_domain_state")
    assert official.source == "genre_domain.battle_ledger"
    assert official.included_reason == "genre_route:battle"
    assert official.included is True

    assert "[Official domain state]" in harness.compiled_text
    assert "official active match: 아리아 vs 루나" in harness.compiled_text
    assert "[Prompt harness ledger]" not in harness.compiled_text
    assert harness.used_tokens > 0


def test_xai_prompt_appends_editable_roleplay_contract_last_without_affecting_other_providers():
    character = Character(id="char_aria", name="아리아", persona="차분하게 사용자를 챙긴다")
    custom_contract = "CUSTOM XAI CONTRACT: JSON is transport only; preserve natural roleplay prose."

    xai_harness = build_multi_character_prompt_harness(
        characters=[character],
        recent_messages=[],
        user_message="이어가",
        provider_type="xai",
        prompt_settings={"xai_roleplay_rendering_contract": custom_contract},
    )
    generic_harness = build_multi_character_prompt_harness(
        characters=[character],
        recent_messages=[],
        user_message="이어가",
        provider_type="openai_compatible",
        prompt_settings={"xai_roleplay_rendering_contract": custom_contract},
    )

    assert xai_harness.sections[-1].key == "xai_roleplay_rendering_contract"
    assert xai_harness.ledger[-1].source == "backend.system_prompt_registry.xai_roleplay_rendering_contract"
    assert xai_harness.compiled_text.rstrip().endswith(custom_contract)
    assert "xai_roleplay_rendering_contract" not in {section.key for section in generic_harness.sections}
    assert custom_contract not in generic_harness.compiled_text


def test_multi_character_cards_keep_late_active_characters_under_section_budget():
    characters = [
        Character(
            id=f"char_{idx}",
            name=f"캐릭터{idx}",
            description="긴 설명 " * 20,
            persona=f"캐릭터{idx} 고유 페르소나 " + ("상세 설정 " * 80),
            behavior_style=f"캐릭터{idx} 행동 스타일 " + ("행동 묘사 " * 60),
            speech_style=f"캐릭터{idx} 말투 예시 " + ("대사 예시 " * 60),
        )
        for idx in range(1, 5)
    ]

    harness = build_multi_character_prompt_harness(
        characters=characters,
        recent_messages=[Message(id="m1", conversation_id="conv", speaker_type="user", speaker_id="user_001", content="네 명 모두 반응해줘")],
        user_message="네 명 모두 반응해줘",
        genre_mode="battle",
        total_budget_tokens=3600,
        min_output_tokens=1280,
    )

    start = harness.compiled_text.index("[Active character cards]")
    next_section = harness.compiled_text.find("\n\n[", start + 1)
    character_section = harness.compiled_text[start: next_section if next_section != -1 else len(harness.compiled_text)]
    for character in characters:
        assert f"name: {character.name} ({character.id})" in character_section
        assert f"{character.name} 고유 페르소나" in character_section
    entry = next(entry for entry in harness.ledger if entry.key == "character_cards")
    assert entry.used_tokens <= entry.budget_tokens


def test_single_character_prompt_uses_harness_ledger_and_budget_targets():
    character = Character(
        id="char_aria",
        name="아리아",
        persona="사용자를 가까이서 챙기는 후배다.",
        behavior_style="피곤한 사용자에게 먼저 정리해서 말한다.",
        speech_style="사용자, 이건 내가 정리해줄게.",
    )
    messages = [Message(id="m1", conversation_id="conv", speaker_type="user", speaker_id="user_001", content="맥락 살려줘")]

    harness = build_character_prompt_harness(
        character=character,
        recent_messages=messages,
        user_message="이어가",
        continuity_context="[Runtime relationships]\nuser_001 | trust=4",
        genre_mode="romance",
    )

    keys = [entry.key for entry in harness.ledger]
    assert "identity_lock" in keys
    assert "continuity_state_by_character" in keys
    assert "recent_messages" in keys
    assert "output_rules" in keys
    assert "[Identity lock - highest priority]" in harness.compiled_text
    assert "[Continuity state]" in harness.compiled_text
    assert harness.total_budget_tokens == prompt_budget_for_context(genre_mode="romance", is_multi_room=False)
    assert harness.used_tokens > 0


def test_single_character_prompt_keeps_manual_memory_without_relationship_state():
    character = Character(
        id="char_mina",
        name="아리아",
        persona="초기 설정은 낯선 연습생이다. " * 80,
        behavior_style="긴 행동 스타일 " * 120,
        speech_style="긴 말투 예시 " * 120,
    )
    messages = [
        Message(
            id=f"m{i}",
            conversation_id="conv",
            speaker_type="user" if i % 2 == 0 else "character",
            speaker_id="user_001" if i % 2 == 0 else "char_mina",
            content="최근 대화가 길게 이어진다 " * 50,
        )
        for i in range(12)
    ]
    scene = SceneState(
        conversation_id="conv",
        location="유저의 집",
        mood="초면",
        world_seed="유저와 초면인 아리아가 마주치는 초기 전제",
        summary="[Story Arc]\n- 아리아는 유저와 사귀기로 했고 비밀 연애를 이어간다.",
    )

    harness = build_character_prompt_harness(
        character=character,
        recent_messages=messages,
        user_message="이어가",
        scene_state=scene,
        continuity_context="[Long-term character memory]\n- 유저와 비밀 연애를 시작함",
        relationship_context="[Runtime relationships]\nuser_001 | trust=5 affinity=5 | 연인 관계를 유지 중",
        external_memory_context="[External memory]\n아리아는 오렌지레드엔터 데뷔조다.",
        genre_mode="romance",
        total_budget_tokens=12000,
    )

    entries = {entry.key: entry for entry in harness.ledger}
    assert "relationship_state_by_character" not in entries
    assert entries["continuity_state_by_character"].included is True
    assert "유저와 비밀 연애를 시작함" in harness.compiled_text
    assert "연인 관계를 유지 중" not in harness.compiled_text
    assert "[Durable conversation history]" in harness.compiled_text
    assert "Continuity rule" not in harness.compiled_text


def test_multi_character_output_contract_survives_tight_budget_and_long_context():
    character = Character(
        id="char_mina",
        name="아리아",
        description="긴 설명 " * 200,
        persona="복잡한 페르소나 " * 300,
        behavior_style="행동 규칙 " * 200,
        speech_style="말투 예시 " * 200,
    )
    messages = [
        Message(
            id=f"m{i}",
            conversation_id="conv",
            speaker_type="user" if i % 3 == 0 else "character",
            speaker_id="user_001" if i % 3 == 0 else "char_mina",
            content="최근 대화가 길게 이어진다 " * 80,
        )
        for i in range(18)
    ]
    scene = SceneState(
        conversation_id="conv",
        summary="오래된 스토리아크 " * 120,
        location="홍대 길거리",
        mood="초면의 긴장",
        current_conflict="서로의 거리감을 확인하는 중",
    )

    harness = build_multi_character_prompt_harness(
        characters=[character],
        recent_messages=messages,
        user_message="이어가",
        scene_state=scene,
        conversation_mode="user_character",
        genre_mode="romance",
        relationship_context_by_character={"char_mina": "관계 상태 " * 200},
        external_memory_context_by_character={"char_mina": "외부 기억 " * 200},
        continuity_context_by_character={"char_mina": "연속성 기억 " * 200},
        total_budget_tokens=2400,
    )

    output_entry = next(entry for entry in harness.ledger if entry.key == "output_rules")
    assert output_entry.included is True
    assert output_entry.excluded_reason == ""
    assert "[Output contract]" in harness.compiled_text
    assert '\"emotion\"' in harness.compiled_text
    assert '\"replies\"' in harness.compiled_text


def test_prompt_budget_targets_are_finite_and_route_aware():
    assert prompt_budget_for_context(genre_mode="romance", is_multi_room=False) == 12_000
    assert prompt_budget_for_context(genre_mode="slice_of_life", is_multi_room=True) == 16_000
    assert prompt_budget_for_context(genre_mode="battle", is_multi_room=True) == 18_000
