"""Read-only route and prompt-input policy shared by execution and previews.

This module resolves configuration and estimates capacity; it never sends a
provider request, compresses history, or persists room state. Callers retain
ownership of source-message cutoffs, command selection and compression budgets.
"""
from dataclasses import dataclass

from sqlmodel import Session

from app.engine import prompts
from app.engine.harness_comparison import ComparisonOptions
from app.engine.llm_client import LLMClient
from app.services import conversation_service, model_provider_service, runtime_settings_service
from app.services.context_management_service import ContextCapacity, resolve_safe_model_context_capacity


@dataclass(frozen=True)
class GenerationPreparation:
    preset_key: str
    token_target: int
    primary_overrides: dict
    fallback_overrides: dict | None
    primary_client: LLMClient
    fallback_client: LLMClient | None
    model_option: object
    capacity: ContextCapacity
    room_prompt_budget: int


def prepare_generation_route(
    session: Session,
    runtime_setting,
    *,
    genre_mode: str | None,
    is_multi_room: bool,
    comparison: ComparisonOptions,
) -> GenerationPreparation:
    """Reserve the largest reachable reasoning-aware chat output ceiling."""
    preset_key = getattr(runtime_setting, "response_length_preset", runtime_settings_service.DEFAULT_RESPONSE_LENGTH_PRESET)
    token_target = runtime_settings_service.preset_token_target(preset_key)
    primary_overrides = dict(runtime_settings_service.llm_overrides_for_setting(runtime_setting, session=session))
    primary_overrides["min_output_tokens"] = token_target
    fallback = runtime_settings_service.fallback_llm_overrides_for_setting(runtime_setting, session=session)
    fallback_overrides = {**fallback, "min_output_tokens": token_target} if fallback else None
    primary_client = LLMClient(profile="chat", purpose="chat_generation", overrides=primary_overrides)
    fallback_client = (
        LLMClient(profile="chat", purpose="chat_generation_fallback", overrides=fallback_overrides)
        if fallback_overrides else None
    )
    model_option = model_provider_service.option_by_key(session, runtime_setting.model_key)
    fallback_option = model_provider_service.option_by_key(
        session, getattr(runtime_setting, "fallback_model_key", None),
    ) if fallback_client is not None else None
    room_prompt_budget = prompts.prompt_budget_for_context(genre_mode=genre_mode, is_multi_room=is_multi_room)
    output_reserve = max(
        int(primary_client.max_output_tokens() or 0),
        int(fallback_client.max_output_tokens() or 0) if fallback_client is not None else 0,
    )
    capacity = resolve_safe_model_context_capacity(
        model_option, fallback_option,
        room_prompt_budget_tokens=room_prompt_budget,
        completion_reserve_tokens=output_reserve,
        comparison_options=comparison,
    )
    if comparison.context_policy != "compact":
        room_prompt_budget = capacity.working_input_tokens
    return GenerationPreparation(
        preset_key, token_target, primary_overrides, fallback_overrides,
        primary_client, fallback_client, model_option, capacity, room_prompt_budget,
    )


def manual_memory_context(session: Session, conversation_id: str, characters) -> tuple[dict[str, str], dict[str, str]]:
    """Only authored notes belong in prompts; do not restore automatic recall."""
    continuity = {
        conversation_service.COMMON_ROOM_MEMORY_CHARACTER_ID: conversation_service.build_continuity_context(
            conversation_service.list_common_room_memories(session, conversation_id), None,
        ),
    }
    for character in characters:
        continuity[character.id] = conversation_service.build_continuity_context(
            conversation_service.list_character_memories(session, conversation_id, character.id), None,
        )
    return continuity, {character.id: "" for character in characters}


def generation_base_directive(prompt_settings: dict, *, scene_direction: bool) -> str:
    if scene_direction:
        return prompt_settings.get("directive_scene_direction", "Acknowledge this scene direction as true state and continue the room. Do not treat it as user dialogue.")
    return prompt_settings.get("directive_natural_chat", "Generate the next natural chat bubbles for this room. In multi-character rooms, let the listed characters interact in one model call.")


def offstage_recall_query(*, genre_mode, scene, include_world: bool, full_history: bool, source_text: str) -> str:
    return "\n".join(filter(None, [
        f"genre={genre_mode}" if genre_mode else "",
        scene.world_seed if scene and include_world else "",
        scene.current_conflict if scene and not full_history else "",
        scene.summary if scene and not full_history else "",
        source_text,
    ]))[:800]
