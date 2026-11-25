"""Actual core + standard graph + actual SDK, synthetic wire and isolated DB."""
import asyncio
import base64
from io import BytesIO
import json

import httpx
from PIL import Image
import pytest
from sqlmodel import SQLModel, Session, create_engine
from sqlalchemy import select

from app.agent.contracts import Principal, SettingsUpdate
from app.agent.service import AgentService
from app.agent.schema import initialize_agent_schema
from app.agent import models
from app.agent.runtime import run_agent_turn
from app.agent.image_generation import execute_image_plan
from app.db.models import Character
from app.engine import litellm_transport as transport


@pytest.fixture
def service(tmp_path):
    engine = create_engine(f'sqlite:///{tmp_path}/fixture.db', connect_args={'check_same_thread': False})
    SQLModel.metadata.create_all(engine)
    initialize_agent_schema(engine)
    service = AgentService(engine)
    service.update_settings(SettingsUpdate(enabled=True))
    yield service
    engine.dispose()


OWNER = Principal('owner', frozenset(), True)


def test_actual_run_receipt_graph_tools_reopen_and_no_recharge(service, monkeypatch):
    calls = []
    def wire(request):
        payload = json.loads(request.content)
        calls.append(payload)
        first = len(calls) == 1
        message = {'role': 'assistant', 'content': None if first else 'Available tools inspected.'}
        if first:
            message['tool_calls'] = [{'id': 'call-capabilities', 'type': 'function',
                'function': {'name': 'lore_capabilities', 'arguments': '{}'}}]
        return httpx.Response(200, json={'id': 'fixture', 'created': 1, 'object': 'chat.completion', 'model': 'gpt-4o',
            'choices': [{'index': 0, 'finish_reason': 'tool_calls' if first else 'stop', 'message': message}],
            'usage': {'prompt_tokens': 2, 'completion_tokens': 3, 'total_tokens': 5}})
    monkeypatch.setattr(transport, 'new_http_transport', lambda: httpx.MockTransport(wire))
    monkeypatch.setattr(service, 'resolve_llm_route', lambda p:
        transport.Route('openai', 'openai', 'gpt-4o', 'https://api.openai.com/v1', 'fixture-private-key'))
    session = service.create_session(OWNER, 'new-session')
    async def exercise():
        result = await run_agent_turn(service, OWNER, session['id'], 'turn', 'What can you do?', [])
        assert result['last_error'] is None
        assert result['messages'][-1]['content'] == 'Available tools inspected.'
        replay = await run_agent_turn(service, OWNER, session['id'], 'turn', 'What can you do?', [])
        assert replay == result
        assert len(calls) == 2
        second = await run_agent_turn(service, OWNER, session['id'], 'turn-2', 'Thanks', [])
        assert second['last_error'] is None
        assert len(second['messages']) == 4
        assert len(calls) == 3
        assert any(m.get('tool_call_id') == 'call-capabilities' for m in calls[-1]['messages'])
    asyncio.run(exercise())
    assert service.get_operation('turn', OWNER)['status'] == 'completed'
    assert b'fixture-private-key' not in service.checkpoint_path.read_bytes()
    assert 'tool_calls' not in json.dumps(service.get_session(session['id'], OWNER))


def test_model_http_rejection_is_safe_in_session_receipt_and_log(service, monkeypatch, caplog):
    calls = []
    def wire(request):
        calls.append(1)
        return httpx.Response(429, json={'error': {'message': 'private-provider-response', 'type': 'rate_limit_error'}})
    monkeypatch.setattr(transport, 'new_http_transport', lambda: httpx.MockTransport(wire))
    monkeypatch.setattr(service, 'resolve_llm_route', lambda p:
        transport.Route('openai', 'openai', 'gpt-4o', 'https://api.openai.com/v1', 'fixture-private-key'))
    sid = service.create_session(OWNER, 'new-session')['id']
    view = asyncio.run(run_agent_turn(service, OWNER, sid, 'rejected-model', 'private-user-input', []))
    receipt = service.get_operation('rejected-model', OWNER)
    assert view['last_error'] == receipt['error'] == 'MODEL_REQUEST_HTTP_ERROR · HTTP 429'
    assert receipt['status'] == 'failed' and calls == [1]
    assert 'request_id=rejected-model' in caplog.text and 'http_status=429' in caplog.text
    assert 'exception=RateLimitError' in caplog.text
    assert not any(value in caplog.text for value in ('private-provider-response', 'private-user-input', 'fixture-private-key'))


def test_actual_retained_image_outputs_and_replay(service, monkeypatch):
    with Session(service.engine) as db:
        db.add(Character(id='fixture-character', name='Fixture', persona='Fixture', avatar_url=None))
        db.commit()
    plan = {'provider_account_id': 'image-account', 'provider_type': 'openai', 'model_id': 'gpt-image-2.5-sunburst',
            'target_id': 'fixture-character', 'target_revision': 0, 'prompt': 'Fixture image',
            'count': 1, 'options': {}, 'reference_output_id': None}
    # Isolate selection discovery only; real approval, claim, retention and SDK remain.
    monkeypatch.setattr(service, 'settings_view', lambda: {
        'image_accounts': [{'id': 'image-account', 'provider_type': 'openai'}],
        'image_provider_account_id': 'image-account', 'image_model_id': 'gpt-image-2.5-sunburst',
        'image_models': [{'id': 'gpt-image-2.5-sunburst', 'supports_reference': True}]})
    monkeypatch.setattr(service, 'resolve_image_route', lambda *args:
        transport.Route('openai', 'openai', 'gpt-image-2.5-sunburst', 'https://api.openai.com/v1', 'fixture-image-key'))
    buffer = BytesIO()
    Image.new('RGB', (8, 8), 'red').save(buffer, format='PNG')
    raw = buffer.getvalue()
    calls = []
    def wire(request):
        calls.append(request)
        return httpx.Response(200, json={'created': 1, 'data': [{'b64_json': base64.b64encode(raw).decode()}]})
    monkeypatch.setattr(transport, 'new_http_transport', lambda: httpx.MockTransport(wire))
    draft = service.plan_image(OWNER, 'fixture-character', plan['prompt'])
    with pytest.raises(ValueError):
        asyncio.run(execute_image_plan(service, OWNER, draft['id'], 'unapproved', draft['version'], draft['preview_hash']))
    assert not calls
    service.approve_draft(OWNER, draft['id'], draft['version'], draft['preview_hash'])
    async def exercise():
        result = await execute_image_plan(service, OWNER, draft['id'], 'image-run', draft['version'], draft['preview_hash'])
        assert result['status'] == 'completed'
        replay = await execute_image_plan(service, OWNER, draft['id'], 'image-run', draft['version'], draft['preview_hash'])
        assert replay == result
        new_key = await execute_image_plan(service, OWNER, draft['id'], 'image-run-new-key', draft['version'], draft['preview_hash'])
        assert new_key['result'] == result['result']
        job = service.get_image_job(result['result']['job_id'], OWNER)
        output = service.image_output(job['outputs'][0]['id'], OWNER)
        assert output == {'data': raw, 'mime_type': 'image/png'}
        with pytest.raises(ValueError):
            service.stage_image_registration(OWNER, job['id'], 'bad-registration',
                [job['outputs'][0]['id']], 'fixture-character', {'avatar_url': 'forbidden'})
        assert service.image_output(job['outputs'][0]['id'], OWNER)['data'] == raw
        again = await execute_image_plan(service, OWNER, draft['id'], 'image-run', draft['version'], draft['preview_hash'])
        assert again['status'] == 'completed'
    asyncio.run(exercise())
    assert len(calls) == 1
    with service.engine.connect() as db:
        assert len(db.execute(select(models.image_outputs)).all()) == 1
    with Session(service.engine) as db:
        assert db.get(Character, 'fixture-character').avatar_url is None
