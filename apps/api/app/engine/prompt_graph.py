from __future__ import annotations

from typing import Any, Awaitable, Callable, TypedDict
import inspect

from langgraph.graph import END, StateGraph

from app.engine.prompt_harness import PromptHarness, PromptLedgerEntry, PromptSection, compile_prompt_harness


class PromptPipelineState(TypedDict, total=False):
    sections: list[PromptSection]
    total_budget_tokens: int
    routed_sections: list[PromptSection]
    harness: PromptHarness
    compiled_text: str
    ledger: list[PromptLedgerEntry]
    used_tokens: int
    pipeline_steps: list[str]


def _append_step(state: PromptPipelineState, step: str) -> list[str]:
    return [*(state.get("pipeline_steps") or []), step]


SECTION_ROUTE_PRIORITY = {
    "base_rules": 10,
    "identity_lock": 15,
    "output_rules": 20,
    "output_style": 22,
    "character_cards": 30,
    "scene": 35,
    "directive": 40,
    "recent_messages": 45,
    "official_domain_state": 50,
    "official_battle_state": 50,
    "battle_control_hint": 52,
    "user_description": 55,
    "room_cast_roles": 58,
    "room_memory": 60,
    "continuity_state_by_character": 65,
    "offstage_actor_recall": 70,
    "genre_mode": 75,
    "external_memory_by_character": 80,
    "minimum_output_target": 90,
    "character_card_section_rules": 100,
    "persona_background": 110,
    "appearance_reference": 120,
    "behavior_style": 130,
    "speech_examples": 140,
    "trait_scores": 150,
    "emotional_rules": 160,
    "forbidden_rules": 170,
    "room_context": 180,
}

OPTIONAL_CONTEXT_KEYS = {
    "room_memory",
    "offstage_actor_recall",
    "external_memory_by_character",
    "continuity_state_by_character",
}


def _section_has_routable_content(section: PromptSection) -> bool:
    content = (section.content or "").strip()
    if section.required:
        return True
    if not content:
        return False
    if section.key in OPTIONAL_CONTEXT_KEYS and content.lower() in {"none", "n/a", "null", "[]", "{}"}:
        return False
    return True


def route_sections(state: PromptPipelineState) -> PromptPipelineState:
    """Apply first-pass section routing before budget compilation.

    The router keeps unknown/caller-specific sections stable, but promotes known
    source-of-truth blocks ahead of optional memory/context noise and drops empty
    optional context sections before they spend ledger/budget attention.
    """
    indexed_sections = [
        (index, section)
        for index, section in enumerate(state.get("sections") or [])
        if _section_has_routable_content(section)
    ]
    routed_sections = [
        section
        for _, section in sorted(
            indexed_sections,
            key=lambda item: (SECTION_ROUTE_PRIORITY.get(item[1].key, 1000 + item[0]), item[0]),
        )
    ]
    return {
        **state,
        "routed_sections": routed_sections,
        "pipeline_steps": _append_step(state, "route_sections"),
    }


def budget_context(state: PromptPipelineState) -> PromptPipelineState:
    harness = compile_prompt_harness(
        sections=list(state.get("routed_sections") or []),
        total_budget_tokens=int(state.get("total_budget_tokens") or 3200),
    )
    return {
        **state,
        "harness": harness,
        "pipeline_steps": _append_step(state, "budget_context"),
    }


def compile_prompt(state: PromptPipelineState) -> PromptPipelineState:
    harness = state.get("harness")
    if harness is None:
        raise ValueError("prompt harness missing before compile_prompt node")
    return {
        **state,
        "compiled_text": harness.compiled_text,
        "ledger": harness.ledger,
        "used_tokens": harness.used_tokens,
        "pipeline_steps": _append_step(state, "compile_prompt"),
    }


def build_prompt_pipeline_graph():
    graph = StateGraph(PromptPipelineState)
    graph.add_node("route_sections", route_sections)
    graph.add_node("budget_context", budget_context)
    graph.add_node("compile_prompt", compile_prompt)
    graph.set_entry_point("route_sections")
    graph.add_edge("route_sections", "budget_context")
    graph.add_edge("budget_context", "compile_prompt")
    graph.add_edge("compile_prompt", END)
    return graph.compile()


def run_prompt_pipeline(graph, *, sections: list[PromptSection], total_budget_tokens: int) -> PromptPipelineState:
    return graph.invoke({
        "sections": sections,
        "total_budget_tokens": total_budget_tokens,
        "pipeline_steps": [],
    })



GenerationCallable = Callable[..., Any | Awaitable[Any]]
ParseCallable = Callable[[str], Any]
PersistCallable = Callable[[Any], Any | Awaitable[Any]]


class PromptGenerationPipelineState(PromptPipelineState, total=False):
    user_message: str
    current_turn_role: str
    continuation_instruction: str
    messages: list[dict]
    response_format: dict | None
    conversation_id: str | None
    response_content: str
    llm_response: Any
    parsed_replies: Any
    persisted_messages: Any
    generate: GenerationCallable
    parse: ParseCallable
    persist: PersistCallable


def prepare_messages(state: PromptGenerationPipelineState) -> PromptGenerationPipelineState:
    current_turn_role = state.get("current_turn_role") or "user"
    if current_turn_role not in {"user", "assistant"}:
        raise ValueError(f"Unsupported current turn role: {current_turn_role}")
    messages = [
        {"role": "system", "content": state.get("compiled_text") or ""},
        {"role": current_turn_role, "content": state.get("user_message") or ""},
    ]
    continuation_instruction = state.get("continuation_instruction") or ""
    if current_turn_role == "assistant":
        if not continuation_instruction.strip():
            raise ValueError("Assistant-authored current turns require a continuation instruction")
        messages.append({"role": "user", "content": continuation_instruction})
    return {
        **state,
        "messages": messages,
        "pipeline_steps": _append_step(state, "prepare_messages"),
    }


async def generate_response(state: PromptGenerationPipelineState) -> PromptGenerationPipelineState:
    generate = state.get("generate")
    if generate is None:
        raise ValueError("generate callable missing before generate node")
    result = generate(
        messages=state.get("messages") or [],
        response_format=state.get("response_format"),
        conversation_id=state.get("conversation_id"),
    )
    if inspect.isawaitable(result):
        result = await result
    content = getattr(result, "content", result)
    return {
        **state,
        "response_content": str(content or ""),
        "llm_response": result,
        "pipeline_steps": _append_step(state, "generate"),
    }


def parse_response(state: PromptGenerationPipelineState) -> PromptGenerationPipelineState:
    parse = state.get("parse")
    if parse is None:
        raise ValueError("parse callable missing before parse node")
    return {
        **state,
        "parsed_replies": parse(state.get("response_content") or ""),
        "pipeline_steps": _append_step(state, "parse"),
    }


async def persist_response(state: PromptGenerationPipelineState) -> PromptGenerationPipelineState:
    persist = state.get("persist")
    if persist is None:
        return {**state, "pipeline_steps": _append_step(state, "persist")}
    result = persist(state.get("parsed_replies"))
    if inspect.isawaitable(result):
        result = await result
    return {
        **state,
        "persisted_messages": result,
        "pipeline_steps": _append_step(state, "persist"),
    }


def build_prompt_generation_graph():
    graph = StateGraph(PromptGenerationPipelineState)
    graph.add_node("route_sections", route_sections)
    graph.add_node("budget_context", budget_context)
    graph.add_node("compile_prompt", compile_prompt)
    graph.add_node("prepare_messages", prepare_messages)
    graph.add_node("generate", generate_response)
    graph.add_node("parse", parse_response)
    graph.add_node("persist", persist_response)
    graph.set_entry_point("route_sections")
    graph.add_edge("route_sections", "budget_context")
    graph.add_edge("budget_context", "compile_prompt")
    graph.add_edge("compile_prompt", "prepare_messages")
    graph.add_edge("prepare_messages", "generate")
    graph.add_edge("generate", "parse")
    graph.add_edge("parse", "persist")
    graph.add_edge("persist", END)
    return graph.compile()


async def run_prompt_generation_pipeline(
    graph,
    *,
    sections: list[PromptSection],
    total_budget_tokens: int,
    user_message: str,
    current_turn_role: str = "user",
    continuation_instruction: str = "",
    generate: GenerationCallable,
    parse: ParseCallable,
    persist: PersistCallable | None = None,
    response_format: dict | None = None,
    conversation_id: str | None = None,
) -> PromptGenerationPipelineState:
    return await graph.ainvoke({
        "sections": sections,
        "total_budget_tokens": total_budget_tokens,
        "user_message": user_message,
        "current_turn_role": current_turn_role,
        "continuation_instruction": continuation_instruction,
        "generate": generate,
        "parse": parse,
        "persist": persist,
        "response_format": response_format,
        "conversation_id": conversation_id,
        "pipeline_steps": [],
    })
