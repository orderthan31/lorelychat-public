"""Lease-fenced derived work and post-commit execution."""
import asyncio
import logging
from app.services import generation_preparation
import threading
from sqlmodel import Session
from app.db.models import Message, PostCommitTask, SceneState
from app.db.session import engine
from app.engine.llm_client import LLMClient
from app.services.hardening_core_transactions import isolated_mock_execution
from sqlalchemy import update as sa_update
from sqlalchemy.exc import SQLAlchemyError
from app.core.config import get_settings
from app.services import (
    asset_decision_service,
    asset_service,
    character_service,
    conversation_service,
    runtime_settings_service,
    tts_service,
)
from app.engine.harness_comparison import HistoryAccounting, ComparisonOptions


logger = logging.getLogger(__name__)


def build_generated_turn_post_commit_tasks(
    *,
    session: Session,
    conversation,
    scene_state: SceneState | None,
    incoming_message: Message,
    generated_messages: list[Message],
    enqueue_compression: bool,
    character_ids: list[str],
    generation_job_id: str | None = None,
    generation_attempt_id: str | None = None,
    compression_raw_tail_token_budget: int | None = None,
    compression_mandatory_prompt_tokens: int | None = None,
    compression_history_accounting: dict | None = None,
    comparison_options: ComparisonOptions | None = None,
) -> list[dict]:
    comparison = comparison_options or ComparisonOptions.from_settings(get_settings())
    if generation_job_id:
        identity = f"job:{generation_job_id}"
    elif generation_attempt_id:
        identity = f"sync:{generation_attempt_id}"
    else:
        raise ValueError("generation attempt identity is required")
    tasks: list[dict] = []
    # Decision work is opt-in and post-commit: no provider await may occur in
    # the primary reply-set finalizer. Mock jobs never create paid work.
    decision_key = None
    if not isolated_mock_execution.get() and not get_settings().llm_mock:
        try:
            with session.no_autoflush:
                decision_key = asset_decision_service.decision_model_key_for_turn(session, conversation.id)
        except SQLAlchemyError:
            raise
        except Exception:
            logger.warning("Optional asset decision configuration unavailable; using legacy selection")
    decision_message_ids: list[str] = []

    reserved_asset_ids: dict[str, set[str]] = {}
    for reply_index, message in enumerate(generated_messages):
        if message.speaker_type != "character" or not message.speaker_id:
            continue
        task_prefix = f"{identity}:reply:{reply_index}"
        try:
            if decision_key:
                decision_message_ids.append(message.id)
                selected_asset = None
            else:
                selected_asset = asset_service.select_asset_for_message(
                    session,
                    conversation_id=conversation.id,
                    character_id=message.speaker_id,
                    emotion=message.emotion,
                    action=message.action,
                    content=message.content,
                    scene_state=scene_state,
                    excluded_asset_ids=reserved_asset_ids.setdefault(message.speaker_id, set()),
                    selection_seed=message.id,
                )
        except SQLAlchemyError:
            # A broken persistence session is not an optional policy failure.
            raise
        except Exception:
            # Selection alone is optional. Do not log prose/policy/exception text
            # or catch the finalizer/task-persistence boundary below.
            logger.warning("Optional asset selection failed; preserving validated primary")
            selected_asset = None
        if selected_asset is not None:
            reserved_asset_ids[message.speaker_id].add(selected_asset.id)
            tasks.append({
                "unique_key": f"{task_prefix}:asset",
                "task_type": "asset",
                "payload": {
                    "conversation_id": conversation.id,
                    "message_id": message.id,
                    "asset_id": selected_asset.id,
                },
            })
        if not isolated_mock_execution.get() and conversation.tts_enabled and (message.content or "").strip():
            tasks.append({
                "unique_key": f"{task_prefix}:tts",
                "task_type": "tts",
                "payload": {"conversation_id": conversation.id, "message_id": message.id},
            })

    if decision_message_ids:
        tasks.append({
            "unique_key": f"{identity}:asset_decision",
            "task_type": "asset_decision",
            "payload": {
                "conversation_id": conversation.id,
                "message_ids": decision_message_ids,
                "decision_model_option_key": decision_key,
                "turn_id": generation_job_id or generation_attempt_id,
            },
        })
    if not isolated_mock_execution.get() and enqueue_compression and comparison.context_policy != "full_history":
        tasks.append({
            "unique_key": f"{identity}:compression",
            "task_type": "compression",
            "payload": {
                "conversation_id": conversation.id,
                "generated_character_count": sum(1 for message in generated_messages if message.speaker_type == "character"),
                "character_ids": character_ids,
                "raw_tail_token_budget": compression_raw_tail_token_budget,
                "mandatory_prompt_tokens": compression_mandatory_prompt_tokens,
                "history_accounting": compression_history_accounting,
                "chat_context_policy": comparison.context_policy,
                "chat_history_serialization": comparison.history_serialization,
                "chat_context_input_budget_tokens": comparison.input_budget_tokens,
            },
        })
    return tasks


async def run_scene_compression_in_session(
    session: Session,
    conversation_id: str,
    *,
    generated_character_count: int,
    character_ids: list[str],
    raw_tail_token_budget: int | None = None,
    mandatory_prompt_tokens: int | None = None,
    history_accounting: dict | None = None,
    comparison_options: ComparisonOptions | None = None,
) -> bool:
    comparison = comparison_options or ComparisonOptions.from_settings(get_settings())
    if comparison.context_policy == "full_history" or getattr(get_settings(), "chat_context_policy", "compact") == "full_history":
        return True
    if generated_character_count <= 0:
        return True
    conversation = conversation_service.get_conversation(session, conversation_id)
    if not conversation:
        return True
    runtime_setting = runtime_settings_service.get_effective_setting(session, conversation_id)
    current_messages = conversation_service.list_messages(session, conversation_id)
    current_scene = session.get(SceneState, conversation_id) or SceneState(conversation_id=conversation_id)
    job_mapping = conversation_service.generation_job_source_message_ids(session, conversation_id)
    character_names = {
        character_id: character.name
        for character_id in character_ids
        if (character := character_service.get_character(session, character_id)) is not None
    }
    history_token_estimator = conversation_service.automatic_context_message_token_estimator(
        character_names, history_serialization=comparison.history_serialization,
    )
    if get_settings().context_management_mode == "automatic":
        automatic_capacity = generation_preparation.prepare_generation_route(
            session, runtime_setting, genre_mode=getattr(conversation, "genre_mode", None),
            is_multi_room=len(character_ids) >= 2, comparison=comparison,
        ).capacity
        execution_plan = conversation_service.automatic_context_plan(
            current_scene,
            current_messages,
            capacity=automatic_capacity,
            mandatory_prompt_tokens=max(0, int(mandatory_prompt_tokens or 0)),
            job_source_message_ids=job_mapping,
            token_estimator=history_token_estimator,
            history_token_estimator=conversation_service.automatic_context_history_token_estimator(
                character_names, history_serialization=comparison.history_serialization,
                accounting=HistoryAccounting(**history_accounting) if history_accounting else None,
            ),
        )
        if execution_plan.pressure.status == "low":
            return True
        raw_tail_token_budget = execution_plan.raw_tail_token_budget
    selector_kwargs = {
        "raw_tail_token_budget": raw_tail_token_budget,
        "job_source_message_ids": job_mapping,
        "token_estimator": history_token_estimator,
    }
    try:
        pending_batch, _ = conversation_service.select_incremental_compression_batch(
            current_scene,
            current_messages,
            **selector_kwargs,
        )
    except ValueError:
        pending_batch = current_messages
    if not pending_batch:
        return True

    before = session.get(SceneState, conversation_id)
    before_revision = int(getattr(before, "compression_revision", 0) or 0)
    before_boundary = getattr(before, "last_compression_source_message_id", None)
    compression_overrides = runtime_settings_service.compression_llm_overrides_for_setting(runtime_setting, session=session)
    compression_fallback_overrides = runtime_settings_service.compression_fallback_llm_overrides_for_setting(runtime_setting, session=session)
    await conversation_service.update_scene_orchestration_summary(
        session,
        conversation_id,
        conversation_service.list_messages(session, conversation_id),
        llm_client=LLMClient(profile="compression", purpose="conversation_compression", overrides=compression_overrides),
        fallback_llm_client=(
            LLMClient(profile="compression", purpose="conversation_compression_fallback", overrides=compression_fallback_overrides)
            if compression_fallback_overrides else None
        ),
        character_ids=character_ids,
        compression_strategy=conversation_service.normalize_compression_strategy(
            getattr(runtime_setting, "compression_strategy", runtime_settings_service.DEFAULT_COMPRESSION_STRATEGY)
        ),
        **selector_kwargs,
    )

    session.expire_all()
    after = session.get(SceneState, conversation_id)
    after_revision = int(getattr(after, "compression_revision", 0) or 0)
    after_boundary = getattr(after, "last_compression_source_message_id", None)
    if after_revision > before_revision and after_boundary != before_boundary:
        return True

    selector_scene = after or SceneState(conversation_id=conversation_id)
    try:
        remaining_batch, _ = conversation_service.select_incremental_compression_batch(
            selector_scene,
            conversation_service.list_messages(session, conversation_id),
            **selector_kwargs,
        )
    except ValueError as exc:
        raise RuntimeError(
            f"compression boundary validation failed for conversation {conversation_id}"
        ) from exc
    if not remaining_batch:
        return True
    raise RuntimeError(
        "compression completed without boundary or revision progress "
        f"for conversation {conversation_id}; foldable_messages={len(remaining_batch)}"
    )


async def execute_claimed_post_commit_task(
    session: Session,
    task: PostCommitTask,
    *,
    lease_owner: str,
) -> None:
    if task.status != "processing" or task.lease_owner != lease_owner:
        raise RuntimeError("Post-commit task lease is not owned by this worker")
    claimed_attempt = task.attempt_count
    task_id = task.id
    payload = dict(task.payload_ or {})
    if task.task_type == "asset_decision":
        conversation_id = payload.get("conversation_id")
        message_ids = payload.get("message_ids")
        model_key = payload.get("decision_model_option_key")
        if (not isinstance(conversation_id, str) or not isinstance(model_key, str)
                or not isinstance(message_ids, list)
                or any(not isinstance(item, str) for item in message_ids)):
            raise ValueError("Invalid asset decision task payload")
        def apply_fence():
            # This no-op UPDATE starts a real SQLite write transaction, unlike
            # SELECT/autobegin. Keep it through authoritative source validation,
            # attachments and the final task acknowledgement below.
            with session.no_autoflush:
                result = session.exec(sa_update(PostCommitTask).where(
                    PostCommitTask.id == task_id,
                    PostCommitTask.status == "processing",
                    PostCommitTask.lease_owner == lease_owner,
                    PostCommitTask.attempt_count == claimed_attempt,
                ).values(updated_at=PostCommitTask.updated_at)
                  .execution_options(synchronize_session=False))
            return result.rowcount == 1
        diagnostics = {}

        def reserve_send():
            # Durable pessimistic marker: a crash before the actual send may
            # miss a recommendation, but reclaim must never bill a second send.
            if not apply_fence():
                session.rollback()
                raise RuntimeError("Post-commit task lease was lost before send")
            session.expire_all()
            current = session.get(PostCommitTask, task_id)
            current_payload = dict(current.payload_ or {})
            if "decision_send" in current_payload:
                session.rollback()
                return False
            current_payload["decision_send"] = {
                "version": 1, "state": "send_may_have_started", "attempt": claimed_attempt,
            }
            current_payload["decision_diagnostics"] = asset_decision_service.bounded_diagnostics(diagnostics)
            current.payload_ = current_payload
            session.add(current)
            session.commit()  # Must precede provider I/O, separate from link+ack.
            return True

        if "decision_send" in payload or claimed_attempt > 1:
            if not apply_fence():
                raise RuntimeError("Post-commit task lease was lost before recovery")
            session.expire_all()
            # Do not re-plan or fallback after an ambiguous prior send, even if
            # settings changed or the current catalog now has no candidates.
            previous = payload.get("decision_diagnostics")
            diagnostics = {
                **(asset_decision_service.bounded_diagnostics(previous) if isinstance(previous, dict) else {}),
                "policy_version": asset_decision_service.POLICY_VERSION,
                "outcome": "send_recovery_abstention", "attached_count": 0,
                "target_count": len(message_ids),
            }
            selections = []
        else:
            selections = await asset_decision_service.select_turn_assets(
                session,
                conversation_id=conversation_id,
                message_ids=message_ids,
                model_option_key=model_key,
                apply_fence=apply_fence,
                reserve_send=reserve_send,
                diagnostics=diagnostics,
            )
        # The selector ends its read transaction before provider I/O and
        # revalidates source/settings/assets afterwards. Final links and the
        # lease-fenced acknowledgement still commit in one transaction.
        task = session.get(PostCommitTask, task_id)
        if task is None:
            raise RuntimeError("Post-commit task disappeared during asset decision")
        for message_id, asset_id in selections:
            asset_service.attach_asset_to_message(session, message_id, asset_id, commit=False)
        task.payload_ = {**dict(task.payload_ or {}),
                         "decision_diagnostics": asset_decision_service.bounded_diagnostics(diagnostics)}
        session.add(task)
    elif task.task_type == "asset":
        message = session.get(Message, payload.get("message_id"))
        asset_id = payload.get("asset_id")
        if message is not None and asset_id:
            asset_service.attach_asset_to_message(session, message.id, str(asset_id), commit=False)
    elif task.task_type == "tts":
        message = session.get(Message, payload.get("message_id"))
        if message is not None:
            character = character_service.get_character(session, message.speaker_id) if message.speaker_id else None
            tts_service.ensure_message_tts(session, message, character=character, commit=False)
    elif task.task_type == "continuity":
        # Compatibility no-op for already queued tasks from the retired
        # auto-derived relationship/continuity pipeline.
        pass
    elif task.task_type == "compression":
        conversation_id = str(payload.get("conversation_id") or "")
        if conversation_id:
            from app.services.hardening_core_transactions import compression_post_commit_fence
            fence_token = compression_post_commit_fence.set((task.id, lease_owner, task.attempt_count))
            try:
                await run_scene_compression_in_session(
                    session,
                    conversation_id,
                    generated_character_count=int(payload.get("generated_character_count") or 0),
                    comparison_options=ComparisonOptions(
                        payload.get("chat_history_serialization", "transcript"),
                        payload.get("chat_context_policy", "compact"),
                        payload.get("chat_context_input_budget_tokens"),
                    ),
                    character_ids=[str(item) for item in (payload.get("character_ids") or []) if item],
                    raw_tail_token_budget=(
                        int(payload["raw_tail_token_budget"])
                        if payload.get("raw_tail_token_budget") is not None else None
                    ),
                    history_accounting=payload.get("history_accounting"),
                    mandatory_prompt_tokens=(
                        int(payload["mandatory_prompt_tokens"])
                        if payload.get("mandatory_prompt_tokens") is not None else None
                    ),
                )
            finally:
                compression_post_commit_fence.reset(fence_token)
            session.expire_all()
            refreshed = session.get(PostCommitTask, task.id)
            if refreshed is None:
                raise RuntimeError("Post-commit task disappeared during compression")
            task = refreshed
    else:
        raise ValueError(f"Unsupported post-commit task type: {task.task_type}")
    if not conversation_service.mark_post_commit_task_completed(
        session,
        task,
        lease_owner=lease_owner,
        expected_attempt=claimed_attempt,
        commit=False,
    ):
        raise RuntimeError("Post-commit task lease was lost before completion")
    session.commit()


def run_post_commit_task_sync(task_id: str, lease_owner: str) -> None:
    with Session(engine) as session:
        task = session.get(PostCommitTask, task_id)
        if task is None:
            return
        try:
            asyncio.run(execute_claimed_post_commit_task(session, task, lease_owner=lease_owner))
        except Exception:
            session.rollback()
            raise


async def process_inline_post_commit_tasks(bind, task_specs: list[dict]) -> None:
    inline_types = {"asset", "asset_decision"}
    for task_spec in task_specs:
        if task_spec.get("task_type") not in inline_types:
            continue
        lease_owner = f"inline:{threading.get_ident()}"
        with Session(bind) as session:
            claimed = conversation_service.claim_post_commit_task_by_unique_key(
                session,
                str(task_spec["unique_key"]),
                lease_owner=lease_owner,
                lease_seconds=60,
            )
            if claimed is None:
                continue
            try:
                await execute_claimed_post_commit_task(session, claimed, lease_owner=lease_owner)
            except Exception as exc:
                session.rollback()
                refreshed = session.get(PostCommitTask, claimed.id)
                if refreshed is not None and refreshed.status == "processing" and refreshed.lease_owner == lease_owner:
                    conversation_service.mark_post_commit_task_failed(
                        session,
                        refreshed,
                        exc,
                        lease_owner=lease_owner,
                    )
                logger.exception("Inline post-commit task failed: %s", claimed.id)
