from __future__ import annotations

from typing import Any, Awaitable, Callable, TypedDict, cast
import inspect

from langgraph.graph import END, StateGraph


CompressionCallable = Callable[[Any], Any | Awaitable[Any]]


class CompressionPipelineState(TypedDict, total=False):
    scene_state: Any
    recent_messages: list[Any]
    character_ids: list[str]
    memories: list[Any]
    genre_mode: str | None
    official_domain_context: str | None
    routed_tasks: list[str]
    scene_summary: str
    memory_updates: list[dict]
    validated_update: dict | None
    pipeline_steps: list[str]
    summarize_scene: CompressionCallable
    extract_memories: CompressionCallable
    validate_update: CompressionCallable
    persist_update: CompressionCallable
    compression_harness: Any


def _append_step(state: CompressionPipelineState, step: str) -> list[str]:
    return [*(state.get("pipeline_steps") or []), step]


async def _maybe_await(result: Any | Awaitable[Any]) -> Any:
    if inspect.isawaitable(result):
        return await result
    return result


def prepare_compression_source(state: CompressionPipelineState) -> CompressionPipelineState:
    character_ids = list(state.get("character_ids") or [])
    memories = list(state.get("memories") or [])
    recent_messages = list(state.get("recent_messages") or [])
    scene_state = state.get("scene_state")
    compression_harness = state.get("compression_harness")
    if compression_harness is None and scene_state is not None:
        from app.services import conversation_service

        compression_harness = conversation_service.build_compression_prompt_harness(
            scene_state=scene_state,
            recent_messages=recent_messages,
            character_ids=character_ids,
            memories=memories,
        )
    return {
        **state,
        "character_ids": character_ids,
        "memories": memories,
        "recent_messages": recent_messages,
        "compression_harness": compression_harness,
        "pipeline_steps": _append_step(state, "prepare_compression_source"),
    }


def route_compression_tasks(state: CompressionPipelineState) -> CompressionPipelineState:
    return {
        **state,
        "routed_tasks": ["summarize_scene", "extract_durable_memory"],
        "pipeline_steps": _append_step(state, "route_compression_tasks"),
    }


async def summarize_scene_node(state: CompressionPipelineState) -> CompressionPipelineState:
    callback = state.get("summarize_scene")
    if callback is None:
        raise ValueError("summarize_scene callback missing before summarize_scene node")
    return {
        **state,
        "scene_summary": await _maybe_await(callback(state)),
        "pipeline_steps": _append_step(state, "summarize_scene"),
    }


async def extract_durable_memory_node(state: CompressionPipelineState) -> CompressionPipelineState:
    callback = state.get("extract_memories")
    if callback is None:
        raise ValueError("extract_memories callback missing before extract_durable_memory node")
    return {
        **state,
        "memory_updates": await _maybe_await(callback(state)),
        "pipeline_steps": _append_step(state, "extract_durable_memory"),
    }


async def validate_update_node(state: CompressionPipelineState) -> CompressionPipelineState:
    callback = state.get("validate_update")
    if callback is None:
        raise ValueError("validate_update callback missing before validate_update node")
    return {
        **state,
        "validated_update": await _maybe_await(callback(state)),
        "pipeline_steps": _append_step(state, "validate_update"),
    }


async def persist_compression_update_node(state: CompressionPipelineState) -> CompressionPipelineState:
    callback = state.get("persist_update")
    if callback is None:
        return {**state, "pipeline_steps": _append_step(state, "persist_compression_update")}
    await _maybe_await(callback(state))
    return {**state, "pipeline_steps": _append_step(state, "persist_compression_update")}


def build_compression_graph(*, include_persist: bool = False):
    graph = StateGraph(CompressionPipelineState)
    graph.add_node("prepare_compression_source", prepare_compression_source)
    graph.add_node("route_compression_tasks", route_compression_tasks)
    graph.add_node("summarize_scene", summarize_scene_node)
    graph.add_node("extract_durable_memory", extract_durable_memory_node)
    graph.add_node("validate_update", validate_update_node)
    if include_persist:
        graph.add_node("persist_compression_update", persist_compression_update_node)
    graph.set_entry_point("prepare_compression_source")
    graph.add_edge("prepare_compression_source", "route_compression_tasks")
    graph.add_edge("route_compression_tasks", "summarize_scene")
    graph.add_edge("summarize_scene", "extract_durable_memory")
    graph.add_edge("extract_durable_memory", "validate_update")
    if include_persist:
        graph.add_edge("validate_update", "persist_compression_update")
        graph.add_edge("persist_compression_update", END)
    else:
        graph.add_edge("validate_update", END)
    return graph.compile()


async def run_compression_graph(state: CompressionPipelineState, *, include_persist: bool = False) -> CompressionPipelineState:
    graph = build_compression_graph(include_persist=include_persist)
    return cast(CompressionPipelineState, await graph.ainvoke({**state, "pipeline_steps": list(state.get("pipeline_steps") or [])}))
