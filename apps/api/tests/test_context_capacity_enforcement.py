"""Capacity regressions use synthetic fixtures and mocked transport only."""
from dataclasses import replace
from types import SimpleNamespace
import pytest
from app.api.conversations import AutomaticGenerationContextEvaluation, ContextMaintenanceRequired, _ensure_automatic_generation_context
from app.db.models import Character, Message, SceneState
from app.engine.prompt_harness import compile_prompt_harness
from app.services.context_management_service import ContextCapacity, build_automatic_context_plan
from app.core.config import get_settings

def capacity():
    return ContextCapacity(model_option_key='review', context_window_tokens=200, max_output_tokens=50,
        completion_reserve_tokens=50, mandatory_reserve_tokens=0, available_input_tokens=150,
        working_input_tokens=100, source='model_metadata', used_fallback=False)

@pytest.mark.asyncio
async def test_required_only_overflow_must_not_compress_repeatedly():
    remaining = [Message(id=f'm{i}', conversation_id='review', speaker_type='user', speaker_id='u', content='short') for i in range(160)]
    calls = []
    def evaluate():
        plan = build_automatic_context_plan(capacity=capacity(), mandatory_prompt_tokens=200,
            messages=remaining, token_estimator=lambda m: 1)
        return AutomaticGenerationContextEvaluation(scene_state=SceneState(conversation_id='review'),
            messages=tuple(remaining), harness=compile_prompt_harness(sections=[], total_budget_tokens=100),
            plan=plan, mandatory_without_current=200, job_source_message_ids={})
    async def compress_once(evaluation):
        calls.append(len(evaluation.plan.fold_message_ids))
        folded = set(evaluation.plan.fold_message_ids)
        remaining[:] = [m for m in remaining if m.id not in folded]
        return True
    with pytest.raises(ContextMaintenanceRequired):
        await _ensure_automatic_generation_context(evaluate=evaluate, compress_once=compress_once, capacity=capacity())
    assert calls == [], f'Irreducible 200 mandatory > 100 working tokens still invoked compression batches: {calls}'

def test_api_irreducible_authored_card_does_not_trigger_three_compressions(client, session, monkeypatch):
    from datetime import datetime, timedelta, timezone
    from app.services import conversation_service
    monkeypatch.setenv('CONTEXT_MANAGEMENT_MODE', 'automatic')
    get_settings.cache_clear()
    char = Character(id='review-irreducible', name='Review', persona='Uncut authored canon. ' * 3000)
    session.add(char)
    session.commit()
    room = client.post('/conversations', json={'mode':'user_character', 'participants':[
        {'type':'user','id':'user_001'}, {'type':'character','id':char.id}]}).json()
    start = datetime.now(timezone.utc) - timedelta(days=1)
    for i in range(160):
        session.add(Message(id=f'review-history-{i}', conversation_id=room['id'], speaker_type='user',
            speaker_id='user_001', content='Old public fact.', created_at=start + timedelta(seconds=i)))
    session.commit()
    monkeypatch.setattr('app.api.conversations.resolve_safe_model_context_capacity', lambda *a, **k: capacity())
    compression_batches = []
    async def successful_compression(db, conversation_id, recent_messages, **kwargs):
        scene = db.get(SceneState, conversation_id) or SceneState(conversation_id=conversation_id)
        suffix = conversation_service.messages_after_compression_boundary(scene, recent_messages)
        folded = suffix[:48]
        compression_batches.append(len(folded))
        scene.summary = 'Old public facts remain summarized.'
        scene.last_compression_source_message_id = folded[-1].id
        scene.compression_revision = int(scene.compression_revision or 0) + 1
        db.add(scene)
        db.commit()
        return scene
    monkeypatch.setattr(conversation_service, 'update_scene_orchestration_summary', successful_compression)
    response = client.post(f"/conversations/{room['id']}/messages", json={
        'speaker_type':'user','speaker_id':'user_001','content':'Continue'})
    assert response.status_code == 503
    assert response.json()['detail']['code'] == 'context_maintenance_required'
    assert compression_batches == [], f'Actual API irreducible card overflow: compression_batches={compression_batches}, detail={response.json()}'

@pytest.mark.parametrize('mode', ['legacy', 'shadow'])
def test_protected_overflow_must_not_reach_provider_in_supported_modes(client, session, monkeypatch, mode):
    monkeypatch.setenv('CONTEXT_MANAGEMENT_MODE', mode)
    get_settings.cache_clear()
    char = Character(id='review-character', name='Review', persona='Uncut authored canon. ' * 3000)
    session.add(char)
    session.commit()
    room = client.post('/conversations', json={'mode':'user_character', 'participants':[
        {'type':'user','id':'user_001'}, {'type':'character','id':char.id}]}).json()
    monkeypatch.setattr('app.api.conversations.resolve_safe_model_context_capacity', lambda *a, **k: capacity())
    captured = []
    async def blocked_transport(**kwargs):
        captured.append(kwargs['messages'])
        raise RuntimeError('review intercepted transport; no network')
    monkeypatch.setattr('app.engine.character_runtime.chat_with_optional_conversation_id', blocked_transport)
    response = client.post(f"/conversations/{room['id']}/messages", json={'speaker_type':'user', 'speaker_id':'user_001','content':'Continue'})
    assert response.status_code == 503
    assert response.json()['detail']['code'] == 'context_maintenance_required'
    from sqlmodel import select
    persisted = session.exec(select(Message).where(Message.conversation_id == room['id'])).all()
    assert [(m.speaker_type, m.content) for m in persisted] == [('user', 'Continue')]
    assert not captured


@pytest.mark.parametrize('mode', ['automatic', 'legacy', 'shadow'])
def test_actual_job_capacity_failure_has_no_partial_reply_and_typed_diagnostics(client, session, monkeypatch, mode):
    import asyncio
    from sqlmodel import select
    from app.api.conversations import _run_message_generation_job_in_session
    from app.db.models import MessageGenerationJob, GenerationJobEvent
    monkeypatch.setenv('CONTEXT_MANAGEMENT_MODE', mode)
    get_settings.cache_clear()
    character = client.post('/characters', json={'name':'Capacity', 'persona':'Complete authored card.'}).json()
    room = client.post('/conversations', json={'mode':'user_character', 'participants':[
        {'type':'user','id':'user_001'}, {'type':'character','id':character['id']}]}).json()
    monkeypatch.setattr('app.api.conversations._start_message_generation_worker', lambda job_id: None)
    monkeypatch.setattr('app.api.conversations.resolve_safe_model_context_capacity', lambda *a, **k: capacity())
    calls = []
    async def transport(**kwargs):
        calls.append(kwargs)
        raise AssertionError('capacity failure must precede transport')
    monkeypatch.setattr('app.engine.character_runtime.chat_with_optional_conversation_id', transport)
    response = client.post(f"/conversations/{room['id']}/messages/jobs", json={
        'speaker_type':'user', 'speaker_id':'user_001', 'content':'Keep this exact source'})
    assert response.status_code == 200
    body = response.json()
    job_id = body['job']['id']
    asyncio.run(_run_message_generation_job_in_session(session, job_id))
    session.expire_all()
    job = session.get(MessageGenerationJob, job_id)
    source = session.get(Message, body['incoming_message']['id'])
    assert job.status == 'failed'
    assert 'context_maintenance_required' in job.error_message
    assert source.content == 'Keep this exact source'
    assert source.metadata_['generation_error_code'] == 'context_maintenance_required'
    rows = session.exec(select(Message).where(Message.conversation_id == room['id'])).all()
    assert [row.id for row in rows] == [source.id]
    event = session.exec(select(GenerationJobEvent).where(
        GenerationJobEvent.job_id == job_id, GenerationJobEvent.status == 'failed')).one()
    assert event.payload_['error_code'] == 'context_maintenance_required'
    public = client.get(f"/conversations/{room['id']}/generation-jobs/{job_id}")
    assert public.json()['error_code'] == 'context_maintenance_required'
    assert calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize('source_type,initial_text,expected_calls', [
    ('user', 'oversized ' * 200, 0),
    ('character', 'continue', 0),
    ('user', 'continue', 1),
])
async def test_actual_runtime_checks_initial_continuation_and_retry(
    monkeypatch, source_type, initial_text, expected_calls,
):
    from app.engine.character_runtime import CharacterRuntime
    from app.engine.prompt_harness import PromptSection
    monkeypatch.setenv('CHAT_RESPONSE_PROTOCOL', 'legacy_json')
    get_settings.cache_clear()
    calls, snapshots, persisted = [], [], []
    async def transport(**kwargs):
        calls.append(kwargs['messages'])
        return SimpleNamespace(content='{', finish_reason='STOP')
    monkeypatch.setattr('app.engine.character_runtime.chat_with_optional_conversation_id', transport)
    # Initial user input fits; continuation and semantic retry controls do not.
    small = replace(capacity(), working_input_tokens=20)
    harness = compile_prompt_harness(sections=[
        PromptSection('contract', 'Contract', 'Keep all text.', 'test', 'test', 100, True),
    ], total_budget_tokens=100)
    with pytest.raises(ContextMaintenanceRequired):
        await CharacterRuntime().generate_replies(
            characters=[Character(id='c', name='C')], recent_messages=[],
            user_message=initial_text, source_speaker_type=source_type,
            prepared_harness=harness, context_capacity=small,
            prompt_snapshot_callback=snapshots.append, persist_replies=persisted.append,
        )
    assert len(calls) == expected_calls
    assert persisted == []
    assert snapshots[-1]['metadata']['attempt_diagnostics'][-1]['code'] == 'context_maintenance_required'


def test_safe_capacity_keeps_room_budget_output_reserve_and_unknown_ceiling():
    from app.db.models import ModelOption
    from app.services.context_management_service import resolve_safe_model_context_capacity
    primary = ModelOption(key='wide', context_window_tokens=100000, max_output_tokens=2000)
    fallback = ModelOption(key='narrow', context_window_tokens=8000, max_output_tokens=2000)
    safe = resolve_safe_model_context_capacity(primary, fallback,
        room_prompt_budget_tokens=10000, completion_reserve_tokens=4000)
    assert safe.working_input_tokens == 2976
    assert safe.completion_reserve_tokens == 4000
    unknown = resolve_safe_model_context_capacity(None, primary, room_prompt_budget_tokens=100000)
    assert unknown.context_window_tokens == 16384
    assert unknown.working_input_tokens == 13312
    impossible = resolve_safe_model_context_capacity(fallback, completion_reserve_tokens=10000)
    assert impossible.completion_reserve_tokens == 10000
    assert impossible.working_input_tokens == 0


@pytest.mark.asyncio
async def test_runtime_uses_narrower_reachable_fallback_without_dispatch(monkeypatch):
    from app.db.models import ModelOption
    from app.engine.character_runtime import CharacterRuntime
    from app.engine.llm_client import LLMClient
    from app.engine.prompt_harness import PromptSection
    from app.services.context_management_service import resolve_safe_model_context_capacity
    primary = ModelOption(key='wide', context_window_tokens=100000, max_output_tokens=2000)
    fallback = ModelOption(key='narrow', context_window_tokens=3200, max_output_tokens=2000)
    safe = resolve_safe_model_context_capacity(primary, fallback, room_prompt_budget_tokens=10000)
    assert safe.working_input_tokens == 176
    calls = []
    async def transport(**kwargs):
        calls.append(kwargs)
        raise AssertionError('neither route may receive this request')
    monkeypatch.setattr('app.engine.character_runtime.chat_with_optional_conversation_id', transport)
    text = 'Full authored source. ' * 100
    harness = compile_prompt_harness(sections=[
        PromptSection('fixed', 'Fixed', text, 'test', 'test', 10000, True),
    ], total_budget_tokens=10000)
    with pytest.raises(ContextMaintenanceRequired):
        await CharacterRuntime(fallback_llm_client=LLMClient()).generate_replies(
            characters=[Character(id='c', name='C')], recent_messages=[], user_message='Continue',
            prepared_harness=harness, context_capacity=safe,
        )
    assert calls == []
    assert text in harness.compiled_text


@pytest.mark.asyncio
async def test_rewritable_arc_overflow_still_allows_useful_catchup():
    remaining = [Message(id=f'arc-{i}', conversation_id='arc', speaker_type='user',
        speaker_id='u', content='old') for i in range(4)]
    calls = []
    def evaluate():
        mandatory = 200 if not calls else 10
        plan = build_automatic_context_plan(capacity=capacity(), mandatory_prompt_tokens=mandatory,
            messages=remaining, token_estimator=lambda m: 1)
        return AutomaticGenerationContextEvaluation(
            scene_state=SceneState(conversation_id='arc', summary='rewritable'),
            messages=tuple(remaining), harness=compile_prompt_harness(sections=[], total_budget_tokens=100),
            plan=plan, mandatory_without_current=mandatory, job_source_message_ids={},
            irreducible_prompt_tokens=10,
        )
    async def compress_once(evaluation):
        calls.append(evaluation.plan.fold_message_ids)
        return True
    result, batches = await _ensure_automatic_generation_context(
        evaluate=evaluate, compress_once=compress_once, capacity=capacity())
    assert batches == 1
    assert result.plan.pressure.status == 'low'


@pytest.mark.parametrize('compression_succeeds', [True, False])
def test_api_arc_can_shrink_and_failed_catchup_keeps_summary_and_source(
    client, session, monkeypatch, compression_succeeds,
):
    from datetime import datetime, timedelta, timezone
    from sqlmodel import select
    from app.services import conversation_service
    monkeypatch.setenv('CONTEXT_MANAGEMENT_MODE', 'automatic')
    get_settings.cache_clear()
    character = Character(id='arc-char', name='Arc', persona='A complete small card.')
    session.add(character)
    session.commit()
    room = client.post('/conversations', json={'mode':'user_character', 'participants':[
        {'type':'user','id':'user_001'}, {'type':'character','id':character.id}]}).json()
    start = datetime.now(timezone.utc) - timedelta(days=1)
    for i in range(4):
        session.add(Message(id=f'arc-hist-{i}', conversation_id=room['id'], speaker_type='user',
            speaker_id='user_001', content='An old fact.', created_at=start + timedelta(seconds=i)))
    scene = session.get(SceneState, room['id']) or SceneState(conversation_id=room['id'])
    scene.summary = 'Old story. ' * 10000
    # This opening is no longer rendered once an Arc exists. Removing the Arc
    # for a floor estimate must not accidentally reactivate opening-only text.
    scene.opening_scene = 'Dormant opening hook. ' * 10000
    scene.last_compression_source_message_id = 'arc-hist-0'
    scene.compression_revision = 1
    session.add(scene)
    session.commit()
    old_summary = scene.summary
    generous = replace(capacity(), working_input_tokens=10000, available_input_tokens=12000,
        context_window_tokens=16384)
    monkeypatch.setattr('app.api.conversations.resolve_safe_model_context_capacity', lambda *a, **k: generous)
    calls = []
    async def compress(db, conversation_id, recent_messages, **kwargs):
        current = db.get(SceneState, conversation_id)
        calls.append(current.summary)
        if compression_succeeds:
            current.summary = 'Old facts preserved.'
            current.last_compression_source_message_id = 'arc-hist-2'
            current.compression_revision += 1
            db.add(current)
            db.commit()
        return current
    monkeypatch.setattr(conversation_service, 'update_scene_orchestration_summary', compress)
    response = client.post(f"/conversations/{room['id']}/messages", json={
        'speaker_type':'user','speaker_id':'user_001','content':'Continue from here'})
    assert calls == [old_summary]
    assert response.status_code == (200 if compression_succeeds else 503)
    session.expire_all()
    saved = session.get(SceneState, room['id'])
    rows = session.exec(select(Message).where(Message.conversation_id == room['id'])).all()
    assert sum(m.content == 'Continue from here' for m in rows) == 1
    if not compression_succeeds:
        assert saved.summary == old_summary
        assert saved.compression_revision == 1
        assert saved.last_compression_source_message_id == 'arc-hist-0'
        assert not any(m.speaker_type == 'character' for m in rows)

