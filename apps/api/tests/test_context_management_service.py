from __future__ import annotations

from dataclasses import asdict

from sqlalchemy import inspect, text
from sqlmodel import SQLModel

from app.db.models import Message, ModelOption
from app.db.session import make_engine, migrate_sqlite_columns
from app.engine.prompt_harness import approx_tokens
from app.engine.prompts import format_message_for_context
from app.services.context_management_service import (
    build_automatic_context_plan,
    ContextCapacity,
    estimate_context_pressure,
    estimate_visible_message_tokens,
    group_complete_turns,
    resolve_model_context_capacity,
    select_raw_tail_groups,
)


def _message(
    message_id: str,
    *,
    content: str = "대화",
    generation_job_id: str | None = None,
    action: str | None = None,
    thought: str | None = None,
    metadata: dict | None = None,
) -> Message:
    return Message(
        id=message_id,
        conversation_id="conv_context",
        speaker_type="character" if generation_job_id else "user",
        speaker_id="char_context" if generation_job_id else "user_001",
        content=content,
        action=action,
        thought=thought,
        generation_job_id=generation_job_id,
        metadata_=metadata or {},
    )


def test_resolve_model_context_capacity_uses_metadata_and_product_budget():
    option = ModelOption(
        id="model_context",
        key="model_context_key",
        provider_account_id="provider_context",
        provider_type="google",
        model="gemini-context",
        label="Gemini Context",
        context_window_tokens=32_000,
        max_output_tokens=8_000,
    )

    capacity = resolve_model_context_capacity(
        option,
        room_prompt_budget_tokens=18_000,
        completion_reserve_tokens=4_000,
        mandatory_reserve_tokens=1_000,
    )

    assert capacity.context_window_tokens == 32_000
    assert capacity.max_output_tokens == 8_000
    assert capacity.completion_reserve_tokens == 4_000
    assert capacity.available_input_tokens == 27_000
    assert capacity.working_input_tokens == 18_000
    assert capacity.source == "model_metadata"
    assert capacity.used_fallback is False


def test_resolve_model_context_capacity_marks_unknown_model_fallback():
    capacity = resolve_model_context_capacity(None, room_prompt_budget_tokens=18_000)

    assert capacity.context_window_tokens == 16_384
    assert capacity.max_output_tokens == 2_048
    assert capacity.available_input_tokens == 13_312
    assert capacity.working_input_tokens == 13_312
    assert capacity.source == "conservative_fallback"
    assert capacity.used_fallback is True


def test_visible_message_estimate_uses_visible_fields_but_not_metadata_content():
    base = _message("msg_base", content="짧은 대화")
    richer = _message("msg_richer", content="짧은 대화", action="문을 열고 들어온다", thought="아직 말하지 말자")
    metadata_only = _message(
        "msg_metadata",
        content="짧은 대화",
        metadata={"provider_payload": "secret-like diagnostics must not affect estimates" * 100},
    )

    assert estimate_visible_message_tokens(base) > 0
    assert estimate_visible_message_tokens(richer) > estimate_visible_message_tokens(base)
    assert estimate_visible_message_tokens(metadata_only) == estimate_visible_message_tokens(base)


def test_visible_message_estimate_matches_renderer_compaction_caps():
    capped = _message(
        "msg_capped",
        content="대" * 240,
        action="행" * 110,
        thought="생" * 70,
    )
    oversized = _message(
        "msg_oversized",
        content="대" * 2_400,
        action="행" * 1_100,
        thought="생" * 700,
    )

    assert estimate_visible_message_tokens(oversized) == estimate_visible_message_tokens(capped)


def test_visible_message_estimate_matches_production_context_renderer():
    messages = [
        _message("msg_user", content="질문", action="다가간다", thought="생각한다"),
        _message("msg_character", content="답변", generation_job_id="job_1", action="웃는다", thought="경계한다"),
        Message(
            id="msg_system",
            conversation_id="conv_context",
            speaker_type="system",
            speaker_id="system",
            content="장면을 전환한다",
        ),
    ]

    for message in messages:
        assert estimate_visible_message_tokens(message) == approx_tokens(
            format_message_for_context(message)
        )


def test_complete_turn_groups_keep_source_and_generated_bubbles_indivisible():
    messages = [
        _message("source_1"),
        _message("reply_1a", generation_job_id="job_1"),
        _message("reply_1b", generation_job_id="job_1"),
        _message("source_2"),
        _message("reply_2", generation_job_id="job_2"),
    ]

    groups = group_complete_turns(messages)

    assert [[message.id for message in group] for group in groups] == [
        ["source_1", "reply_1a", "reply_1b"],
        ["source_2", "reply_2"],
    ]


def test_complete_turn_groups_honor_authoritative_job_source_mapping():
    messages = [
        _message("source_1"),
        _message("source_2"),
        _message("late_reply", generation_job_id="job_1"),
    ]

    groups = group_complete_turns(
        messages,
        job_source_message_ids={"job_1": "source_1"},
    )

    assert [[message.id for message in group] for group in groups] == [
        ["source_1", "source_2", "late_reply"],
    ]


def test_complete_turn_groups_decode_noncontiguous_sync_source_mapping():
    messages = [
        _message("source_sync"),
        _message("intervening_message"),
        _message("reply_sync_a", generation_job_id="sync_turn:source_sync"),
        _message("reply_sync_b", generation_job_id="sync_turn:source_sync"),
    ]

    groups = group_complete_turns(messages)

    assert [[message.id for message in group] for group in groups] == [
        ["source_sync", "intervening_message", "reply_sync_a", "reply_sync_b"],
    ]


def test_complete_turn_groups_multiple_jobs_for_one_source_as_one_turn():
    messages = [
        _message("source_retry"),
        _message("reply_first", generation_job_id="job_first"),
        _message("reply_retry", generation_job_id="job_retry"),
    ]

    groups = group_complete_turns(
        messages,
        job_source_message_ids={
            "job_first": "source_retry",
            "job_retry": "source_retry",
        },
    )

    assert [[message.id for message in group] for group in groups] == [
        ["source_retry", "reply_first", "reply_retry"],
    ]


def test_generated_message_can_be_authoritative_source_for_next_job():
    generated_source = _message("generated_source", generation_job_id="job_previous")
    next_reply = _message("next_reply", generation_job_id="job_next")

    groups = group_complete_turns(
        [generated_source, next_reply],
        job_source_message_ids={"job_next": "generated_source"},
    )

    assert [[message.id for message in group] for group in groups] == [
        ["generated_source", "next_reply"],
    ]


def test_complete_turn_groups_merge_transitive_generation_dependencies():
    source = _message("source_root")
    first_reply = _message("reply_first", generation_job_id="job_first")
    next_reply = _message("reply_next", generation_job_id="job_next")

    groups = group_complete_turns(
        [source, first_reply, next_reply],
        job_source_message_ids={
            "job_first": source.id,
            "job_next": first_reply.id,
        },
    )

    assert [[message.id for message in group] for group in groups] == [
        ["source_root", "reply_first", "reply_next"],
    ]


def test_token_tail_selection_keeps_contiguous_whole_groups_and_oversized_latest_turn():
    groups = group_complete_turns([
        _message("source_1"),
        _message("reply_1", generation_job_id="job_1"),
        _message("source_2"),
        _message("reply_2", generation_job_id="job_2"),
        _message("source_3"),
        _message("reply_3", generation_job_id="job_3"),
    ])
    token_costs = {
        "source_1": 3,
        "reply_1": 3,
        "source_2": 4,
        "reply_2": 4,
        "source_3": 5,
        "reply_3": 5,
    }

    selected = select_raw_tail_groups(
        groups,
        token_budget=18,
        token_estimator=lambda message: token_costs[message.id],
    )
    oversized = select_raw_tail_groups(
        [groups[-1]],
        token_budget=1,
        token_estimator=lambda message: token_costs[message.id],
    )

    assert [[message.id for message in group] for group in selected] == [
        ["source_2", "reply_2"],
        ["source_3", "reply_3"],
    ]
    assert [[message.id for message in group] for group in oversized] == [["source_3", "reply_3"]]


def _automatic_capacity(*, working_tokens: int = 100) -> ContextCapacity:
    return ContextCapacity(
        model_option_key="automatic_model",
        context_window_tokens=200,
        max_output_tokens=50,
        completion_reserve_tokens=50,
        mandatory_reserve_tokens=0,
        available_input_tokens=150,
        working_input_tokens=working_tokens,
        source="model_metadata",
        used_fallback=False,
    )


def test_automatic_plan_measures_full_pressure_and_folds_to_low_watermark():
    messages = [_message(f"source_{index}") for index in range(4)]

    plan = build_automatic_context_plan(
        capacity=_automatic_capacity(),
        mandatory_prompt_tokens=20,
        messages=messages,
        token_estimator=lambda _message: 20,
    )

    assert plan.pressure.status == "high"
    assert plan.pressure.projected_input_tokens == 100
    assert plan.raw_tail_token_budget == 45
    assert plan.fold_message_ids == ("source_0", "source_1")
    assert plan.raw_tail_message_ids == ("source_2", "source_3")


def test_automatic_plan_never_splits_oversized_fold_group_at_batch_limit():
    source = _message("source_large")
    replies = [_message(f"reply_{index:02d}", generation_job_id="job_large") for index in range(48)]
    latest = _message("source_latest")

    plan = build_automatic_context_plan(
        capacity=_automatic_capacity(),
        mandatory_prompt_tokens=0,
        messages=[source, *replies, latest],
        job_source_message_ids={"job_large": source.id},
        token_estimator=lambda _message: 2,
        batch_message_limit=48,
    )

    assert plan.pressure.status == "high"
    assert len(plan.fold_message_ids) == 49
    assert plan.fold_message_ids[0] == source.id
    assert plan.fold_message_ids[-1] == "reply_47"
    assert plan.raw_tail_message_ids == (latest.id,)


def test_automatic_plan_preserves_oversized_newest_turn_and_reports_hard_state():
    source = _message("source_current")
    reply = _message("reply_current", generation_job_id="job_current")

    plan = build_automatic_context_plan(
        capacity=_automatic_capacity(),
        mandatory_prompt_tokens=0,
        messages=[source, reply],
        job_source_message_ids={"job_current": source.id},
        token_estimator=lambda _message: 60,
    )

    assert plan.pressure.status == "hard"
    assert plan.oversized_indivisible_turn is True
    assert plan.fold_message_ids == ()
    assert plan.raw_tail_message_ids == (source.id, reply.id)


def test_pressure_report_contains_only_sanitized_counts_ids_and_estimates():
    messages = [
        _message("source_private", content="PRIVATE USER CONTENT " * 50),
        _message("reply_private", content="PRIVATE MODEL CONTENT " * 50, generation_job_id="job_private"),
    ]
    capacity = ContextCapacity(
        model_option_key="model_key",
        context_window_tokens=2_000,
        max_output_tokens=500,
        completion_reserve_tokens=500,
        mandatory_reserve_tokens=100,
        available_input_tokens=1_400,
        working_input_tokens=300,
        source="model_metadata",
        used_fallback=False,
    )

    report = estimate_context_pressure(
        capacity=capacity,
        mandatory_prompt_tokens=250,
        messages=messages,
    )
    serialized = str(asdict(report))

    assert report.status == "hard"
    assert report.total_group_count == 1
    assert report.selected_group_count == 1
    assert report.backlog_group_count == 0
    assert report.selected_message_ids == ("source_private", "reply_private")
    assert "PRIVATE USER CONTENT" not in serialized
    assert "PRIVATE MODEL CONTENT" not in serialized


def test_empty_minimal_capacity_report_stays_low():
    capacity = ContextCapacity(
        model_option_key=None,
        context_window_tokens=2,
        max_output_tokens=1,
        completion_reserve_tokens=1,
        mandatory_reserve_tokens=0,
        available_input_tokens=1,
        working_input_tokens=1,
        source="conservative_fallback",
        used_fallback=True,
    )

    report = estimate_context_pressure(
        capacity=capacity,
        mandatory_prompt_tokens=0,
        messages=[],
    )

    assert report.status == "low"
    assert report.utilization_ratio == 0


def test_additive_model_context_metadata_migration_preserves_existing_rows(tmp_path):
    test_engine = make_engine(f"sqlite:///{tmp_path / 'legacy-model-options.db'}")
    with test_engine.begin() as connection:
        connection.execute(text(
            "CREATE TABLE model_options ("
            "id VARCHAR PRIMARY KEY, key VARCHAR NOT NULL, provider_account_id VARCHAR NOT NULL, "
            "provider_type VARCHAR NOT NULL, model VARCHAR NOT NULL, label VARCHAR NOT NULL, "
            "enabled BOOLEAN NOT NULL, supports_chat BOOLEAN NOT NULL, supports_compression BOOLEAN NOT NULL, "
            "supports_tts BOOLEAN NOT NULL, model_family VARCHAR NOT NULL, supports_json BOOLEAN NOT NULL, "
            "source VARCHAR NOT NULL, created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL)"
        ))
        connection.execute(text(
            "INSERT INTO model_options ("
            "id, key, provider_account_id, provider_type, model, label, enabled, supports_chat, "
            "supports_compression, supports_tts, model_family, supports_json, source, created_at, updated_at"
            ") VALUES ("
            "'legacy_option', 'legacy_key', 'legacy_provider', 'openai', 'legacy-model', 'Legacy', "
            "1, 1, 1, 0, 'gpt', 1, 'manual', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
        ))

    SQLModel.metadata.create_all(test_engine)
    migrate_sqlite_columns(test_engine)

    columns = {column["name"] for column in inspect(test_engine).get_columns("model_options")}
    assert {"context_window_tokens", "max_output_tokens"}.issubset(columns)
    with test_engine.connect() as connection:
        row = connection.execute(text(
            "SELECT model, context_window_tokens, max_output_tokens FROM model_options WHERE id='legacy_option'"
        )).one()
    assert tuple(row) == ("legacy-model", None, None)
