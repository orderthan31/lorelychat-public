"""Synthetic session image feedback; no model/provider dispatch or live DB."""
import asyncio
import json

import pytest
from sqlalchemy import func, select
from sqlmodel import Session

from app.agent import models as m
from app.agent.contracts import AgentError, AgentSessionView, SettingsUpdate
from app.agent.operations import AgentOperations
from app.db.models import Character
from test_image_consistency import setup_image


def test_image_read_appears_before_plan_without_approval(service, owner, monkeypatch, tmp_path):
    setup_image(service, monkeypatch, tmp_path)
    monkeypatch.setattr(service, 'resolve_image_route', lambda *args: pytest.fail('No paid dispatch allowed'))
    session = service.create_session(owner, 'create-feedback')
    service.accept_run(owner, session['id'], 'read-feedback', 'Create a new outfit', [])
    ops = AgentOperations(service, owner, session['id'], 'read-feedback')
    context = asyncio.run(ops.execute('lore_image_context', {'target_id': 'c'}))
    view = AgentSessionView.model_validate(service.get_session(session['id'], owner)).model_dump()
    assert view['busy'] and not view['drafts']
    item = view['image_contexts'][0]
    assert item['target_id'] == 'c' and item['target_label'] == 'Ada'
    assert item['context']['candidates'][0]['available']
    assert item['context']['inventory_digest'] == context['inventory_digest']
    assert '/uploads/' not in json.dumps(view)
    assert '_active_run' not in json.dumps(view)
    with service.engine.connect() as c:
        assert c.execute(select(func.count()).select_from(m.image_jobs)).scalar_one() == 0
        assert c.execute(select(func.count()).select_from(m.image_outputs)).scalar_one() == 0
    draft = asyncio.run(ops.execute('lore_plan_image', {'target_id': 'c', 'prompt': 'A new outfit'}))
    assert draft['status'] == 'draft'
    assert draft['after']['generation_mode'] == 'undecided'
    assert not draft['after']['owner_choice_confirmed']
    with pytest.raises(AgentError):
        service.approve_draft(owner, draft['id'], draft['version'], draft['preview_hash'])
    service.complete_run(owner, session['id'], 'read-feedback', content='Review the image plan.')
    assert service.get_session(session['id'], owner)['image_contexts'] == [item]


def test_image_context_fences_stale_run_and_other_session(service, owner, monkeypatch, tmp_path):
    setup_image(service, monkeypatch, tmp_path)
    first = service.create_session(owner, 'first-feedback')
    other = service.create_session(owner, 'second-feedback')
    service.accept_run(owner, first['id'], 'current-feedback', 'Inspect images', [])
    with pytest.raises(AgentError, match='run changed'):
        service.image_context(owner, 'c', session_id=first['id'], request_id='stale-feedback')
    assert service.get_session(first['id'], owner)['image_contexts'] == []
    service.image_context(owner, 'c', session_id=first['id'], request_id='current-feedback')
    assert service.get_session(other['id'], owner)['image_contexts'] == []
    service.complete_run(owner, first['id'], 'current-feedback', content='Ready for review.')
    with pytest.raises(AgentError, match='run changed'):
        service.image_context(owner, 'c', session_id=first['id'], request_id='current-feedback')


def test_image_context_is_bounded_and_rest_read_does_not_record(service, owner, monkeypatch, tmp_path):
    setup_image(service, monkeypatch, tmp_path)
    session = service.create_session(owner, 'bounded-feedback')
    service.image_context(owner, 'c')
    assert service.get_session(session['id'], owner)['image_contexts'] == []
    with Session(service.engine) as db:
        for i in range(9):
            db.add(Character(id=f'feedback-{i}', name=f'Example {i}', persona='Fictional example'))
        db.commit()
    for i in range(9):
        service.image_context(owner, f'feedback-{i}', session_id=session['id'])
    items = service.get_session(session['id'], owner)['image_contexts']
    assert len(items) == 8 and items[0]['target_id'] == 'feedback-1'
    service.image_context(owner, 'feedback-5', session_id=session['id'])
    items = service.get_session(session['id'], owner)['image_contexts']
    assert len(items) == 8 and items[-1]['target_id'] == 'feedback-5'
    assert sum(x['target_id'] == 'feedback-5' for x in items) == 1


def test_image_context_cannot_attach_to_another_principal(service, owner, monkeypatch, tmp_path):
    setup_image(service, monkeypatch, tmp_path)
    service.update_settings(SettingsUpdate(enabled=True, mcp_enabled=True))
    client = service.create_client('Synthetic external reader', ['lore:read', 'lore:draft'])
    principal = service.principal_for_token(client['token'])
    session = service.create_session(principal, 'foreign-feedback')
    with pytest.raises(AgentError, match='another session'):
        service.image_context(owner, 'c', session_id=session['id'])
    assert service.get_session(session['id'], principal)['image_contexts'] == []


def test_image_guidance_stages_choice_controls_before_waiting():
    from app.agent.runtime import SYSTEM_PROMPT
    from app.agent.operations import TOOL_SPECS
    assert 'BEFORE asking for the owner choice' in SYSTEM_PROMPT
    assert 'no plan has been staged' in SYSTEM_PROMPT
    assert 'No choice is needed to stage that plan' in TOOL_SPECS['lore_image_context'].description
    assert 'UNAPPROVED' in TOOL_SPECS['lore_plan_image'].description
