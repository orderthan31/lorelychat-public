"""Serialized request accounting, with real API/job/preview and fake transport."""
import asyncio
from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone

import pytest
from sqlmodel import select

from app.api import conversations as api
from app.core.config import get_settings
from app.db.models import LLMPromptSnapshot, Message, ModelOption, PostCommitTask, SceneState
from app.engine import prompts
from app.engine.harness_comparison import (
    ComparisonOptions, HistoryAccounting, generation_input_estimate, rendered_input_tokens,
    source_role_overhead,
)
from app.engine.llm_client import LLMClient, LLMResponse
from app.engine.prompt_harness import PromptSection, approx_tokens, compile_prompt_harness
from app.services import conversation_service, context_preview_service
from app.services.context_management_service import resolve_safe_model_context_capacity


def configure(monkeypatch, serialization, policy):
    monkeypatch.setenv('CHAT_HISTORY_SERIALIZATION', serialization)
    monkeypatch.setenv('CHAT_CONTEXT_POLICY', policy)
    monkeypatch.setenv('CHAT_RESPONSE_PROTOCOL', 'tagged_text')
    monkeypatch.setenv('CONTEXT_MANAGEMENT_MODE', 'automatic')
    get_settings.cache_clear()


def room_fixture(client):
    response = client.post('/characters', json={'name': 'Reviewer Canon', 'persona': 'UNCHANGED AUTHORED CARD'})
    assert response.status_code == 200, response.text
    character = response.json()
    response = client.post('/conversations', json={
        'mode': 'user_character', 'genre_mode': 'slice_of_life',
        'participants': [{'type': 'user', 'id': 'user_001'}, {'type': 'character', 'id': character['id']}],
    })
    assert response.status_code == 200, response.text
    return response.json(), character


@pytest.mark.parametrize('serialization', ['native', 'transcript'])
@pytest.mark.parametrize('policy', ['compact', 'expanded', 'full_history'])
@pytest.mark.parametrize('delta', [0, -1])
@pytest.mark.parametrize('history_kind', ['rounding', 'empty', 'unicode_arc'])
def test_actual_preview_threshold(client, session, monkeypatch, record_property, serialization, policy, delta, history_kind):
    configure(monkeypatch, serialization, policy)
    room, character = room_fixture(client)
    start = datetime.now(timezone.utc) - timedelta(days=1)
    texts = [f'Fact {i} ' + ('x' * (i % 4)) for i in range(9)]
    if history_kind == 'empty':
        texts = []
    elif history_kind == 'unicode_arc':
        texts = ['covered before Arc'] + ['한글🙂\nSecond line\n' * 30 + str(i) for i in range(9)]
    for i, text in enumerate(texts):
        session.add(Message(id=f'rounding-{i}', conversation_id=room['id'], speaker_type='user',
                            speaker_id='user_001', content=text, created_at=start + timedelta(seconds=i)))
    if history_kind == 'unicode_arc':
        scene = session.get(SceneState, room['id']) or SceneState(conversation_id=room['id'])
        scene.summary = 'Preserved Arc 이야기\nsecond line'
        scene.last_compression_source_message_id = 'rounding-0'
        scene.compression_revision = 7
        session.add(scene)
    session.commit()
    monkeypatch.setattr(api, '_start_message_generation_worker', lambda *args: None)
    response = client.post(f"/conversations/{room['id']}/messages/jobs", json={
        'speaker_type': 'user', 'speaker_id': 'user_001', 'content': 'THRESHOLD ORIGINAL SOURCE',
    })
    assert response.status_code == 200, response.text
    queued = response.json()
    response = client.get(f"/conversations/{room['id']}/context-preview")
    assert response.status_code == 200, response.text
    preview = response.json()
    threshold = preview['projected_input_tokens']
    capacity = replace(resolve_safe_model_context_capacity(None), working_input_tokens=threshold + delta)
    monkeypatch.setattr(api, 'resolve_safe_model_context_capacity', lambda *args, **kwargs: capacity)
    monkeypatch.setattr(context_preview_service, 'resolve_safe_model_context_capacity', lambda *args, **kwargs: capacity)
    again = client.get(f"/conversations/{room['id']}/context-preview").json()
    assert again['projected_input_tokens'] == threshold
    assert any('context_budget_exceeded' in warning for warning in again['warnings']) == (delta < 0)
    plans, calls, compress = [], [], []
    original = conversation_service.automatic_context_plan

    def plan(*args, **kwargs):
        value = original(*args, **kwargs)
        plans.append(value)
        return value

    async def chat(self, messages, **kwargs):
        calls.append(messages)
        return LLMResponse(content='<m c="C1">A complete answer.</m>', finish_reason='STOP')

    async def no_progress(*args, **kwargs):
        compress.append(1)
        return session.get(SceneState, room['id'])

    monkeypatch.setattr(conversation_service, 'automatic_context_plan', plan)
    monkeypatch.setattr(conversation_service, 'update_scene_orchestration_summary', no_progress)
    monkeypatch.setattr(LLMClient, 'chat', chat)
    # The compression-preview pressure caller must use the same source exclusion.
    context_preview_service.build_compression_preview(session, room['id'])
    if policy != 'full_history':
        assert plans[-1].pressure.projected_input_tokens == threshold
    plans.clear()
    asyncio.run(api._run_message_generation_job_in_session(session, queued['job']['id']))
    status = client.get(f"/conversations/{room['id']}/generation-jobs/{queued['job']['id']}").json()
    if delta == 0:
        assert status['status'] == 'completed', (threshold, plans, status)
        assert len(calls) == 1 and compress == []
        if policy != 'full_history':
            # Post-commit plans project all now-public rows into the SAME frozen
            # non-history envelope, without a new current-source payload.
            scene = session.get(SceneState, room['id']) or SceneState(conversation_id=room['id'])
            suffix = conversation_service.messages_after_compression_boundary(
                scene, conversation_service.list_messages(session, room['id']))
            public_rows = [prompts.format_message_for_context(message, include_thought=False,
                           character_names={character['id']: character['name']}, compact=False)
                           for message in suffix]
            base_sections = [section for section in preview['sections']
                             if section['included'] and section['key'] not in {'recent_messages', 'current_user_input'}
                             and not section['key'].startswith('native_history_')]
            base = '\n\n'.join(f"[{section['title']}]\n{section['content']}" for section in base_sections)
            expected_mandatory = approx_tokens(base) + preview['control_reserve_tokens'] + source_role_overhead('user', serialization)
            if serialization == 'native':
                expected_postcommit = expected_mandatory + sum(approx_tokens(row) + 4 for row in public_rows)
            else:
                rendered = '\n\n'.join([base, f'[{prompts.conversation_history_title(scene)}]\n' + '\n'.join(public_rows)])
                expected_postcommit = approx_tokens(rendered) + preview['control_reserve_tokens']
            source_text = next(section['content'] for section in preview['sections'] if section['key'] == 'current_user_input')
            assert plans[0].pressure.mandatory_prompt_tokens == expected_mandatory + approx_tokens(source_text)
            assert all(item.pressure.projected_input_tokens == expected_postcommit for item in plans[1:])
            assert all(item.pressure.mandatory_prompt_tokens == expected_mandatory for item in plans[1:])
        # Reserved controls are deliberately above actual initial transport;
        # compare the same prepared-input contract, not billed/actual usage.
        assert rendered_input_tokens(calls[0], history_serialization=serialization) + preview['control_reserve_tokens'] == threshold
        snapshot = session.exec(select(LLMPromptSnapshot)).one()
        assert snapshot.messages_json == calls[0]
        assert '\n'.join(message['content'] for message in calls[0]).count('THRESHOLD ORIGINAL SOURCE') == 1
        generated_ids = status['generated_message_ids']
        assert len(generated_ids) == 1
        persisted = client.get(f"/conversations/{room['id']}/messages").json()
        assert sum(message['id'] in generated_ids for message in persisted) == 1
        outbox_ids = [task.id for task in session.exec(select(PostCommitTask)).all()]
        asyncio.run(api._run_message_generation_job_in_session(session, queued['job']['id']))
        assert len(calls) == 1
        assert [task.id for task in session.exec(select(PostCommitTask)).all()] == outbox_ids
        if history_kind == 'rounding' and policy != 'full_history':
            task = session.exec(select(PostCommitTask).where(PostCommitTask.task_type == 'compression')).one()
            task_payload = task.payload_
            assert task_payload['history_accounting']['base_characters'] == len(base)
            worker_calls = []

            async def worker_no_progress(*args, **kwargs):
                worker_calls.append(1)
                return session.get(SceneState, room['id'])

            with monkeypatch.context() as worker_patch, pytest.raises(RuntimeError, match='without boundary or revision progress'):
                worker_patch.setattr(conversation_service, 'update_scene_orchestration_summary', worker_no_progress)
                asyncio.run(api._run_scene_compression_in_session(
                    session, room['id'], generated_character_count=task_payload['generated_character_count'],
                    character_ids=task_payload['character_ids'],
                    mandatory_prompt_tokens=task_payload['mandatory_prompt_tokens'],
                    raw_tail_token_budget=task_payload['raw_tail_token_budget'],
                    history_accounting=task_payload['history_accounting'],
                    comparison_options=ComparisonOptions(serialization, policy),
                ))
            assert plans[-1].pressure.projected_input_tokens == expected_postcommit
            assert plans[-1].pressure.mandatory_prompt_tokens == expected_mandatory
            assert worker_calls == [1]
    else:
        assert status['status'] == 'failed' and status['error_code'] == 'context_maintenance_required'
        assert calls == []
        assert session.get(Message, queued['incoming_message']['id']).content == 'THRESHOLD ORIGINAL SOURCE'
        assert not session.exec(select(PostCommitTask)).all()
        assert not status['generated_message_ids']
    if policy != 'full_history':
        assert plans[0].pressure.projected_input_tokens == threshold
        if delta == 0:
            assert plans[0].pressure.backlog_message_count == 0
    else:
        assert plans == [] and compress == []
    for key, value in {'preview_tokens': threshold, 'working_tokens': threshold + delta,
                       'pressure_tokens': plans[0].pressure.projected_input_tokens if plans else None,
                       'main_calls': len(calls), 'compression_calls': len(compress),
                       'job_status': status['status']}.items():
        record_property(key, value)
    if history_kind == 'unicode_arc':
        saved = session.get(SceneState, room['id'])
        assert (saved.summary, saved.last_compression_source_message_id, saved.compression_revision) == (
            'Preserved Arc 이야기\nsecond line', 'rounding-0', 7)


@pytest.mark.parametrize('serialization', ['native', 'transcript'])
def test_useful_catchup_recounts_after_real_cas(client, session, monkeypatch, serialization):
    configure(monkeypatch, serialization, 'expanded')
    monkeypatch.setenv('CHAT_CONTEXT_INPUT_BUDGET_TOKENS', '4000')
    get_settings.cache_clear()
    room, character = room_fixture(client)
    start = datetime.now(timezone.utc) - timedelta(days=1)
    rows = []
    # Three dependency-closed jobs, including interleaved human input. No fold
    # may cut between source and reply, even with pressure at the boundary.
    for i in range(9):
        row = Message(id=f'fold-{i}', conversation_id=room['id'],
            speaker_type='character' if i % 3 == 2 else 'user',
            speaker_id=character['id'] if i % 3 == 2 else 'user_001',
            content=f'Original {i}\n' + '한글 public fact. ' * 100,
            generation_job_id=f'history-job-{i // 3}' if i % 3 == 2 else None,
            created_at=start + timedelta(seconds=i))
        rows.append(row)
        session.add(row)
    from app.db.models import MessageGenerationJob
    for i in range(3):
        session.add(MessageGenerationJob(id=f'history-job-{i}', conversation_id=room['id'],
            incoming_message_id=f'fold-{3 * i}', status='completed',
            generated_message_ids=[f'fold-{3 * i + 2}']))
    session.commit()
    original_rows = {row.id: row.content for row in rows}
    monkeypatch.setattr(api, '_start_message_generation_worker', lambda *args: None)
    queued = client.post(f"/conversations/{room['id']}/messages/jobs", json={
        'speaker_type': 'user', 'speaker_id': 'user_001', 'content': 'Catch up source',
    }).json()
    preview = client.get(f"/conversations/{room['id']}/context-preview").json()
    assert preview['projected_input_tokens'] > preview['working_input_tokens'] == 4000
    calls, folds, evaluations, harnesses = [], [], [], []
    original_build = prompts.build_multi_character_prompt_harness
    original_plan = conversation_service.automatic_context_plan

    def build(**kwargs):
        result = original_build(**kwargs)
        harnesses.append(result)
        return result

    def plan(*args, **kwargs):
        result = original_plan(*args, **kwargs)
        if not calls:
            source_text = next(section['content'] for section in preview['sections'] if section['key'] == 'current_user_input')
            assert result.pressure.projected_input_tokens == generation_input_estimate(
                harnesses[-1], source_text, 'user', preview['control_reserve_tokens'])
            evaluations.append(result)
        return result

    async def summarize(**kwargs):
        ids = [message.id for message in kwargs['recent_messages']]
        folds.append(ids)
        for i in range(3):
            group = {f'fold-{3 * i + offset}' for offset in range(3)}
            assert not group.intersection(ids) or group.issubset(ids)
        assert queued['incoming_message']['id'] not in ids
        return {'scene': {'summary': 'All original facts preserved in the new Arc.'},
                'memories': [], 'relationships': [], 'battle_events': []}

    async def chat(self, messages, **kwargs):
        calls.append(messages)
        return LLMResponse(content='<m c="C1">A complete answer.</m>', finish_reason='STOP')

    monkeypatch.setattr(prompts, 'build_multi_character_prompt_harness', build)
    monkeypatch.setattr(conversation_service, 'automatic_context_plan', plan)
    monkeypatch.setattr(conversation_service, 'summarize_conversation_state_with_llm', summarize)
    monkeypatch.setattr(LLMClient, 'chat', chat)
    asyncio.run(api._run_message_generation_job_in_session(session, queued['job']['id']))
    status = client.get(f"/conversations/{room['id']}/generation-jobs/{queued['job']['id']}").json()
    assert status['status'] == 'completed', status
    assert len(calls) == 1 and len(folds) == 1 and len(evaluations) == 2
    assert evaluations[0].pressure.status == 'hard' and evaluations[1].pressure.status == 'low'
    assert rendered_input_tokens(calls[0], history_serialization=serialization) + preview['control_reserve_tokens'] == evaluations[1].pressure.projected_input_tokens
    session.expire_all()
    saved = session.get(SceneState, room['id'])
    assert saved.compression_revision == 1
    assert saved.last_compression_source_message_id == folds[0][-1]
    assert all(session.get(Message, key).content == text for key, text in original_rows.items())
    # Exercise the actual CAS again with the stale pre-fold revision.
    assert conversation_service.apply_conversation_compression_update(session, room['id'],
        {'scene': {'summary': 'Stale result must not apply'}},
        source_message_id=folds[0][-1], expected_revision=0,
        expected_boundary_message_id=None, folded_message_ids=folds[0]) is False
    session.expire_all()
    assert session.get(SceneState, room['id']).summary == 'All original facts preserved in the new Arc.'
    asyncio.run(api._run_message_generation_job_in_session(session, queued['job']['id']))
    assert len(calls) == len(folds) == 1


@pytest.mark.parametrize('serialization', ['native', 'transcript'])
@pytest.mark.parametrize('row_count', [0, 1, 9, 49])
@pytest.mark.parametrize('base_length', [0, 1, 2, 7, 113])
def test_sequence_cost_is_whole_compiled_marginal_not_row_sum(serialization, row_count, base_length):
    rows = ['한글🙂\n' + 'x' * (index % 7) for index in range(row_count)]
    sections = [PromptSection('base', 'Fixed', 'b' * base_length, '', '', 10000, True)]
    if serialization == 'transcript':
        sections.append(PromptSection('recent_messages', 'Conversation since then', '\n'.join(rows), '', '', 10000, True))
    harness = compile_prompt_harness(sections=sections, total_budget_tokens=10000)
    accounting = HistoryAccounting.from_harness(harness)
    # Independent reference serialization, including section/header separators.
    expected_base = f'[Fixed]\n{"b" * base_length}' if base_length else ''
    assert accounting.base_tokens == approx_tokens(expected_base)
    expected = approx_tokens(harness.compiled_text)
    if serialization == 'native':
        expected += sum(approx_tokens(row) + 4 for row in rows)
    assert accounting.base_tokens + accounting.tokens(rows, history_serialization=serialization) == expected
    assert HistoryAccounting(**asdict(accounting)) == accounting


@pytest.mark.parametrize('serialization', ['native', 'transcript'])
@pytest.mark.parametrize('route', ['primary', 'fallback'])
def test_real_expanded_budget_keeps_output_reserves(client, session, monkeypatch, serialization, route):
    """No capacity-function patch: exact target plus primary/fallback reserve proof."""
    configure(monkeypatch, serialization, 'expanded')
    room, _ = room_fixture(client)
    start = datetime.now(timezone.utc) - timedelta(days=1)
    for i in range(9):
        session.add(Message(id=f'budget-{i}', conversation_id=room['id'], speaker_type='user',
                            speaker_id='user_001', content=f'Fact {i} ' + 'x' * (i % 4),
                            created_at=start + timedelta(seconds=i)))
    session.commit()
    monkeypatch.setattr(api, '_start_message_generation_worker', lambda *args: None)
    queued = client.post(f"/conversations/{room['id']}/messages/jobs", json={
        'speaker_type': 'user', 'speaker_id': 'user_001', 'content': 'THRESHOLD ORIGINAL SOURCE',
    }).json()
    preview = client.get(f"/conversations/{room['id']}/context-preview").json()
    threshold = preview['projected_input_tokens']
    monkeypatch.setenv('CHAT_CONTEXT_INPUT_BUDGET_TOKENS', str(threshold))
    get_settings.cache_clear()
    again = client.get(f"/conversations/{room['id']}/context-preview").json()
    assert again['projected_input_tokens'] == again['working_input_tokens'] == threshold
    assert again['completion_reserve_tokens'] == preview['completion_reserve_tokens']
    primary = ModelOption(key='primary', context_window_tokens=20000, max_output_tokens=2048)
    fallback = ModelOption(key='fallback', context_window_tokens=12000, max_output_tokens=4096)
    safe = resolve_safe_model_context_capacity(primary, fallback if route == 'fallback' else None,
        comparison_options=ComparisonOptions(serialization, 'expanded', threshold),
        completion_reserve_tokens=4096, room_prompt_budget_tokens=10000)
    assert safe.working_input_tokens == threshold
    assert safe.completion_reserve_tokens == 4096 and safe.mandatory_reserve_tokens == 1024
    calls, compress = [], []

    async def chat(self, messages, **kwargs):
        calls.append(messages)
        return LLMResponse(content='<m c="C1">A complete answer.</m>', finish_reason='STOP')

    async def no_progress(*args, **kwargs):
        compress.append(1)
        return session.get(SceneState, room['id'])

    monkeypatch.setattr(LLMClient, 'chat', chat)
    monkeypatch.setattr(conversation_service, 'update_scene_orchestration_summary', no_progress)
    asyncio.run(api._run_message_generation_job_in_session(session, queued['job']['id']))
    status = client.get(f"/conversations/{room['id']}/generation-jobs/{queued['job']['id']}").json()
    assert status['status'] == 'completed'
    assert len(calls) == 1 and compress == []
