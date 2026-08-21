from __future__ import annotations

import math
from datetime import datetime

from sqlmodel import Session, select

from app.db.models import Character, CharacterMemory, Conversation, SceneState
from app.engine import prompts
from app.schemas.conversations import ConversationCompressionPreviewRead, ConversationContextPreviewRead, ContextPreviewSectionRead
from app.services import conversation_service, domain_actor_service, external_memory_service, genre_domain_service, model_provider_service, runtime_settings_service, system_prompt_service


def approx_tokens(text: str) -> int:
    return int(math.ceil(len(text or "") / 2.2))


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


def build_compression_preview(session: Session, conversation_id: str) -> ConversationCompressionPreviewRead:
    messages = conversation_service.list_messages(session, conversation_id)
    participants = conversation_service.get_participants(session, conversation_id)
    character_ids = [participant.participant_id for participant in participants if participant.participant_type == "character"]
    scene = session.get(SceneState, conversation_id) or SceneState(conversation_id=conversation_id)
    warnings: list[str] = []
    try:
        selected_messages, _raw_tail = conversation_service.select_incremental_compression_batch(scene, messages)
    except ValueError as exc:
        selected_messages = []
        warnings.append(str(exc))
    if not selected_messages:
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
    runtime_setting = runtime_settings_service.get_effective_setting(session, conversation_id)
    conversation = session.get(Conversation, conversation_id)
    genre_mode = conversation_service.normalize_genre_mode(getattr(conversation, "genre_mode", None))
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
    raw_messages = prompts.messages_after_scene_summary_boundary(messages, scene)
    selected_messages = prompts.select_prompt_recent_messages(raw_messages)
    is_multi_room = len(characters) >= 2

    preview_query = "\n".join(filter(None, [
        f"genre={genre_mode}",
        scene.world_seed if scene else "",
        scene.current_conflict if scene else "",
        scene.summary if scene else "",
        messages[-1].content if messages else "",
    ]))[:800]
    offstage_recall = domain_actor_service.build_offstage_actor_recall_context(
        session,
        conversation,
        text=preview_query,
        active_character_ids=active_ids,
        genre_mode=genre_mode,
        query=preview_query,
    )
    common_memory_context = conversation_service.build_continuity_context(
        conversation_service.list_common_room_memories(session, conversation_id),
        None,
    )
    continuity_by_character: dict[str, str] = {
        conversation_service.COMMON_ROOM_MEMORY_CHARACTER_ID: common_memory_context,
    }
    external_memory_context_by_character: dict[str, str] = {}
    for character in characters:
        local_context = conversation_service.build_continuity_context(
            conversation_service.list_character_memories(session, conversation_id, character.id),
            None,
        )
        external_memory_context_by_character[character.id] = ""
        continuity_by_character[character.id] = local_context

    preset_key = getattr(runtime_setting, "response_length_preset", runtime_settings_service.DEFAULT_RESPONSE_LENGTH_PRESET)
    directive = "Generate the next natural chat bubbles for this room. In multi-character rooms, let the listed characters interact in one model call."
    token_target = runtime_settings_service.preset_token_target(preset_key)
    min_bubbles = runtime_settings_service.min_bubbles_for_preset(preset_key, is_multi_room=is_multi_room)
    max_bubbles = runtime_settings_service.max_bubbles_for_preset(preset_key, is_multi_room=is_multi_room)
    official_domain_context = genre_domain_service.get_official_context(session, conversation)
    preview_source_message = messages[-1] if messages and messages[-1].speaker_type in {"user", "system"} else None
    preview_history = messages[:-1] if preview_source_message else messages
    model_option = model_provider_service.option_by_key(session, runtime_setting.model_key)
    provider_type = getattr(model_option, "provider_type", None)

    if characters:
        harness = prompts.build_multi_character_prompt_harness(
            characters=characters,
            recent_messages=preview_history,
            user_message=preview_source_message.content if preview_source_message else "",
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
            prompt_settings=system_prompt_service.prompt_settings_map(),
            official_domain_context=official_domain_context,
            room_cast_roles=room_cast_roles,
            provider_type=provider_type,
        )
        raw_sections = harness.sections
        ledger = harness.ledger
    else:
        raw_sections = [
            prompts.PromptSection("scene", "Scene", prompts.build_scene_text(scene), "scene_state", "current_room_scene", 360, True),
            prompts.PromptSection("genre_mode", "Genre mode", prompts.build_genre_mode_policy(genre_mode), "conversation.genre_mode", f"genre_route:{genre_mode or 'battle'}", 180, True),
        ]
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
    if preview_source_message:
        sections.append(make_section(
            "current_user_input",
            "Current user input (role=user, outside system prompt)",
            preview_source_message.content,
            source="conversation.messages.current_source",
            included_reason="single_role_user_payload",
            budget_tokens=prompts.approx_tokens(preview_source_message.content),
        ))

    warnings: list[str] = []

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
        model_key=runtime_setting.model_key,
        compression_model_key=runtime_setting.compression_model_key,
        response_length_preset=runtime_setting.response_length_preset,
        recent_message_count=len(messages),
        selected_recent_message_count=len(selected_messages),
        sections=sections,
        warnings=warnings,
    )
