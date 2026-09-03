from __future__ import annotations

import pytest

from app.api.conversations import (
    AutomaticGenerationContextEvaluation,
    ContextMaintenanceRequired,
    _ensure_automatic_generation_context,
)
from app.db.models import Message, SceneState
from app.engine.prompt_harness import compile_prompt_harness
from app.engine.prompts import ContextCoverageError
from app.services.context_management_service import (
    ContextCapacity,
    build_automatic_context_plan,
)


def _capacity() -> ContextCapacity:
    return ContextCapacity(
        model_option_key="preflight_model",
        context_window_tokens=200,
        max_output_tokens=50,
        completion_reserve_tokens=50,
        mandatory_reserve_tokens=0,
        available_input_tokens=150,
        working_input_tokens=100,
        source="model_metadata",
        used_fallback=False,
    )


def _message(message_id: str) -> Message:
    return Message(
        id=message_id,
        conversation_id="conv_preflight",
        speaker_type="user",
        speaker_id="user_001",
        content="context",
    )


def _evaluation(*, token_cost: int, message_count: int) -> AutomaticGenerationContextEvaluation:
    messages = tuple(_message(f"msg_{index}") for index in range(message_count))
    plan = build_automatic_context_plan(
        capacity=_capacity(),
        mandatory_prompt_tokens=0,
        messages=messages,
        token_estimator=lambda _message: token_cost,
    )
    return AutomaticGenerationContextEvaluation(
        scene_state=SceneState(conversation_id="conv_preflight"),
        messages=messages,
        harness=compile_prompt_harness(sections=[], total_budget_tokens=100),
        plan=plan,
        mandatory_without_current=0,
        job_source_message_ids={},
    )


@pytest.mark.asyncio
async def test_automatic_preflight_refreshes_after_each_batch_until_not_hard():
    evaluations = [
        _evaluation(token_cost=50, message_count=3),
        _evaluation(token_cost=50, message_count=2),
        _evaluation(token_cost=20, message_count=1),
    ]
    evaluate_calls = 0
    compressed_plans = []

    def evaluate():
        nonlocal evaluate_calls
        result = evaluations[evaluate_calls]
        evaluate_calls += 1
        return result

    async def compress_once(evaluation):
        compressed_plans.append(evaluation.plan.fold_message_ids)
        return True

    result, batches = await _ensure_automatic_generation_context(
        evaluate=evaluate,
        compress_once=compress_once,
        capacity=_capacity(),
    )

    assert batches == 2
    assert evaluate_calls == 3
    assert len(compressed_plans) == 2
    assert result.plan.pressure.status == "low"


@pytest.mark.asyncio
async def test_automatic_preflight_no_progress_fails_before_chat_provider_step():
    provider_calls = 0

    async def compress_once(_evaluation):
        return False

    async def run_generation_path():
        nonlocal provider_calls
        result = await _ensure_automatic_generation_context(
            evaluate=lambda: _evaluation(token_cost=50, message_count=3),
            compress_once=compress_once,
            capacity=_capacity(),
        )
        provider_calls += 1
        return result

    with pytest.raises(ContextMaintenanceRequired) as exc_info:
        await run_generation_path()

    assert exc_info.value.code == "context_maintenance_required"
    assert exc_info.value.batches == 1
    assert provider_calls == 0


@pytest.mark.asyncio
async def test_automatic_preflight_dangling_boundary_fails_without_compression():
    compression_calls = 0

    def evaluate():
        raise ContextCoverageError("missing boundary id")

    async def compress_once(_evaluation):
        nonlocal compression_calls
        compression_calls += 1
        return True

    with pytest.raises(ContextMaintenanceRequired) as exc_info:
        await _ensure_automatic_generation_context(
            evaluate=evaluate,
            compress_once=compress_once,
            capacity=_capacity(),
        )

    assert exc_info.value.code == "context_maintenance_required"
    assert exc_info.value.projected_tokens == 0
    assert exc_info.value.working_tokens == 100
    assert compression_calls == 0
