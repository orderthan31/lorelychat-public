from __future__ import annotations


from sqlmodel import Session

from app.core.config import get_settings
from app.db.models import Character, CharacterMemory, Conversation, SceneState
from app.engine import prompts, command_overlay
from app.services import chat_command_service
from app.engine.character_runtime import format_runtime_source_message, generation_control_reserve_tokens
from app.engine.prompt_harness import approx_tokens
from app.services import generation_preparation
from app.schemas.conversations import (
    ConversationCompressionPreviewRead,
    ConversationContextPreviewRead,
    ContextPreviewSectionRead,
)
from app.services import (
    conversation_service,
    domain_actor_service,
    external_memory_service,
    genre_domain_service,
    runtime_settings_service,
    system_prompt_service,
)
from app.engine.harness_comparison import HistoryAccounting, ComparisonOptions, generation_input_estimate


def make_section(
    key: str,
    title: str,
    content: str,
    *,
    included: bool = True,
    source: str | None = None,
    included_reason: str = "prompt_section",
    budget_tokens: int | None = None,
    excluded_reason: str = "",
) -> ContextPreviewSectionRead:
    token_count = approx_tokens(content or "")
    return ContextPreviewSectionRead(
        key=key,
        title=title,
        content=content or "",
        included=included,
        char_count=len(content or ""),
        approx_tokens=token_count,
        used_tokens=token_count if included else 0,
        source=source or f"prompt.{key}",
        included_reason=included_reason,
        budget_tokens=budget_tokens or max(1, token_count),
        excluded_reason=excluded_reason,
    )


def make_section_from_prompt_section(section: prompts.PromptSection, ledger_entry) -> ContextPreviewSectionRead:
    return ContextPreviewSectionRead(
        key=section.key,
        title=section.title,
        content=section.content or "",
        included=ledger_entry.included,
        char_count=len(section.content or ""),
        approx_tokens=ledger_entry.approx_tokens,
        used_tokens=ledger_entry.used_tokens,
        source=ledger_entry.source,
        included_reason=ledger_entry.included_reason,
        budget_tokens=ledger_entry.budget_tokens,
        excluded_reason=ledger_entry.excluded_reason,
    )


def _durable_memories(session: Session, conversation_id: str, active_character_ids: set[str]) -> list[CharacterMemory]:
    return conversation_service.list_visible_durable_memories_for_compression(
        session,
        conversation_id,
        active_character_ids,
        limit=80,
    )


def _active_commands(session: Session, conversation: Conversation | None):
    commands = [command for cid in chat_command_service.active_command_ids_for_conversation(conversation)
                if (command := chat_command_service.get_chat_command(session, cid)) and command.enabled]
    commands.sort(key=lambda item: (-item.priority, item.name))
    return commands


def _active_command_plan(session: Session, conversation: Conversation | None):
    return command_overlay.build_plan(_active_commands(session, conversation))


def build_compression_preview(session: Session, conversation_id: str) -> ConversationCompressionPreviewRead:
    comparison = ComparisonOptions.from_settings(get_settings())
    messages = conversation_service.list_messages(session, conversation_id)
    participants = conversation_service.get_participants(session, conversation_id)
    character_ids = [participant.participant_id for participant in participants if participant.participant_type == "character"]
    scene = session.get(SceneState, conversation_id) or SceneState(conversation_id=conversation_id)
    mode = get_settings().context_management_mode
    selector_kwargs = {}
    selected_messages: list = []
    warnings: list[str] = []
    coverage_valid = True
    if comparison.context_policy == "full_history":
        selector_kwargs = None
        warnings.append("automatic_compression_disabled: full_history; manual compression remains available")
    elif mode == "automatic":
        runtime_setting = runtime_settings_service.get_effective_setting(session, conversation_id)
        conversation = session.get(Conversation, conversation_id)
        capacity = generation_preparation.prepare_generation_route(
            session, runtime_setting, genre_mode=getattr(conversation, "genre_mode", None),
            is_multi_room=len(character_ids) >= 2, comparison=comparison,
        ).capacity
        characters = [
            character
            for character_id in character_ids
            if (character := session.get(Character, character_id)) is not None
        ]
        history_token_estimator = conversation_service.automatic_context_message_token_estimator(
            {character.id: character.name for character in characters},
            history_serialization=comparison.history_serialization,
        )
        try:
            context_preview = build_context_preview(session, conversation_id)
            # The preview exposes the actual included sections. Reconstruct only
            # their envelope counts, not a new differently budgeted harness.
            history_ids = set(context_preview.history_message_ids)
            source_ids = frozenset(message.id for message in messages if message.id not in history_ids)
            accounting_sections = [section for section in context_preview.sections
                                   if section.included and section.key != "current_user_input"
                                   and not section.key.startswith("native_history_")]
            if not any(section.key == "recent_messages" for section in accounting_sections):
                accounting_sections.append(make_section(
                    "recent_messages", prompts.conversation_history_title(scene), ""))
            accounting = HistoryAccounting.from_sections(accounting_sections)
            sequence_estimator = conversation_service.automatic_context_history_token_estimator(
                {character.id: character.name for character in characters},
                history_serialization=comparison.history_serialization,
                accounting=accounting, excluded_message_ids=source_ids,
            )
            mandatory_prompt_tokens = context_preview.projected_input_tokens - sequence_estimator(messages)
            public_estimator = history_token_estimator
            history_token_estimator = lambda message: public_estimator(message) if message.id in history_ids else 0
            job_mapping = conversation_service.generation_job_source_message_ids(session, conversation_id)
            plan = conversation_service.automatic_context_plan(
                scene,
                messages,
                capacity=capacity,
                mandatory_prompt_tokens=mandatory_prompt_tokens,
                job_source_message_ids=job_mapping,
                token_estimator=history_token_estimator,
                history_token_estimator=sequence_estimator,
            )
            if plan.pressure.status == "low":
                selector_kwargs = None
            else:
                selector_kwargs = {
                    "raw_tail_token_budget": plan.raw_tail_token_budget,
                    "job_source_message_ids": job_mapping,
                    "token_estimator": history_token_estimator,
                }
        except (ValueError, prompts.ContextCoverageError) as exc:
            coverage_valid = False
            warnings.append(str(exc))
    if coverage_valid and selector_kwargs is not None:
        try:
            selected_messages, _raw_tail = conversation_service.select_incremental_compression_batch(
                scene,
                messages,
                **selector_kwargs,
            )
        except ValueError as exc:
            selected_messages = []
            warnings.append(str(exc))
    else:
        selected_messages = []
    if coverage_valid and not selected_messages:
        warnings.append("no_foldable_overflow")
    memories = _durable_memories(session, conversation_id, set(character_ids))
    harness = conversation_service.build_compression_prompt_harness(
        scene_state=scene,
        recent_messages=selected_messages,
        character_ids=character_ids,
        memories=memories,
    )
    source_by_key = {section.key: section for section in harness.sections}
    sections = [
        make_section_from_prompt_section(source_by_key[entry.key], entry)
        for entry in harness.ledger
        if entry.key in source_by_key
    ]
    if any("Advantage:" in section.content or "Relationship/tension:" in section.content for section in sections):
        warnings.append("legacy_scene_summary_markers_present")
    if any(conversation_service.official_battle_memory_noise(memory.content) for memory in memories):
        warnings.append("official_battle_memory_noise_present")
    if scene.last_compression_error:
        warnings.append(f"last_compression_error: {scene.last_compression_error}")
    return ConversationCompressionPreviewRead(
        conversation_id=conversation_id,
        recent_message_count=len(messages),
        selected_recent_message_count=len(selected_messages),
        total_budget_tokens=harness.total_budget_tokens,
        used_tokens=harness.used_tokens,
        pipeline_steps=["prepare_compression_source", "route_compression_tasks"],
        sections=sections,
        warnings=warnings,
    )


def build_context_preview(session: Session, conversation_id: str) -> ConversationContextPreviewRead:
    comparison = ComparisonOptions.from_settings(get_settings())
    full_history = comparison.context_policy == "full_history"
    runtime_setting = runtime_settings_service.get_effective_setting(session, conversation_id)
    conversation = session.get(Conversation, conversation_id)
    genre_mode = conversation_service.normalize_conversation_genre(getattr(conversation, "genre_mode", None))
    messages = conversation_service.list_messages(session, conversation_id)
    participants = conversation_service.get_participants(session, conversation_id)
    character_participants = [p for p in participants if p.participant_type == "character"]
    character_ids = [p.participant_id for p in character_participants]
    room_cast_roles = {p.participant_id: p.role for p in character_participants if p.role}
    characters = [character for character_id in character_ids if (character := session.get(Character, character_id))]
    active_ids = {character.id for character in characters}
    stored_scene = session.get(SceneState, conversation_id)
    scene = conversation_service.build_live_scene_state(
        session,
        conversation,
        stored_scene,
    ) if conversation else session.get(SceneState, conversation_id)
    if scene and stored_scene and stored_scene.last_compression_error:
        scene.last_compression_error = stored_scene.last_compression_error
    context_mode = get_settings().context_management_mode
    raw_messages = messages if full_history else prompts.messages_after_scene_summary_boundary(
        messages,
        scene,
        context_management_mode=context_mode,
    )
    selected_messages = prompts.select_prompt_recent_messages(
        raw_messages,
        context_management_mode="automatic" if full_history else context_mode,
    )
    is_multi_room = len(characters) >= 2

    preview_query = generation_preparation.offstage_recall_query(
        genre_mode=genre_mode, scene=scene,
        include_world=bool(conversation and conversation.world_setting_id), full_history=full_history,
        source_text=messages[-1].content if messages else "",
    )
    offstage_recall = domain_actor_service.build_offstage_actor_recall_context(
        session,
        conversation,
        text=preview_query,
        active_character_ids=active_ids,
        genre_mode=genre_mode,
        query=preview_query,
    )
    continuity_by_character, external_memory_context_by_character = generation_preparation.manual_memory_context(
        session, conversation_id, characters,
    )
    preparation = generation_preparation.prepare_generation_route(
        session, runtime_setting, genre_mode=genre_mode,
        is_multi_room=is_multi_room, comparison=comparison,
    )
    preset_key = preparation.preset_key
    prompt_settings = system_prompt_service.prompt_settings_map()
    token_target = preparation.token_target
    min_bubbles = runtime_settings_service.min_bubbles_for_preset(preset_key, is_multi_room=is_multi_room)
    max_bubbles = runtime_settings_service.max_bubbles_for_preset(preset_key, is_multi_room=is_multi_room)
    official_domain_context = genre_domain_service.get_official_context(session, conversation)
    preview_source_message = messages[-1] if messages and messages[-1].speaker_type in {"user", "system", "character"} else None
    preview_history = messages[:-1] if preview_source_message else messages
    preview_speaker_name = next(
        (character.name for character in characters if character.id == getattr(preview_source_message, "speaker_id", None)),
        None,
    )
    preview_source_text = format_runtime_source_message(
        speaker_type=preview_source_message.speaker_type,
        speaker_id=preview_source_message.speaker_id,
        content=preview_source_message.content,
        action=preview_source_message.action,
        speaker_name=preview_speaker_name,
    ) if preview_source_message else ""
    model_option = preparation.model_option
    provider_type = getattr(model_option, "provider_type", None)
    room_prompt_budget = preparation.room_prompt_budget
    capacity = preparation.capacity

    active_commands = _active_commands(session, conversation)
    panel_plan = command_overlay.build_plan(active_commands)
    source_is_scene_direction = preview_source_message is not None and preview_source_message.speaker_type == "system"
    base_directive = generation_preparation.generation_base_directive(
        prompt_settings, scene_direction=source_is_scene_direction,
    )
    directive = prompts.compose_generation_directive(
        base_directive=base_directive,
        commands=active_commands,
        command_overlay_plan=panel_plan,
        prompt_settings=prompt_settings,
    )
    chat_command_service.prepare_combined_context(session, panel_plan, conversation_id, scene,
        comparison_options=comparison)
    if characters:
        harness = prompts.build_multi_character_prompt_harness(
            include_world=bool(conversation and conversation.world_setting_id),
            comparison_options=comparison,
            total_budget_tokens=room_prompt_budget,
            command_overlay_plan=panel_plan,
            characters=characters,
            recent_messages=preview_history,
            user_message=preview_source_text,
            scene_state=scene,
            directive=directive,
            conversation_mode="character_character" if is_multi_room else "user_character",
            genre_mode=genre_mode,
            continuity_context_by_character=continuity_by_character,
            relationship_context_by_character={},
            external_memory_context_by_character=external_memory_context_by_character,
            offstage_actor_context=offstage_recall.content,
            min_bubbles=min_bubbles,
            max_bubbles=max_bubbles,
            min_output_tokens=token_target,
            prompt_settings=prompt_settings,
            official_domain_context=official_domain_context,
            room_cast_roles=room_cast_roles,
            provider_type=provider_type,
            context_management_mode=get_settings().context_management_mode,
        )
        raw_sections = harness.sections
        ledger = harness.ledger
    else:
        raw_sections = [
            prompts.PromptSection("scene", "Scene", prompts.build_scene_text(scene,
                include_world=bool(conversation and conversation.world_setting_id)), "scene_state", "current_room_scene", 360, True),
        ]
        if genre_mode:
            raw_sections.append(prompts.PromptSection("genre_mode", "Genre mode", prompts.build_genre_mode_policy(genre_mode), "conversation.genre_mode", f"genre_route:{genre_mode}", 180, True))
        for domain_section in genre_domain_service.get_context_sections(session, conversation):
            if domain_section.included:
                raw_sections.append(prompts.PromptSection(domain_section.key, domain_section.title, domain_section.content, "genre_domain.context_sections", f"genre_route:{genre_mode}", 320, False))
        harness = prompts.compile_prompt_sections(
            sections=raw_sections,
            total_budget_tokens=prompts.prompt_budget_for_context(genre_mode=genre_mode, is_multi_room=False),
        )
        ledger = harness.ledger

    source_by_key = {section.key: section for section in raw_sections}
    sections = [
        make_section_from_prompt_section(source_by_key[entry.key], entry)
        for entry in ledger
        if entry.key in source_by_key and (entry.included or entry.excluded_reason == "context_budget_exceeded")
    ]
    for index, message in enumerate(harness.prior_messages):
        sections.append(make_section(f"native_history_{index}",
            f"Prior history (role={message.role}, outside system prompt)", message.content,
            source="conversation.messages.public_history", included_reason="native_history_channel"))
    if preview_source_message:
        sections.append(make_section(
            "current_user_input",
            (
                "Current source input (role=assistant, outside system prompt)"
                if preview_source_message.speaker_type == "character"
                else "Current source input (role=user, outside system prompt)"
            ),
            preview_source_text,
            source="conversation.messages.current_source",
            included_reason="single_role_user_payload",
            budget_tokens=prompts.approx_tokens(preview_source_text),
        ))

    warnings: list[str] = []

    source_type = preview_source_message.speaker_type if preview_source_message else "user"
    control_tokens = generation_control_reserve_tokens(speaker_type=source_type,
        min_bubbles=min_bubbles, max_bubbles=max_bubbles, characters=characters,
        room_cast_roles=room_cast_roles, command_overlay_plan=panel_plan)
    projected_tokens = generation_input_estimate(harness, preview_source_text, source_type, control_tokens)
    if projected_tokens > capacity.working_input_tokens:
        warnings.append("context_budget_exceeded: 필수 자료를 자르지 않아 작업 입력 예산을 초과함")
    if len(selected_messages) >= 10:
        warnings.append("최근 메시지 선택량이 많음")
    if any(section.key in {"character_cards", "identity_lock"} and section.char_count > 3000 for section in sections):
        warnings.append("캐릭터 카드가 큼")
    model_key = runtime_setting.model_key or ""
    compression_model_key = runtime_setting.compression_model_key or ""
    if "gemini" in model_key.lower():
        warnings.append("Gemini chat_generation 사용 중")
    if "gemini" in compression_model_key.lower():
        warnings.append("압축 모델이 Gemini라 비용 발생 가능")
    settings = external_memory_service.get_settings()
    if settings.mem0_read_enabled:
        warnings.append("mem0 read enabled")
    if settings.mem0_write_enabled:
        warnings.append("mem0 write enabled")

    return ConversationContextPreviewRead(
        conversation_id=conversation_id,
        history_serialization=harness.history_serialization,
        context_policy=harness.context_policy,
        history_message_ids=list(harness.history_message_ids),
        history_sha256=harness.history_sha256,
        projected_input_tokens=projected_tokens,
        working_input_tokens=capacity.working_input_tokens,
        completion_reserve_tokens=capacity.completion_reserve_tokens,
        control_reserve_tokens=control_tokens,
        model_key=runtime_setting.model_key,
        compression_model_key=runtime_setting.compression_model_key,
        response_length_preset=runtime_setting.response_length_preset,
        recent_message_count=len(messages),
        selected_recent_message_count=len(selected_messages),
        total_budget_tokens=harness.total_budget_tokens,
        used_tokens=harness.used_tokens,
        sections=sections,
        warnings=warnings,
    )
