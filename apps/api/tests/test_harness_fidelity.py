"""Prompt fidelity regressions; pure synthetic inputs, no provider calls."""
from types import SimpleNamespace

import pytest

from app.db.models import Character, CharacterMemory, Message, SceneState
from app.engine import prompts
from app.engine.prompt_harness import PromptSection, compile_prompt_harness
from app.services.conversation_service import build_continuity_context


def long_fact(label):
    return f"{label}_START\n" + ("complete authored detail " * 100) + f"{label}_MIDDLE\n" + ("more detail " * 100) + f"{label}_END"


@pytest.mark.parametrize("count", [1, 4])
def test_all_authored_cards_and_silent_role_survive_pressure(count):
    chars = [Character(id=f"c{i}", name=f"Name{i}", persona=long_fact(f"PERSONA{i}"),
                       speech_style=long_fact(f"SPEECH{i}"), emotional_rules=[long_fact(f"EMOTION{i}")],
                       forbidden_rules=[long_fact(f"FORBIDDEN{i}")]) for i in range(count)]
    roles = {chars[-1].id: "silent"} if count > 1 else {}
    harness = prompts.build_multi_character_prompt_harness(characters=chars, recent_messages=[], user_message="latest",
                                                         room_cast_roles=roles, total_budget_tokens=100)
    for char in chars:
        assert char.id in harness.compiled_text
        for value in [char.persona, char.speech_style, *char.emotional_rules, *char.forbidden_rules]:
            assert value in harness.compiled_text
    if roles:
        assert "role=silent/context-only" in harness.compiled_text
    assert harness.used_tokens > harness.total_budget_tokens == 100


@pytest.mark.parametrize("mode", ["legacy", "automatic"])
def test_selected_public_history_is_complete_ordered_and_private_free(mode):
    messages = [Message(id=f"m{i}", conversation_id="conv", speaker_type=speaker, speaker_id="c",
                        content=long_fact(speaker), action=long_fact(f"ACTION_{speaker}"),
                        thought="PRIVATE_SECRET") for i, speaker in enumerate(["user", "system", "character"])]
    public_parts = [{"type": "dialogue", "text": long_fact("D1")}, {"type": "thought", "text": "PRIVATE_SECRET"},
                    {"type": "action", "text": long_fact("A2")}, {"type": "dialogue", "text": long_fact("D3")}]
    messages.append(Message(id="free", conversation_id="conv", speaker_type="character", speaker_id="c", content="AGGREGATE",
                            metadata_={"response_protocol": "tagged_text", "render_parts": public_parts}))
    result = prompts.build_multi_character_messages(characters=[Character(id="c", name="C")], recent_messages=messages,
                                                   user_message="LATEST_ONLY", context_management_mode=mode)
    text = result[0]["content"]
    for message in messages[:-1]:
        assert message.content in text
        if message.action:
            assert message.action in text
    assert "PRIVATE_SECRET" not in text and "AGGREGATE" not in text
    assert text.index(long_fact("D1")) < text.index(long_fact("A2")) < text.index(long_fact("D3"))
    assert "LATEST_ONLY" not in text
    assert result[1] == {"role": "user", "content": "LATEST_ONLY"}


def test_selected_manual_notes_keep_full_content_and_scope():
    notes = [CharacterMemory(id="n1", conversation_id="conv", character_id="__room__", memory_type="user_note", importance=4, content=long_fact("ROOM")),
             CharacterMemory(id="n2", conversation_id="conv", character_id="c", memory_type="user_note", importance=3, content=long_fact("PERSONAL"))]
    text = build_continuity_context(notes)
    assert notes[0].content in text and notes[1].content in text
    assert text.index("[Common room memory") < text.index(notes[0].content)
    assert text.index("[Long-term character memory]") < text.index(notes[1].content)


def test_protected_canon_and_directive_are_complete_under_pressure():
    scene = SceneState(conversation_id="conv", world_seed=long_fact("WORLD"), summary=long_fact("ARC"), user_description=long_fact("USER"))
    harness = prompts.build_multi_character_prompt_harness(
        characters=[Character(id="c", name="C")], recent_messages=[], user_message="latest", scene_state=scene,
        directive=long_fact("DIRECTIVE"), genre_mode="custom", official_domain_context=long_fact("OFFICIAL"),
        continuity_context_by_character={"__room__": long_fact("ROOM"), "c": long_fact("NOTE")}, total_budget_tokens=100)
    for label in ["WORLD", "ARC", "USER", "DIRECTIVE", "OFFICIAL", "ROOM", "NOTE"]:
        assert long_fact(label) in harness.compiled_text
    assert harness.total_budget_tokens == 100
    assert harness.used_tokens > 100


def test_required_and_optional_are_inclusion_not_truncation():
    fact = long_fact("REQUIRED")
    optional = long_fact("OPTIONAL")
    result = compile_prompt_harness(sections=[PromptSection("optional", "Optional", optional, "test", "test", 100),
                                             PromptSection("required", "Required", fact, "test", "test", 100, True)], total_budget_tokens=100)
    assert fact in result.compiled_text
    assert optional not in result.compiled_text
    roomy = compile_prompt_harness(sections=[PromptSection("optional", "Optional", optional, "test", "test", 100)], total_budget_tokens=10000)
    assert optional in roomy.compiled_text


@pytest.mark.parametrize("archetype", ["pure_devoted", "high_affection_clingy", "rival_to_lovers", "guarded_slowburn"])
def test_relationship_only_uses_selected_preset(archetype):
    assert prompts.format_relationship_archetype(archetype) == (
        f"Relationship archetype: {archetype}\nRelationship guidance: {prompts.RELATIONSHIP_ARCHETYPE_GUIDANCE[archetype]}")


def test_custom_relationship_is_authored_not_invented_romance_policy():
    custom = "Trusted Co-Captains — already secure; never romantic"
    assert prompts.format_relationship_archetype(custom) == f"Relationship archetype: {custom}"


@pytest.mark.parametrize("combined", [True, False])
def test_command_directive_preserves_long_intent_and_scopes_standalone_guard(combined):
    command = SimpleNamespace(id="cmd", name="panel", generation_prompt=long_fact("INTENT"), postprocess_target="last_bubble")
    plan = SimpleNamespace(eligible={"K1": command} if combined else {})
    result = prompts.compose_generation_directive(base_directive="base", commands=[command], command_overlay_plan=plan,
        prompt_settings={"command_generation_prompt_intro": "INTRO", "command_generation_overlay_guard": "SEPARATE_ONLY_GUARD"})
    assert command.generation_prompt in result
    assert ("SEPARATE_ONLY_GUARD" in result) is (not combined)
    harness = prompts.build_multi_character_prompt_harness(characters=[Character(id="c", name="C")], recent_messages=[],
                                                         user_message="latest", directive=result, total_budget_tokens=10)
    assert result in harness.compiled_text


def test_mixed_commands_scope_guard_to_separate_owner_only():
    combined = SimpleNamespace(id="combined", name="combined", generation_prompt=long_fact("COMBINED"), postprocess_target="first_bubble")
    separate = SimpleNamespace(id="separate", name="separate", generation_prompt=long_fact("SEPARATE"), postprocess_target="last_bubble")
    result = prompts.compose_generation_directive(base_directive="base", commands=[combined, separate],
        command_overlay_plan=SimpleNamespace(eligible={"K1": combined}),
        prompt_settings={"command_generation_overlay_guard": "SEPARATE_ONLY_GUARD"})
    assert combined.generation_prompt in result and separate.generation_prompt in result
    assert "Separate postprocessing only for !separate (not the combined auxiliary panels):" in result
    assert result.count("SEPARATE_ONLY_GUARD") == 1


def test_legacy_single_character_path_retains_canon_and_authored_rules():
    char = Character(id="c", name="C", persona=long_fact("PERSONA"), speech_style=long_fact("SPEECH"),
                     emotional_rules=[long_fact("EMOTION")], forbidden_rules=[long_fact("FORBIDDEN")])
    harness = prompts.build_character_prompt_harness(character=char, recent_messages=[], user_message="latest",
        scene_state=SceneState(conversation_id="conv", user_description=long_fact("USER")),
        directive=long_fact("DIRECTIVE"), continuity_context=long_fact("NOTE"), total_budget_tokens=100)
    for label in ["PERSONA", "SPEECH", "EMOTION", "FORBIDDEN", "USER", "DIRECTIVE", "NOTE"]:
        assert long_fact(label) in harness.compiled_text
    assert harness.used_tokens > harness.total_budget_tokens == 100


def test_optional_unit_cannot_consume_later_required_reserve():
    harness = compile_prompt_harness(sections=[
        PromptSection("optional", "Optional", "optional fact " * 10, "test", "test", 100),
        PromptSection("required", "Required", "required fact " * 10, "test", "test", 100, True),
    ], total_budget_tokens=100)
    assert "optional fact" not in harness.compiled_text
    assert "required fact " * 10 in harness.compiled_text
    assert harness.used_tokens <= harness.total_budget_tokens
