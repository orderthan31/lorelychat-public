"""Offline native editor commits: real SQLite, revision triggers and REST owner boundary."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import select, text
from sqlmodel import Session

from app.agent import models as m
from app.agent.contracts import (AgentError, CHARACTER_FIELDS, WORLD_FIELDS,
                                OperationView, Principal, TOOL_SCHEMAS)
from app.agent.operations import AgentOperations
from app.db.models import Character, WorldSetting


MANUAL = {'avatar_url': '/uploads/manual.png', 'tts_provider': 'supertonic',
          'tts_model': 'supertonic-3', 'tts_voice_style': 'M5', 'tts_sample_text': 'Hello owner'}


def app_for(service, resources=False):
    from app.api.lore_agent import router
    app = FastAPI()
    app.state.agent_service = service
    app.include_router(router)
    if resources:
        from app.api.characters import router as characters
        from app.api.world_settings import router as worlds
        from app.db.session import get_session
        def isolated_session():
            with Session(service.engine) as session:
                yield session
        app.dependency_overrides[get_session] = isolated_session
        app.include_router(characters)
        app.include_router(worlds)
    return app


def setup_update(service, owner, kind='character'):
    model = Character if kind == 'character' else WorldSetting
    values = {'name': 'Before', 'persona': 'Navigator', 'default_model': 'preserve',
              'trait_scores': {'custom': 4}, 'tts_voice_style': 'F3'} if kind == 'character' else {'title': 'Before', 'tags': ['unchanged']}
    with Session(service.engine) as s:
        s.add(model(id='target', **values))
        s.commit()
    baseline = service.get_entity(owner, kind, 'target')['fields']
    draft = service.create_draft(owner, kind, {'description': 'Authored change'}, target_id='target')
    service.approve_draft(owner, draft['id'], draft['version'], draft['preview_hash'])
    return draft, baseline


def body(draft, baseline=None, manual=None, key='owner-save'):
    return {'request_id': key, 'expected_version': draft['version'], 'preview_hash': draft['preview_hash'],
            'manual_fields': deepcopy(MANUAL if manual is None else manual),
            'baseline_fields': deepcopy({} if baseline is None else baseline)}


def commit(service, owner, draft, payload):
    return service.commit_form(owner, draft['id'], **payload)


def entity(service, kind='character', target='target'):
    with Session(service.engine) as s:
        return s.get(Character if kind == 'character' else WorldSetting, target).model_dump()


def revision(service, kind='character', target='target'):
    table = 'characters' if kind == 'character' else 'world_settings'
    with service.engine.connect() as c:
        return c.execute(text(f'SELECT agent_revision FROM {table} WHERE id=:id'), {'id': target}).scalar_one()


def assert_binding(service, owner, draft, receipt):
    final = service.get_draft(draft['id'], owner)
    result = receipt['result']
    assert final['status'] == 'committed'
    assert result == final['result']
    assert result['draft_id'] == final['id']
    assert result['version'] == final['version']
    assert result['preview_hash'] == final['preview_hash']
    assert result['kind'] == final['kind']
    assert service.get_operation(receipt['request_id'], owner) == receipt
    with service.engine.connect() as c:
        assert '_approval' not in service._get(c, m.drafts, draft['id'])
    return final


def test_real_editor_get_revise_approve_and_atomic_form_update(service, owner):
    d, _ = setup_update(service, owner)
    with TestClient(app_for(service, resources=True)) as client:
        original = client.get('/characters/target')
        assert original.status_code == 200
        baseline = {key: original.json()[key] for key in CHARACTER_FIELDS}
        revised = client.post(f"/lore-agent/drafts/{d['id']}/revise", json={
            'expected_version': d['version'], 'fields': {'name': 'Owner revised name'}})
        assert revised.status_code == 200
        d = revised.json()
        assert client.post(f"/lore-agent/drafts/{d['id']}/approve", json={
            'expected_version': d['version'], 'preview_hash': d['preview_hash']}).status_code == 200
        result = client.post(f"/lore-agent/drafts/{d['id']}/commit-form", json=body(d, baseline))
        assert result.status_code == 200, result.text
        final = client.get(f"/lore-agent/drafts/{d['id']}").json()
        assert result.json()['result'] == final['result']
        assert result.json()['result']['version'] == final['version']
        assert result.json()['result']['preview_hash'] == final['preview_hash']
        after = client.get('/characters/target').json()
        assert after['name'] == 'Owner revised name' and after['description'] == 'Authored change'
        assert all(after[k] == v for k, v in MANUAL.items())
    raw = entity(service)
    assert raw['default_model'] == 'preserve' and raw['trait_scores'] == {'custom': 4}
    assert revision(service) == d['base_revision'] + 1


@pytest.mark.parametrize('kind,fields,manual', [
    ('character', {'name': 'New', 'persona': 'New persona'}, MANUAL),
    ('world', {'title': 'New world'}, {'thumbnail_url': '/uploads/world.png'}),
])
def test_create_form_manual_fields_and_exact_replay(service, owner, kind, fields, manual):
    d = service.create_draft(owner, kind, fields)
    service.approve_draft(owner, d['id'], d['version'], d['preview_hash'])
    payload = body(d, manual=manual)
    first = commit(service, owner, d, payload)
    assert_binding(service, owner, d, first)
    assert commit(service, owner, d, payload) == first
    row = entity(service, kind, first['result']['id'])
    assert all(row[k] == v for k, v in manual.items())
    assert revision(service, kind, first['result']['id']) == 0
    table = Character.__table__ if kind == 'character' else WorldSetting.__table__
    with service.engine.connect() as c:
        assert len(c.execute(select(table.c.id)).all()) == 1


def test_real_world_baseline_thumbnail_and_preserved_columns(service, owner):
    d, _ = setup_update(service, owner, 'world')
    with TestClient(app_for(service, resources=True)) as client:
        response = client.get('/world-settings/target')
        assert response.status_code == 200
        baseline = {key: response.json()[key] for key in WORLD_FIELDS}
        payload = body(d, baseline, {'thumbnail_url': '/uploads/scene.webp'})
        response = client.post(f"/lore-agent/drafts/{d['id']}/commit-form", json=payload)
        assert response.status_code == 200, response.text
        final = client.get('/world-settings/target').json()
        assert final['description'] == 'Authored change'
        assert final['thumbnail_url'] == '/uploads/scene.webp'
        assert final['tags'] == ['unchanged']


@pytest.mark.parametrize('manual', [
    {'api_key': 'no'}, {'default_model': 'no'}, {'name': 'no'}, {'provider_settings': {}},
    {'thumbnail_url': '/uploads/no.png'}, {'avatar_url': 123}, {'tts_provider': None},
    {'tts_model': []}, {'tts_voice_style': False}, {'avatar_url': 'a' * 2049},
    {'tts_sample_text': 'a' * 64001},
])
def test_manual_fields_strict_allowlist_normal_schema_and_budget(service, owner, manual):
    d, baseline = setup_update(service, owner)
    with TestClient(app_for(service)) as client:
        response = client.post(f"/lore-agent/drafts/{d['id']}/commit-form", json=body(d, baseline, manual))
        assert response.status_code == 422, response.text
    assert entity(service)['description'] is None
    assert service.get_operation('owner-save', owner)['status'] == 'unknown'
    assert service.get_draft(d['id'], owner)['status'] == 'approved'


def test_world_rejects_voice_and_create_rejects_nonempty_baseline(service, owner):
    d = service.create_draft(owner, 'world', {'title': 'New'})
    service.approve_draft(owner, d['id'], d['version'], d['preview_hash'])
    with pytest.raises(AgentError):
        commit(service, owner, d, body(d, manual={'tts_voice_style': 'F1'}))
    with pytest.raises(AgentError):
        commit(service, owner, d, body(d, {'title': 'New'}, {}))


@pytest.mark.parametrize('mutation', ['missing', 'extra', 'wrong_type', 'oversize'])
def test_complete_baseline_is_required_and_validated(service, owner, mutation):
    d, baseline = setup_update(service, owner)
    if mutation == 'missing': baseline.pop('appearance')
    if mutation == 'extra': baseline['avatar_url'] = '/uploads/no.png'
    if mutation == 'wrong_type': baseline['emotional_rules'] = 'wrong'
    if mutation == 'oversize': baseline['description'] = 'x' * 1048577
    with TestClient(app_for(service)) as client:
        response = client.post(f"/lore-agent/drafts/{d['id']}/commit-form", json=body(d, baseline))
        assert response.status_code == 422, response.text
    assert revision(service) == 0


@pytest.mark.parametrize('changed', ['manual_fields', 'baseline_fields', 'expected_version', 'preview_hash'])
def test_receipt_binds_every_request_field(service, owner, changed):
    d, baseline = setup_update(service, owner)
    payload = body(d, baseline)
    first = commit(service, owner, d, payload)
    assert commit(service, owner, d, payload) == first
    altered = deepcopy(payload)
    if changed == 'manual_fields': altered[changed]['tts_voice_style'] = 'F2'
    elif changed == 'baseline_fields': altered[changed]['name'] = 'different'
    elif changed == 'expected_version': altered[changed] += 1
    else: altered[changed] = '0' * 64
    with pytest.raises(AgentError, match='Request id already used'):
        commit(service, owner, d, altered)
    assert revision(service) == 1
    assert entity(service)['tts_voice_style'] == 'M5'
    # Cross-path replay may not omit the manual context.
    with pytest.raises(AgentError, match='Request id already used'):
        asyncio.run(service.commit_draft(owner, d['id'], payload['request_id'], d['version'], d['preview_hash']))


def test_new_request_on_committed_draft_never_claims_extra_manual_edit(service, owner):
    d, baseline = setup_update(service, owner)
    first = commit(service, owner, d, body(d, baseline))
    with pytest.raises(AgentError, match='already committed'):
        commit(service, owner, d, body(d, baseline, {'tts_voice_style': 'F2'}, key='new-save'))
    assert service.get_operation('new-save', owner)['status'] == 'unknown'
    assert entity(service)['tts_voice_style'] == 'M5'
    assert_binding(service, owner, d, first)


@pytest.mark.parametrize('kind,drift,manual', [
    ('character', {'avatar_url': '/uploads/concurrent.png', 'tts_voice_style': 'F2'}, {'tts_sample_text': 'Owner choice'}),
    ('world', {'thumbnail_url': '/uploads/concurrent-world.png'}, {}),
])
def test_media_only_drift_rebases_approval_atomically_and_replays_original_request(service, owner, kind, drift, manual):
    d, baseline = setup_update(service, owner, kind)
    model = Character if kind == 'character' else WorldSetting
    with Session(service.engine) as s:
        obj = s.get(model, 'target')
        for key, value in drift.items(): setattr(obj, key, value)
        s.add(obj)
        s.commit()
    before_commit = revision(service, kind)
    payload = body(d, baseline, manual)
    result = commit(service, owner, d, payload)
    final = assert_binding(service, owner, d, result)
    assert final['base_revision'] == before_commit
    assert final['version'] == d['version'] + 1
    assert final['preview_hash'] != d['preview_hash']
    assert final['before'] == d['before'] and final['after'] == d['after']
    assert commit(service, owner, d, payload) == result
    assert revision(service, kind) == before_commit + 1
    raw = entity(service, kind)
    assert raw['description'] == 'Authored change'
    assert all(raw[k] == v for k, v in {**drift, **manual}.items())


@pytest.mark.parametrize('kind,column,new_value', [
    ('character', 'description', 'Competing proposed field'),
    ('character', 'appearance', 'Competing unrelated authored field'),
    ('world', 'location', 'Competing unrelated location'),
])
def test_competing_authored_change_rejects_without_any_write(service, owner, kind, column, new_value):
    d, baseline = setup_update(service, owner, kind)
    table = Character.__table__ if kind == 'character' else WorldSetting.__table__
    with service.engine.begin() as c:
        c.execute(table.update().where(table.c.id == 'target').values(**{column: new_value}))
    previous = entity(service, kind)
    with pytest.raises(AgentError, match='Authored fields changed'):
        commit(service, owner, d, body(d, baseline, MANUAL if kind == 'character' else {'thumbnail_url': '/uploads/no.png'}))
    assert entity(service, kind) == previous
    assert service.get_draft(d['id'], owner)['status'] == 'approved'
    assert service.get_operation('owner-save', owner)['status'] == 'unknown'


def test_fresh_baseline_cannot_override_original_proposed_field_snapshot(service, owner):
    d, _ = setup_update(service, owner)
    with service.engine.begin() as c:
        c.execute(text("UPDATE characters SET description='Changed before editor opened' WHERE id='target'"))
    fresh_baseline = service.get_entity(owner, 'character', 'target')['fields']
    with pytest.raises(AgentError, match='Authored fields changed'):
        commit(service, owner, d, body(d, fresh_baseline))
    assert entity(service)['description'] == 'Changed before editor opened'


@pytest.mark.parametrize('creating', [False, True])
def test_receipt_failure_rolls_back_manual_authored_rebase_and_approval(service, owner, monkeypatch, creating):
    if creating:
        d = service.create_draft(owner, 'character', {'name': 'New', 'persona': 'New persona'})
        service.approve_draft(owner, d['id'], d['version'], d['preview_hash'])
        baseline = {}
    else:
        d, baseline = setup_update(service, owner)
        with service.engine.begin() as c:
            c.execute(text("UPDATE characters SET avatar_url='/uploads/existing.png' WHERE id='target'"))
        before_entity = entity(service)
        before_revision = revision(service)
    before_draft = service.get_draft(d['id'], owner)
    receipt = service._receipt
    def fail(*args, **kwargs):
        receipt(*args, **kwargs)  # fail even after INSERT, not only before it
        raise RuntimeError('injected receipt failure')
    monkeypatch.setattr(service, '_receipt', fail)
    with pytest.raises(RuntimeError, match='injected receipt failure'):
        commit(service, owner, d, body(d, baseline))
    assert service.get_draft(d['id'], owner) == before_draft
    assert service.get_operation('owner-save', owner)['status'] == 'unknown'
    with service.engine.connect() as c:
        assert service._get(c, m.drafts, d['id'])['_approval']['preview_hash'] == d['preview_hash']
        if creating: assert c.execute(text('SELECT count(*) FROM characters')).scalar_one() == 0
    if not creating:
        assert entity(service) == before_entity and revision(service) == before_revision
    monkeypatch.setattr(service, '_receipt', receipt)
    assert commit(service, owner, d, body(d, baseline))['status'] == 'completed'


def test_concurrent_duplicate_form_commits_write_once(service, owner):
    d, baseline = setup_update(service, owner)
    payload = body(d, baseline)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: commit(service, owner, d, payload), range(2)))
    assert results[0] == results[1]
    assert revision(service) == 1
    with service.engine.connect() as c:
        assert c.execute(text('SELECT count(*) FROM agent_operations')).scalar_one() == 1


def test_form_requires_existing_exact_approval_and_expected_preview(service, owner):
    d = service.create_draft(owner, 'character', {'name': 'New', 'persona': 'P'})
    with pytest.raises(AgentError, match='approval'):
        commit(service, owner, d, body(d))
    service.approve_draft(owner, d['id'], d['version'], d['preview_hash'])
    altered = body(d); altered['expected_version'] += 1
    with pytest.raises(AgentError, match='Preview version/hash'):
        commit(service, owner, d, altered)


def test_generic_tools_cannot_supply_manual_context_even_for_owner(service, owner):
    d, baseline = setup_update(service, owner)
    ops = AgentOperations(service, owner)
    assert 'lore_commit_form' not in TOOL_SCHEMAS
    assert 'manual_fields' not in TOOL_SCHEMAS['lore_commit_draft'].model_fields
    for extra in ({'manual_fields': MANUAL}, {'baseline_fields': baseline}, {'form_context': {}}):
        with pytest.raises(ValidationError):
            asyncio.run(ops.execute('lore_commit_draft', {
                'draft_id': d['id'], 'request_id': 'tool-save',
                'expected_version': d['version'], 'preview_hash': d['preview_hash'], **extra}))
    for kind, fields in [('character', {'name': 'N', 'persona': 'P', 'avatar_url': '/uploads/no.png'}),
                         ('world', {'title': 'N', 'thumbnail_url': '/uploads/no.png'})]:
        with pytest.raises(AgentError, match='Unsupported'):
            asyncio.run(ops.execute('lore_stage_' + kind, {'fields': fields}))
    with pytest.raises(AgentError, match='Unsupported'):
        asyncio.run(ops.execute('lore_revise_draft', {'draft_id': d['id'], 'expected_version': d['version'], 'fields': {'tts_voice_style': 'F2'}}))
    with TestClient(app_for(service)) as client:
        assert client.post(f"/lore-agent/drafts/{d['id']}/commit", json=body(d, baseline)).status_code == 422
        assert client.post('/lore-agent/tools/lore_commit_form', json={}).status_code == 404
    assert entity(service)['tts_voice_style'] == 'F3'


def test_generic_commit_still_refuses_media_revision_drift(service, owner):
    d, _ = setup_update(service, owner)
    with service.engine.begin() as c:
        c.execute(text("UPDATE characters SET tts_voice_style='F2' WHERE id='target'"))
    with pytest.raises(AgentError, match='Entity changed'):
        asyncio.run(service.commit_draft(owner, d['id'], 'generic', d['version'], d['preview_hash']))
    assert service.get_draft(d['id'], owner)['status'] == 'conflict'
    assert entity(service)['description'] is None


def test_owner_route_denies_bearer_foreign_origin_readonly_and_wrong_principal(service, owner):
    from app.api.lore_agent import owner as owner_dependency
    d, baseline = setup_update(service, owner)
    service.update_settings({'enabled': True, 'mcp_enabled': True})
    client_record = service.create_client('readonly', ['lore:read'])
    reader = service.principal_for_token(client_record['token'])
    app = app_for(service)
    payload = body(d, baseline)
    path = f"/lore-agent/drafts/{d['id']}/commit-form"
    with TestClient(app) as client:
        for headers in ({'Authorization': 'Bearer ' + client_record['token']}, {'Origin': 'https://foreign.invalid'}):
            assert client.post(path, json=payload, headers=headers).status_code == 403
        for principal in (reader, Principal('wrong-owner', frozenset(), True), Principal('owner', frozenset(), False)):
            app.dependency_overrides[owner_dependency] = lambda p=principal: p
            assert client.post(path, json=payload).status_code == 403
            with pytest.raises(AgentError) as error:
                commit(service, principal, d, payload)
            assert error.value.status_code == 403
        app.dependency_overrides.clear()
    assert service.get_operation(payload['request_id'], owner)['status'] == 'unknown'
    assert entity(service)['description'] is None


@pytest.mark.parametrize('kind,manual', [
    ('character', {'avatar_url': None, 'tts_sample_text': None}),
    ('world', {'thumbnail_url': None}),
])
def test_nullable_manual_clear_uses_normal_editor_semantics(service, owner, kind, manual):
    d, baseline = setup_update(service, owner, kind)
    table = Character.__table__ if kind == 'character' else WorldSetting.__table__
    with service.engine.begin() as c:
        c.execute(table.update().where(table.c.id == 'target').values(**{
            key: '/uploads/old.png' if key.endswith('_url') else 'Old sample' for key in manual}))
    result = commit(service, owner, d, body(d, baseline, manual))
    assert_binding(service, owner, d, result)
    row = entity(service, kind)
    assert all(row[key] is None for key in manual)


def test_form_route_rejects_unknown_envelope_and_image_registration(service, owner):
    d, baseline = setup_update(service, owner)
    with TestClient(app_for(service)) as client:
        payload = {**body(d, baseline), 'provider_account_id': 'forbidden'}
        assert client.post(f"/lore-agent/drafts/{d['id']}/commit-form", json=payload).status_code == 422
    with service._write() as c:
        image = service._new_draft(c, owner, 'image_registration', {'target_kind': 'character'}, target_id='target')
    service.approve_draft(owner, image['id'], image['version'], image['preview_hash'])
    with pytest.raises(AgentError, match='character/world drafts only'):
        commit(service, owner, image, body(image, baseline))
    assert service.get_operation('owner-save', owner)['status'] == 'unknown'


def test_unknown_request_and_progress_contract_historical_compatibility(service, owner):
    unknown = service.get_operation('never-dispatched', owner)
    assert unknown['status'] == 'unknown'
    assert OperationView.model_validate(unknown).progress is None
    session = service.create_session(owner, 'session-form-progress')
    service.accept_run(owner, session['id'], 'progress-run', 'Offline', [])
    with service._write() as c:
        op = service._get(c, m.operations, 'progress-run')
        op['progress'] = {'stage': 'draft', 'sequence': 2, 'round': 1}
        service._put(c, m.operations, 'progress-run', op)
    with TestClient(app_for(service)) as client:
        response = client.get('/lore-agent/operations/progress-run')
        assert response.status_code == 200
        assert response.json()['progress'] == {'stage': 'draft', 'sequence': 2, 'round': 1}
    for progress in ({'stage': 'secret', 'sequence': 1}, {'stage': 'read', 'sequence': 0},
                     {'stage': 'model', 'sequence': True}, {'stage': 'model', 'sequence': 1, 'round': 0},
                     {'stage': 'search', 'sequence': 1, 'args': {'query': 'secret'}}):
        with pytest.raises(ValidationError):
            OperationView.model_validate({**unknown, 'progress': progress})
