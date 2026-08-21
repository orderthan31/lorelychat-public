import pytest

from app.engine.prompt_graph import build_prompt_generation_graph, build_prompt_pipeline_graph, run_prompt_generation_pipeline, run_prompt_pipeline
from app.engine.prompt_harness import PromptSection


def test_prompt_graph_runs_route_budget_compile_pipeline():
    graph = build_prompt_pipeline_graph()
    result = run_prompt_pipeline(
        graph,
        sections=[
            PromptSection("base", "Base", "base rules", "test.base", "required", 20, True),
            PromptSection("optional", "Optional", "optional recall " * 120, "test.optional", "recall", 30, False),
            PromptSection("recent", "Recent", "latest turn", "test.recent", "tail", 20, True),
        ],
        total_budget_tokens=25,
    )

    assert result["compiled_text"].startswith("[Base]")
    assert "[Recent]" in result["compiled_text"]
    assert "[Optional]" not in result["compiled_text"]
    assert result["used_tokens"] <= 25
    assert [entry.key for entry in result["ledger"]] == ["base", "optional", "recent"]
    assert result["pipeline_steps"] == ["route_sections", "budget_context", "compile_prompt"]


def test_prompt_graph_routes_domain_and_active_context_before_optional_memory_noise():
    graph = build_prompt_pipeline_graph()
    result = run_prompt_pipeline(
        graph,
        sections=[
            PromptSection("recent_messages", "Recent", "recent tail", "test.recent", "tail", 40, True),
            PromptSection("external_memory_by_character", "External", "semantic recall", "test.external", "memory", 40, False),
            PromptSection("offstage_actor_recall", "Offstage", "   ", "test.offstage", "offstage", 40, False),
            PromptSection("room_memory", "Room memory", "manual room note", "test.room_memory", "manual_memory", 40, False),
            PromptSection("official_battle_state", "Official battle", "leader=루나", "test.battle", "domain", 40, False),
            PromptSection("base_rules", "Base", "base rules", "test.base", "required", 40, True),
        ],
        total_budget_tokens=240,
    )

    assert [section.key for section in result["routed_sections"]] == [
        "base_rules",
        "recent_messages",
        "official_battle_state",
        "room_memory",
        "external_memory_by_character",
    ]
    assert "[Official battle]" in result["compiled_text"]
    assert "[Offstage]" not in result["compiled_text"]


@pytest.mark.asyncio
async def test_prompt_generation_graph_runs_generate_parse_persist_nodes():
    graph = build_prompt_generation_graph()
    events = []

    async def generate(*, messages, response_format=None, conversation_id=None):
        events.append(("generate", messages[0]["content"], response_format, conversation_id))
        return '{"replies":[{"dialogue":"ok"}]}'

    def parse(content: str):
        events.append(("parse", content))
        return [{"dialogue": "ok"}]

    async def persist(parsed):
        events.append(("persist", parsed))
        return ["msg_1"]

    result = await run_prompt_generation_pipeline(
        graph,
        sections=[PromptSection("base", "Base", "base rules", "test.base", "required", 20, True)],
        total_budget_tokens=80,
        user_message="이어가",
        generate=generate,
        parse=parse,
        persist=persist,
        response_format={"type": "json_object"},
        conversation_id="conv_1",
    )

    assert result["messages"][1]["content"] == "이어가"
    assert result["response_content"] == '{"replies":[{"dialogue":"ok"}]}'
    assert result["parsed_replies"] == [{"dialogue": "ok"}]
    assert result["persisted_messages"] == ["msg_1"]
    assert result["pipeline_steps"] == ["route_sections", "budget_context", "compile_prompt", "prepare_messages", "generate", "parse", "persist"]
    assert events[0][0] == "generate"
    assert events[1][0] == "parse"
    assert events[2][0] == "persist"


@pytest.mark.asyncio
async def test_prompt_generation_graph_preserves_character_turn_as_assistant_role():
    graph = build_prompt_generation_graph()

    async def generate(*, messages, response_format=None, conversation_id=None):
        return '{"replies":[{"dialogue":"ok"}]}'

    result = await run_prompt_generation_pipeline(
        graph,
        sections=[PromptSection("base", "Base", "base rules", "test.base", "required", 20, True)],
        total_budget_tokens=80,
        user_message="[Character dialogue: 아리아(char_aria)]\n내가 먼저 말했어.",
        current_turn_role="assistant",
        continuation_instruction="Continue from the character-authored turn; it was not spoken by the human user.",
        generate=generate,
        parse=lambda content: [{"dialogue": "ok"}],
    )

    messages = result.get("messages") or []
    assert [message["role"] for message in messages] == ["system", "assistant", "user"]
    assert messages[1]["content"].startswith("[Character dialogue: 아리아")
    assert "not spoken by the human user" in messages[2]["content"]
