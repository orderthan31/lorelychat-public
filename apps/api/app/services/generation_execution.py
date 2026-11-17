"""Generation orchestration and durable runners, independent of HTTP transport."""
import asyncio
import logging
from uuid import uuid4
from contextlib import nullcontext
from dataclasses import asdict, dataclass, replace
from sqlmodel import Session
from app.db.models import BattleMatchRecord, Message, MessageGenerationJob, SceneState
from app.db.session import engine
from app.engine.character_runtime import (
    CharacterRuntime,
    CharacterRuntimeError,
    format_runtime_source_message,
    generation_control_reserve_tokens,
)
from app.engine import free_body_protocol as free_body
from app.engine import command_overlay
from app.engine import prompts
from app.engine.llm_client import LLMClient
from app.engine.prompt_harness import PromptHarness, approx_tokens
from app.services.hardening_core_transactions import isolated_mock_execution
from app.schemas.conversations import MessageCreate
from app.core.config import get_settings
from app.services import (
    character_service,
    chat_command_service,
    conversation_service,
    domain_actor_service,
    genre_domain_service,
    prompt_snapshot_service,
    runtime_settings_service,
    system_prompt_service,
)
from app.services.context_management_service import (
    AutomaticContextPlan,
    ContextCapacity,
    ContextMaintenanceRequired,
    sync_turn_group_id,
)
from app.engine.harness_comparison import HistoryAccounting, ComparisonOptions, source_role_overhead
from app.services.generation_dispatcher import generation_dispatcher
from app.services.post_commit_dispatcher import post_commit_dispatcher

from app.services import post_commit_execution, generation_preparation

logger = logging.getLogger(__name__)

AUTOMATIC_PREGENERATION_MAX_COMPRESSION_BATCHES = 3

@dataclass(frozen=True)
class AutomaticGenerationContextEvaluation:
    scene_state: SceneState
    messages: tuple[Message, ...]
    harness: PromptHarness
    plan: AutomaticContextPlan
    mandatory_without_current: int
    job_source_message_ids: dict[str, str]
    irreducible_prompt_tokens: int | None = None


async def _ensure_automatic_generation_context(
    *,
    evaluate,
    compress_once,
    capacity: ContextCapacity,
    max_batches: int = AUTOMATIC_PREGENERATION_MAX_COMPRESSION_BATCHES,
) -> tuple[AutomaticGenerationContextEvaluation, int]:
    """Refresh exact coverage; once hard catch-up starts, continue below high."""

    batches = 0
    max_batches = max(0, int(max_batches))
    while True:
        try:
            evaluation = evaluate()
        except prompts.ContextCoverageError as exc:
            raise ContextMaintenanceRequired(
                projected_tokens=0,
                working_tokens=capacity.working_input_tokens,
                backlog_groups=0,
                batches=batches,
            ) from exc

        plan = evaluation.plan
        # The Arc is rewritable; authored cards/world/notes/controls are not.
        # Callers without a separate floor have supplied mandatory fixed input.
        irreducible_tokens = evaluation.irreducible_prompt_tokens
        if irreducible_tokens is None:
            irreducible_tokens = plan.pressure.mandatory_prompt_tokens
        if irreducible_tokens > capacity.working_input_tokens:
            raise ContextMaintenanceRequired(
                projected_tokens=plan.pressure.projected_input_tokens,
                working_tokens=capacity.working_input_tokens,
                backlog_groups=plan.pressure.backlog_group_count,
                batches=batches,
            )
        status = plan.pressure.status
        catchup_in_progress = batches > 0
        needs_compression = status == "hard" or (
            catchup_in_progress and status == "high"
        )
        if not needs_compression:
            return evaluation, batches
        if not plan.has_foldable_backlog:
            if status != "hard":
                return evaluation, batches
            raise ContextMaintenanceRequired(
                projected_tokens=plan.pressure.projected_input_tokens,
                working_tokens=plan.pressure.working_input_tokens,
                backlog_groups=plan.pressure.backlog_group_count,
                batches=batches,
            )
        if batches >= max_batches:
            if status != "hard":
                return evaluation, batches
            raise ContextMaintenanceRequired(
                projected_tokens=plan.pressure.projected_input_tokens,
                working_tokens=plan.pressure.working_input_tokens,
                backlog_groups=plan.pressure.backlog_group_count,
                batches=batches,
            )
        progressed = await compress_once(evaluation)
        batches += 1
        if not progressed:
            raise ContextMaintenanceRequired(
                projected_tokens=plan.pressure.projected_input_tokens,
                working_tokens=plan.pressure.working_input_tokens,
                backlog_groups=plan.pressure.backlog_group_count,
                batches=batches,
            )


def runtime_user_message(payload: MessageCreate, *, speaker_name: str | None = None) -> str:
    return format_runtime_source_message(
        speaker_type=payload.speaker_type,
        speaker_id=payload.speaker_id,
        content=payload.content,
        action=payload.action,
        speaker_name=speaker_name,
    )


def _battle_control_value(control: object, key: str):
    if isinstance(control, dict):
        return control.get(key)
    return getattr(control, key, None)


def _battle_character_name(session: Session, character_id: str | None) -> str:
    if not character_id:
        return ""
    character = character_service.get_character(session, character_id)
    return character.name if character else character_id


def build_battle_control_runtime_context(
    session: Session,
    payload: MessageCreate,
    applied_record=None,
    conversation_id: str | None = None,
) -> dict[str, object] | None:
    control = payload.battle_control
    if not control:
        return None
    action = str(_battle_control_value(control, "action") or "").strip().lower()
    if not action:
        return None
    record = applied_record
    if record is None:
        match_id = str(_battle_control_value(control, "match_id") or "").strip()
        if match_id:
            candidate = session.get(BattleMatchRecord, match_id)
            if candidate is not None and (conversation_id is None or candidate.conversation_id == conversation_id):
                record = candidate
    active_pair = None
    if record:
        active_pair = {
            "participant_a_id": record.participant_a_id,
            "participant_a_name": _battle_character_name(session, record.participant_a_id),
            "participant_b_id": record.participant_b_id,
            "participant_b_name": _battle_character_name(session, record.participant_b_id),
        }
    elif action == "start":
        participant_a_id = str(_battle_control_value(control, "participant_a_id") or "").strip()
        participant_b_id = str(_battle_control_value(control, "participant_b_id") or "").strip()
        if participant_a_id and participant_b_id:
            active_pair = {
                "participant_a_id": participant_a_id,
                "participant_a_name": _battle_character_name(session, participant_a_id),
                "participant_b_id": participant_b_id,
                "participant_b_name": _battle_character_name(session, participant_b_id),
            }
    context: dict[str, object] = {"action": action}
    if active_pair:
        context["active_pair"] = active_pair
    if action == "progress":
        favored_id = str(_battle_control_value(control, "favored_character_id") or "").strip()
        advantage = _battle_control_value(control, "advantage")
        if favored_id:
            context["favored_character_id"] = favored_id
            context["favored_character_name"] = _battle_character_name(session, favored_id)
        if advantage is not None:
            context["advantage_percent"] = round(float(advantage) * 100)
    if action == "end":
        winner_id = str(_battle_control_value(control, "winner_id") or "").strip()
        if winner_id:
            context["winner_id"] = winner_id
            context["winner_name"] = _battle_character_name(session, winner_id)
    return context


def select_runtime_room_characters(
    *,
    room_characters,
    recent_messages: list[Message],
    payload: MessageCreate,
    battle_control_context: dict[str, object] | None = None,
):
    """Choose the active speakers for generation in oversized league rooms.

    League conversations may keep every roster member as a participant while the
    visible chat beat only involves a smaller active cast.  Passing the full roster
    into the one-call multi-character prompt lets early participants consume the
    character-card budget and makes later active characters lose their identity.
    """
    if len(room_characters) <= 4:
        return room_characters

    participant_ids = {character.id for character in room_characters}
    active_ids: set[str] = set()

    active_pair = (battle_control_context or {}).get("active_pair") if battle_control_context else None
    if isinstance(active_pair, dict):
        for key in ("participant_a_id", "participant_b_id"):
            value = str(active_pair.get(key) or "").strip()
            if value in participant_ids:
                active_ids.add(value)

    if payload.speaker_type == "character" and payload.speaker_id in participant_ids:
        active_ids.add(payload.speaker_id)

    for message in recent_messages[-24:]:
        if message.speaker_type == "character" and message.speaker_id in participant_ids:
            active_ids.add(message.speaker_id)

    if len(active_ids) >= 2:
        selected = [character for character in room_characters if character.id in active_ids]
        return selected[:6]
    return room_characters


async def persist_generated_reply_messages(
    *,
    session: Session,
    conversation_id: str,
    conversation,
    payload: MessageCreate,
    incoming_message: Message,
    generated_replies,
    room_characters,
    scene_state: SceneState | None,
    is_multi_room: bool,
    min_bubbles: int = 1,
    silent_character_ids: set[str] | None = None,
    suppress_storytelling: bool = False,
) -> list[Message]:
    character_ids = {character.id for character in room_characters}
    silent_character_ids = silent_character_ids or set()
    generated_replies = list(generated_replies or [])
    required_bubbles = 1 if generated_replies and all(getattr(r, "response_protocol", "legacy_json") in free_body.PROTOCOLS for r in generated_replies) else max(1, int(min_bubbles))
    if len(generated_replies) < required_bubbles:
        raise CharacterRuntimeError(
            f"Validated response had {len(generated_replies)} replies before persistence; at least {required_bubbles} are required"
        )
    for index, generated in enumerate(generated_replies, start=1):
        if generated.reply_type == "storytelling":
            if suppress_storytelling or "command_overlay" in (generated.text or "").lower():
                raise CharacterRuntimeError(
                    f"Validated response reply {index} would be filtered before persistence"
                )
            continue
        if generated.character_id not in character_ids or generated.character_id in silent_character_ids:
            raise CharacterRuntimeError(
                f"Validated response reply {index} used a non-persistable character"
            )

    persisted_messages: list[Message] = []
    for generated in generated_replies:
        if generated.reply_type == "storytelling":
            persisted_messages.append(conversation_service.build_message(
                conversation_id,
                MessageCreate(speaker_type="storytelling", speaker_id="storyteller", content=generated.text, metadata=free_body.metadata_for_reply(generated)),
                emotion=generated.emotion,
                action=generated.action,
                thought=generated.thought,
            ))
            continue
        persisted_messages.append(conversation_service.build_message(
            conversation_id,
            MessageCreate(speaker_type="character", speaker_id=generated.character_id, content=generated.text, metadata=free_body.metadata_for_reply(generated)),
            emotion=generated.emotion,
            action=generated.action,
            thought=generated.thought,
        ))
    return persisted_messages


async def generate_replies_from_message(
    *,
    session: Session,
    conversation_id: str,
    conversation,
    payload: MessageCreate,
    incoming_message: Message,
    recent_messages: list[Message] | None = None,
    include_incoming_in_response: bool = True,
    applied_battle_record=None,
    generation_job: MessageGenerationJob | None = None,
) -> list[Message]:
    generation_owner = generation_job.lease_owner if generation_job else None
    generation_attempt = generation_job.attempt_count if generation_job else None
    replies = [incoming_message] if include_incoming_in_response else []
    comparison = ComparisonOptions.from_settings(get_settings())
    full_history = comparison.context_policy == "full_history"
    participants = conversation_service.get_participants(session, conversation_id)
    character_participants = [p for p in participants if p.participant_type == "character"]
    room_cast_roles = {
        participant.participant_id: participant.role
        for participant in character_participants
        if participant.role
    }
    room_characters = [
        character
        for participant in character_participants
        if (character := character_service.get_character(session, participant.participant_id))
    ]

    effective_mode = "character_character" if len(room_characters) >= 2 else "user_character"
    should_generate = (
        (payload.speaker_type in {"user", "system"} and room_characters)
        or (effective_mode == "character_character" and payload.speaker_type == "character" and room_characters)
    )
    if not should_generate:
        return replies

    scene_state = conversation_service.build_live_scene_state(session, conversation, session.get(SceneState, conversation_id))
    runtime_setting = runtime_settings_service.get_effective_setting(session, conversation_id)
    is_multi_room = effective_mode == "character_character"
    preparation = generation_preparation.prepare_generation_route(
        session, runtime_setting,
        genre_mode=conversation_service.normalize_genre_mode(getattr(conversation, "genre_mode", None)),
        is_multi_room=is_multi_room, comparison=comparison,
    )
    preset_key = preparation.preset_key
    token_target = preparation.token_target
    llm_overrides = preparation.primary_overrides
    runtime = CharacterRuntime(
        llm_client=preparation.primary_client,
        fallback_llm_client=preparation.fallback_client,
    )
    min_reply_bubbles = runtime_settings_service.min_bubbles_for_preset(preset_key, is_multi_room=is_multi_room)
    max_reply_bubbles = runtime_settings_service.max_bubbles_for_preset(preset_key, is_multi_room=is_multi_room)
    prompt_settings = system_prompt_service.prompt_settings_map()
    directive = generation_preparation.generation_base_directive(
        prompt_settings, scene_direction=payload.speaker_type == "system",
    )
    command_ids = list((incoming_message.metadata_ or {}).get("command_ids") or (incoming_message.metadata_ or {}).get("active_command_ids") or [])
    if not command_ids:
        single_command_id = (incoming_message.metadata_ or {}).get("command_id") or (incoming_message.metadata_ or {}).get("active_command_id")
        command_ids = [single_command_id] if single_command_id else []
    commands = []
    seen_command_ids = set()
    for command_id in command_ids:
        if not command_id or command_id in seen_command_ids:
            continue
        command = chat_command_service.get_chat_command(session, command_id)
        if command and command.enabled:
            commands.append(command)
            seen_command_ids.add(command.id)
    commands.sort(key=lambda item: (-item.priority, item.name))
    panel_plan = command_overlay.build_plan(commands)
    directive = prompts.compose_generation_directive(
        base_directive=directive,
        commands=commands,
        command_overlay_plan=panel_plan,
        prompt_settings=prompt_settings,
    )
    active_character_ids = {character.id for character in room_characters}
    silent_character_ids = {
        participant.participant_id
        for participant in character_participants
        if (participant.role or "").strip().lower().replace("-", "_").replace(" ", "_") == "silent"
    }
    genre_mode = conversation_service.normalize_genre_mode(getattr(conversation, "genre_mode", None))
    official_domain_context = genre_domain_service.get_official_context(session, conversation)
    battle_control_context = build_battle_control_runtime_context(
        session,
        payload,
        applied_record=applied_battle_record,
        conversation_id=conversation_id,
    )
    recent_for_prompt = recent_messages if recent_messages is not None else conversation_service.list_messages(session, conversation_id)
    def through_source(messages):
        for index, message in enumerate(messages):
            if message.id == incoming_message.id:
                return list(messages[:index + 1])
        raise prompts.ContextCoverageError("current source missing from available history")

    if full_history:
        recent_for_prompt = through_source(conversation_service.list_messages(session, conversation_id))
    room_characters = select_runtime_room_characters(
        room_characters=room_characters,
        recent_messages=recent_for_prompt,
        payload=payload,
        battle_control_context=battle_control_context,
    )
    if not room_characters:
        return replies
    active_character_ids = {character.id for character in room_characters}
    character_names = {character.id: character.name for character in room_characters}
    payload_speaker_name = next((character.name for character in room_characters if character.id == payload.speaker_id), None)
    runtime_message_text = runtime_user_message(payload, speaker_name=payload_speaker_name)

    continuity_by_character, external_memory_context_by_character = generation_preparation.manual_memory_context(
        session, conversation_id, room_characters,
    )
    context_mode = get_settings().context_management_mode
    selected_model_option = preparation.model_option
    capacity = preparation.capacity
    room_prompt_budget = preparation.room_prompt_budget
    job_mapping = conversation_service.generation_job_source_message_ids(
        session,
        conversation_id,
        extra={generation_job.id: incoming_message.id} if generation_job else None,
    )

    latest_offstage_context = ""

    def build_generation_harness(current_scene: SceneState, current_messages: list[Message]):
        nonlocal latest_offstage_context
        base_recall_query = generation_preparation.offstage_recall_query(
            genre_mode=genre_mode, scene=current_scene,
            include_world=bool(conversation.world_setting_id), full_history=full_history,
            source_text=runtime_message_text,
        )
        offstage_recall = domain_actor_service.build_offstage_actor_recall_context(
            session,
            conversation,
            text=base_recall_query,
            active_character_ids=active_character_ids,
            genre_mode=genre_mode,
            query=base_recall_query,
        )
        latest_offstage_context = offstage_recall.content
        chat_command_service.prepare_combined_context(session, panel_plan, conversation_id, current_scene,
            comparison_options=comparison)
        return prompts.build_multi_character_prompt_harness(
            include_world=bool(conversation.world_setting_id),
            comparison_options=comparison,
            command_overlay_plan=panel_plan,
            characters=room_characters,
            recent_messages=[message for message in current_messages if message.id != incoming_message.id],
            user_message=runtime_message_text,
            scene_state=current_scene,
            directive=directive,
            conversation_mode=effective_mode,
            genre_mode=genre_mode,
            continuity_context_by_character=continuity_by_character,
            relationship_context_by_character={},
            external_memory_context_by_character=external_memory_context_by_character,
            offstage_actor_context=offstage_recall.content,
            min_bubbles=min_reply_bubbles,
            max_bubbles=max_reply_bubbles,
            min_output_tokens=token_target,
            prompt_settings=prompt_settings,
            battle_control_context=battle_control_context,
            official_domain_context=official_domain_context,
            room_cast_roles=room_cast_roles,
            provider_type=getattr(selected_model_option, "provider_type", None),
            total_budget_tokens=room_prompt_budget,
            context_management_mode=context_mode,
        )

    prepared_harness = None
    pressure_plan = None
    generation_mandatory_without_current = 0
    catchup_batches = 0
    if context_mode == "automatic" and not full_history:
        compression_overrides = runtime_settings_service.compression_llm_overrides_for_setting(runtime_setting, session=session)
        compression_fallback_overrides = runtime_settings_service.compression_fallback_llm_overrides_for_setting(runtime_setting, session=session)

        rendered_history_token_estimator = conversation_service.automatic_context_message_token_estimator(
            character_names, history_serialization=comparison.history_serialization,
        )

        def generation_history_token_estimator(message: Message) -> int:
            if message.id == incoming_message.id:
                return 0
            return rendered_history_token_estimator(message)

        current_turn_control_tokens = generation_control_reserve_tokens(
            command_overlay_plan=panel_plan,
            speaker_type=payload.speaker_type,
            min_bubbles=min_reply_bubbles,
            max_bubbles=max_reply_bubbles,
            characters=room_characters,
            room_cast_roles=room_cast_roles,
        )

        def evaluate_automatic_context() -> AutomaticGenerationContextEvaluation:
            current_messages = through_source(conversation_service.list_messages(session, conversation_id))
            current_job_mapping = conversation_service.generation_job_source_message_ids(
                session,
                conversation_id,
                extra=(
                    {generation_job.id: incoming_message.id}
                    if generation_job else None
                ),
            )
            current_scene = conversation_service.build_live_scene_state(
                session,
                conversation,
                session.get(SceneState, conversation_id),
            )
            harness = build_generation_harness(current_scene, current_messages)
            accounting = HistoryAccounting.from_harness(harness)
            history_estimator = conversation_service.automatic_context_history_token_estimator(
                character_names, history_serialization=comparison.history_serialization,
                accounting=accounting, excluded_message_ids=frozenset({incoming_message.id}),
            )
            current_input_tokens = approx_tokens(runtime_message_text)
            # Remove history as one serialized unit, including section framing
            # and whole-system rounding, never as individually rounded rows.
            mandatory_tokens = (accounting.base_tokens + current_input_tokens
                                + current_turn_control_tokens
                                + source_role_overhead(payload.speaker_type, comparison.history_serialization))
            plan = conversation_service.automatic_context_plan(
                session.get(SceneState, conversation_id) or SceneState(conversation_id=conversation_id),
                current_messages,
                capacity=capacity,
                mandatory_prompt_tokens=mandatory_tokens,
                job_source_message_ids=current_job_mapping,
                token_estimator=generation_history_token_estimator,
                history_token_estimator=history_estimator,
            )
            # Subtract only the complete rendered Arc, not the scene section:
            # rendering an empty Arc would reactivate dormant opening fields.
            fixed_scene_text = prompts.build_scene_text(current_scene, include_world=bool(conversation.world_setting_id))
            rendered_arc = (current_scene.summary or "").strip()
            if rendered_arc:
                fixed_scene_text = fixed_scene_text.removesuffix(rendered_arc)
            fixed_sections = [
                replace(section, content=fixed_scene_text)
                if section.key == "scene" else section
                for section in harness.sections
                if section.required and section.key != "recent_messages"
            ]
            fixed_harness = prompts.compile_prompt_sections(
                sections=fixed_sections, total_budget_tokens=room_prompt_budget,
            )
            return AutomaticGenerationContextEvaluation(
                scene_state=current_scene,
                messages=tuple(current_messages),
                harness=harness,
                plan=plan,
                mandatory_without_current=max(0, mandatory_tokens - current_input_tokens),
                job_source_message_ids=current_job_mapping,
                irreducible_prompt_tokens=(
                    fixed_harness.used_tokens + current_input_tokens + current_turn_control_tokens
                    + source_role_overhead(payload.speaker_type, comparison.history_serialization)
                ),
            )

        async def compress_automatic_context(
            evaluation: AutomaticGenerationContextEvaluation,
        ) -> bool:
            persisted_before = session.get(SceneState, conversation_id)
            before_revision = int(
                getattr(persisted_before, "compression_revision", 0) or 0
            )
            before_boundary = getattr(
                persisted_before,
                "last_compression_source_message_id",
                None,
            )
            await conversation_service.update_scene_orchestration_summary(
                session,
                conversation_id,
                list(evaluation.messages),
                llm_client=LLMClient(profile="compression", purpose="conversation_compression", overrides=compression_overrides),
                fallback_llm_client=(
                    LLMClient(profile="compression", purpose="conversation_compression_fallback", overrides=compression_fallback_overrides)
                    if compression_fallback_overrides else None
                ),
                character_ids=[character.id for character in room_characters],
                compression_strategy=conversation_service.normalize_compression_strategy(
                    getattr(runtime_setting, "compression_strategy", runtime_settings_service.DEFAULT_COMPRESSION_STRATEGY)
                ),
                raw_tail_token_budget=evaluation.plan.raw_tail_token_budget,
                job_source_message_ids=evaluation.job_source_message_ids,
                token_estimator=generation_history_token_estimator,
            )
            session.expire_all()
            persisted_after = session.get(SceneState, conversation_id)
            after_revision = int(
                getattr(persisted_after, "compression_revision", 0) or 0
            )
            after_boundary = getattr(
                persisted_after,
                "last_compression_source_message_id",
                None,
            )
            return (
                after_revision > before_revision
                and after_boundary != before_boundary
            )

        evaluation, catchup_batches = await _ensure_automatic_generation_context(
            evaluate=evaluate_automatic_context,
            compress_once=compress_automatic_context,
            capacity=capacity,
        )
        scene_state = evaluation.scene_state
        prepared_harness = evaluation.harness
        pressure_plan = evaluation.plan
        generation_mandatory_without_current = evaluation.mandatory_without_current
        job_mapping = evaluation.job_source_message_ids
    else:
        prepared_harness = build_generation_harness(scene_state, recent_for_prompt)
        recent_entry = next(
            (entry for entry in prepared_harness.ledger if entry.key == "recent_messages"),
            None,
        )
        recent_section = next(
            (section for section in prepared_harness.sections if section.key == "recent_messages"),
            None,
        )
        generation_mandatory_without_current = (
            prepared_harness.used_tokens
            - min(
                int(getattr(recent_entry, "used_tokens", 0) or 0),
                approx_tokens(getattr(recent_section, "content", "") or ""),
            )
        )

    history_accounting = HistoryAccounting.from_harness(prepared_harness)
    postcommit_history_estimator = conversation_service.automatic_context_history_token_estimator(
        character_names, history_serialization=comparison.history_serialization,
        accounting=history_accounting,
    )

    async def persist_prompt_snapshot(snapshot: dict):
        try:
            with Session(session.get_bind()) as snapshot_session:
                prompt_snapshot_service.record_prompt_snapshot(snapshot_session, **snapshot)
        except Exception:
            session.rollback()
            logger.exception("Non-critical prompt snapshot persistence failed for conversation %s", conversation_id)

    # Compression/snapshot commits can expire otherwise unused columns. Load
    # every scalar before detaching so later planning cannot reopen a read txn.
    for mapped in list(session.identity_map.values()):
        for key in type(mapped).model_fields:
            getattr(mapped, key)
    session.expunge_all()
    session.rollback()  # Frozen ORM inputs are detached; no transaction spans provider I/O.
    generated_replies = await runtime.generate_replies(
        characters=room_characters,
        recent_messages=[message for message in recent_for_prompt if message.id != incoming_message.id],
        user_message=runtime_message_text,
        scene_state=scene_state,
        directive=directive,
        conversation_mode=effective_mode,
        genre_mode=genre_mode,
        continuity_context_by_character=continuity_by_character,
        relationship_context_by_character={},
        external_memory_context_by_character=external_memory_context_by_character,
        offstage_actor_context=latest_offstage_context,
        min_bubbles=min_reply_bubbles,
        max_bubbles=max_reply_bubbles,
        min_output_tokens=token_target,
        prompt_settings=prompt_settings,
        conversation_id=conversation_id,
        battle_control_context=battle_control_context,
        official_domain_context=official_domain_context,
        room_cast_roles=room_cast_roles,
        persist_replies=None,
        source_message_id=incoming_message.id,
        source_speaker_type=payload.speaker_type,
        prompt_snapshot_callback=persist_prompt_snapshot,
        prepared_harness=prepared_harness,
        context_capacity=capacity,
        command_overlay_plan=panel_plan,
    )
    generated_messages = await persist_generated_reply_messages(
        session=session,
        conversation_id=conversation_id,
        conversation=conversation,
        payload=payload,
        incoming_message=incoming_message,
        generated_replies=generated_replies,
        room_characters=room_characters,
        scene_state=scene_state,
        is_multi_room=is_multi_room,
        min_bubbles=min_reply_bubbles,
        silent_character_ids=silent_character_ids,
    )
    # Commands were resolved before generation so generation_prompt can affect the first pass.
    # Their visible mutations remain transient until the fresh finalization transaction.
    for command in commands:
        generated_messages = await chat_command_service.apply_chat_command_postprocess(
            session=session,
            command=command,
            combined_messages=getattr(generated_replies, "command_panels", {}).get(command.id),
            execution_outcome=getattr(generated_replies, "command_outcomes", panel_plan.outcomes).get(command.id),
            conversation_id=conversation_id,
            source_message=incoming_message,
            generated_messages=generated_messages,
            llm_client=LLMClient(profile="chat", purpose="chat_command_postprocess", overrides=llm_overrides),
            prompt_settings=prompt_settings,
            comparison_options=comparison,
            source_messages=recent_for_prompt if full_history else None,
            context_capacity=capacity,
            commit=False,
        )
    character_ids = [character.id for character in room_characters]
    turn_group_id = assign_generated_turn_group(
        generated_messages,
        incoming_message=incoming_message,
        generation_job=generation_job,
    )
    generated_character_count = sum(1 for message in generated_messages if message.speaker_type == "character")
    if context_mode == "automatic":
        job_mapping = conversation_service.generation_job_source_message_ids(
            session,
            conversation_id,
            extra={turn_group_id: incoming_message.id},
        )
    enqueue_compression = not full_history and generated_character_count > 0 and conversation_service.should_update_scene_orchestration_summary(
        session,
        conversation_id,
        generated_character_messages=generated_character_count,
        interval_turns=runtime_settings_service.clamp_compression_interval_turns(
            getattr(runtime_setting, "compression_interval_turns", runtime_settings_service.DEFAULT_COMPRESSION_INTERVAL_TURNS)
        ),
        prospective_messages=generated_messages,
        context_management_mode=context_mode,
        capacity=capacity,
        mandatory_prompt_tokens=generation_mandatory_without_current,
        history_token_estimator=postcommit_history_estimator,
        job_source_message_ids=job_mapping,
        token_estimator=(
            rendered_history_token_estimator
            if context_mode == "automatic"
            else None
        ),
    )
    compression_raw_tail_token_budget = None
    if enqueue_compression and context_mode == "automatic":
        candidate_messages = conversation_service.list_messages(session, conversation_id)
        persisted_ids = {message.id for message in candidate_messages}
        candidate_messages.extend(message for message in generated_messages if message.id not in persisted_ids)
        enqueue_plan = conversation_service.automatic_context_plan(
            session.get(SceneState, conversation_id) or SceneState(conversation_id=conversation_id),
            candidate_messages,
            capacity=capacity,
            mandatory_prompt_tokens=generation_mandatory_without_current,
            job_source_message_ids=job_mapping,
            token_estimator=rendered_history_token_estimator,
            history_token_estimator=postcommit_history_estimator,
        )
        compression_raw_tail_token_budget = enqueue_plan.raw_tail_token_budget
    post_commit_tasks = post_commit_execution.build_generated_turn_post_commit_tasks(
        comparison_options=comparison,
        session=session,
        conversation=conversation,
        scene_state=scene_state,
        incoming_message=incoming_message,
        generated_messages=generated_messages,
        enqueue_compression=enqueue_compression,
        character_ids=character_ids,
        generation_job_id=generation_job.id if generation_job else None,
        generation_attempt_id=turn_group_id,
        compression_raw_tail_token_budget=compression_raw_tail_token_budget,
        compression_history_accounting=asdict(history_accounting),
        compression_mandatory_prompt_tokens=(
            generation_mandatory_without_current if context_mode == "automatic" else None
        ),
    )
    source_metadata = dict(incoming_message.metadata_ or {})
    finalization_bind = session.get_bind()
    session.rollback()
    with Session(finalization_bind) as final_session:
        final_job = final_session.get(MessageGenerationJob, generation_job.id) if generation_job else None
        if final_job and final_job.cancel_requested_at is not None:
            conversation_service.mark_message_generation_job_cancelled(final_session, final_job)
            return replies
        generated_messages = conversation_service.finalize_message_generation_turn(
            final_session,
            final_job,
            generated_messages,
            source_message_id=incoming_message.id,
            source_metadata=source_metadata,
            post_commit_tasks=post_commit_tasks,
            expected_owner=generation_owner, expected_attempt=generation_attempt,
        )
    await post_commit_execution.process_inline_post_commit_tasks(finalization_bind, post_commit_tasks)
    post_commit_dispatcher.wake()
    replies.extend(generated_messages)
    return replies


def assign_generated_turn_group(
    generated_messages: list[Message],
    *,
    incoming_message: Message,
    generation_job: MessageGenerationJob | None,
) -> str:
    if not generated_messages:
        raise ValueError("A generation attempt requires at least one reply")
    turn_group_id = (
        generation_job.id
        if generation_job is not None
        else sync_turn_group_id(incoming_message.id, generated_messages[0].id)
    )
    for reply_index, generated_message in enumerate(generated_messages):
        generated_message.generation_job_id = turn_group_id
        generated_message.reply_index = reply_index
    return turn_group_id


def _message_payload_from_stored_message(message: Message) -> MessageCreate:
    metadata = dict(message.metadata_ or {})
    return MessageCreate(
        speaker_type=message.speaker_type,
        speaker_id=message.speaker_id,
        content=message.content,
        action=message.action,
        thought=message.thought,
        metadata=metadata,
        battle_control=metadata.get("battle_control"),
    )


async def run_message_generation_job_in_session(
    session: Session,
    job_id: str,
    lease_owner: str | None = None,
) -> None:
    inline_execution = False
    job = conversation_service.get_message_generation_job(session, job_id)
    if not job or job.status in {"completed", "failed", "cancelled"}:
        return
    if (job.execution_options or {}).get("execution_mode") == "mock" and not get_settings().llm_mock:
        logger.warning("Mock-only generation job held without execution job=%s", job_id)
        return
    if job.status == "queued" and lease_owner is None:
        job = conversation_service.claim_next_message_generation_job(session,
            lease_owner="inline:" + uuid4().hex, job_id=job_id,
            execution_mode="mock" if (job.execution_options or {}).get("execution_mode") == "mock" else None)
        if job is None:
            return
        lease_owner = job.lease_owner
        inline_execution = True
    elif job.status != "running" or lease_owner is None:
        return
    if lease_owner is not None and job.lease_owner != lease_owner:
        return
    if job.cancel_requested_at is not None:
        conversation_service.mark_message_generation_job_cancelled(session, job)
        return
    conversation = conversation_service.get_conversation(session, job.conversation_id)
    incoming_message = conversation_service.get_message(session, job.incoming_message_id)
    if not conversation or not incoming_message:
        conversation_service.mark_message_generation_job_failed(session, job, RuntimeError("Conversation or incoming message not found"))
        return
    payload = _message_payload_from_stored_message(incoming_message)
    worker_owner, worker_attempt = job.lease_owner, job.attempt_count
    extra_generation = {}
    options = dict(job.execution_options or {})
    if options.get("kind") == "regeneration":
        payload = MessageCreate.model_validate(options["payload"])
        ordered_ids = list(options.get("recent_message_ids") or [])
        captured = [session.get(Message, mid) for mid in ordered_ids]
        if any(message is None or message.conversation_id != job.conversation_id for message in captured):
            conversation_service.mark_message_generation_job_failed(session, job, RuntimeError("Regeneration context was deleted; it will not be restored"))
            return
        extra_generation["recent_messages"] = captured
    with (generation_dispatcher.track_inline_job(session.get_bind(), job_id, worker_owner) if inline_execution else nullcontext()):
        try:
            replies = await generate_replies_from_message(
                session=session,
                conversation_id=job.conversation_id,
                conversation=conversation,
                payload=payload,
                incoming_message=incoming_message,
                include_incoming_in_response=False,
                generation_job=job,
                **extra_generation,
            )
            session.expire_all()
            fresh_job = conversation_service.get_message_generation_job(session, job_id)
            if fresh_job and fresh_job.status == "running" and fresh_job.lease_owner == worker_owner and fresh_job.attempt_count == worker_attempt:
                conversation_service.mark_message_generation_job_completed(
                    session,
                    fresh_job,
                    [message.id for message in replies],
                    expected_owner=worker_owner, expected_attempt=worker_attempt,
                )
        except Exception as exc:
            session.rollback()
            fresh_job = conversation_service.get_message_generation_job(session, job_id)
            if fresh_job and fresh_job.status == "completed":
                logger.warning("Generation finalization committed before a late runner error for job=%s", job_id)
                return
            fresh_job = conversation_service.get_message_generation_job(session, job_id)
            if fresh_job and fresh_job.status == "running":
                conversation_service.mark_message_generation_job_failed(session, fresh_job, exc,
                    expected_owner=worker_owner, expected_attempt=worker_attempt)
            logger.exception("Message generation job failed for job=%s conversation=%s source_message=%s", job_id, job.conversation_id, incoming_message.id)


async def run_message_generation_job(job_id: str, lease_owner: str | None = None) -> None:
    with Session(engine) as session:
        await run_message_generation_job_in_session(session, job_id, lease_owner)


def run_message_generation_job_sync(job_id: str, lease_owner: str) -> None:
    asyncio.run(run_message_generation_job(job_id, lease_owner))


async def run_mock_generation_queue(bind) -> None:
    # No global dispatcher/engine rebinding. This runner is reachable only in
    # explicit mock mode and uses exactly the request's synthetic/onboarding DB.
    if not get_settings().llm_mock:
        return
    owner = "mock:" + uuid4().hex
    while True:
        with Session(bind) as worker_session:
            conversation_service.requeue_expired_message_generation_jobs(worker_session, execution_mode="mock")
            claimed = conversation_service.claim_next_message_generation_job(worker_session, lease_owner=owner, execution_mode="mock")
            if claimed is None:
                return
            token = isolated_mock_execution.set(True)
            try:
                await run_message_generation_job_in_session(worker_session, claimed.id, owner)
            finally:
                isolated_mock_execution.reset(token)
