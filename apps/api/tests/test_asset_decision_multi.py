"""Whole-turn coverage and anchored repeat contracts, synthetic only."""
import asyncio
import json
from datetime import datetime, timedelta

import pytest
from sqlmodel import Session
from app.db.models import CharacterAsset, Message, MessageAsset, SceneState
from app.services import asset_decision_service as service, decision_transport as transport
from test_asset_decision_service import fixture, run


@pytest.mark.parametrize('required', ['pool', '풀사이드', '수영장 옆'])
def test_explicit_required_policy_matches_normalized_korean_scene(fixture, required):
    session, _, calls = fixture
    asset = session.get(CharacterAsset, 'a1')
    asset.metadata_ = {'asset_selection_v2': {
        'scope': 'scene_required', 'required_scene_tags': [required], 'preferred_scene_tags': [],
    }}
    session.get(SceneState, 'room').location = '풀사이드에서 쉬는 중'
    session.commit()
    assert run(fixture) == [('m1', 'a1')]
    assert len(calls) == 1
    assert session.get(CharacterAsset, 'a1').metadata_['asset_selection_v2']['required_scene_tags'] == [required]


def test_all_32_questions_all_27_candidates_shared_catalog(fixture):
    session, _, calls = fixture
    for i in range(26):
        session.add(CharacterAsset(id=f'a{i+2}', character_id='char', label='Unique caption',
            description=f'caption-only-once-{i:02}', image_url='x'))
    ids = ['m1']
    for i in range(31):
        mid = f'target{i}'
        ids.append(mid)
        session.add(Message(id=mid, conversation_id='room', speaker_type='character', speaker_id='char', content='Hello'))
    session.commit()
    result = run(fixture, ids)
    assert len(calls) == 1
    route, context, questions = calls[0]
    assert [q.id for q in questions] == ids
    assert all(len(q.candidates) == 28 for q in questions)
    assert len(context['asset_catalog']) == 27
    _, _, _, _, wire = transport._request(route, context, questions)
    assert wire.count(b'caption-only-once-00') == 1
    assert result == [('m1', 'a1')]  # deterministic first duplicate wins; no legacy tail


def test_complete_context_narration_manual_and_ordered_parts(fixture):
    session, _, calls = fixture
    first = session.get(Message, 'm1')
    first.generation_job_id = 'job'
    first.reply_index = 0
    first.content = 'aggregate-duplicate'
    first.metadata_ = {'render_parts': [{'type': 'action', 'text': 'opens gate'},
        {'type': 'thought', 'text': 'private character thought'}, {'type': 'dialogue', 'text': 'come in'}],
        'raw_body': 'raw-duplicate', 'irrelevant': 'secret-metadata'}
    session.add_all([
        Message(id='narration', conversation_id='room', generation_job_id='job', reply_index=1,
            speaker_type='narration', speaker_id='', content='late-scene-evidence-' + 'z'*4000),
        Message(id='manual-bubble', conversation_id='room', generation_job_id='job', reply_index=2,
            speaker_type='character', speaker_id='other', content='manual scene evidence'),
        MessageAsset(id='manual', message_id='manual-bubble', asset_id='manual-asset'),
    ])
    session.commit()
    run(fixture)
    context = calls[0][1]
    assert [m['id'] for m in context['messages']] == ['m1', 'narration', 'manual-bubble']
    assert context['messages'][0]['speaker_id'] == 'char'
    assert context['messages'][0]['reply_index'] == 0
    text = json.dumps(context)
    assert 'private character thought' in text and 'z'*4000 in text
    assert 'aggregate-duplicate' not in text and 'raw-duplicate' not in text and 'secret-metadata' not in text
    assert context['current_attachments'] == [{'message_id': 'manual-bubble', 'asset_id': 'manual-asset'}]


def test_oversize_is_explicit_no_call_no_legacy(fixture):
    session, _, calls = fixture
    session.get(Message, 'm1').content = '한'*100000
    session.commit()
    diagnostics = {}
    result = asyncio.run(service.select_turn_assets(session, conversation_id='room', message_ids=['m1'],
        model_option_key='decision', diagnostics=diagnostics))
    assert result == [] and calls == []
    assert diagnostics['outcome'] == 'request_budget_exceeded'
    assert diagnostics['request_bytes'] > transport._MAX_TEXT_BYTES


def prior(session, *, linked=True):
    base = datetime(2026, 1, 1)
    session.get(Message, 'm1').created_at = base + timedelta(days=1)
    session.add(Message(id='prior', conversation_id='room', speaker_type='character', speaker_id='char',
        content='not consumed prose', created_at=base))
    if linked:
        session.add(MessageAsset(id='prior-link', message_id='prior', asset_id='a1'))
    session.commit()


def test_repeat_anchored_before_long_current_turn(fixture):
    session, _, calls = fixture
    prior(session)
    ids = ['m1']
    for i in range(20):
        mid = f'current{i}'
        ids.append(mid)
        session.add(Message(id=mid, conversation_id='room', speaker_type='character', speaker_id='char', content='Current'))
    session.commit()
    assert run(fixture, ids) == []
    assert calls == []


def test_prior_attachment_race_invalidates(fixture, monkeypatch):
    session, engine, _ = fixture
    prior(session, linked=False)
    async def decide(*args, **kwargs):
        with Session(engine) as writer:
            writer.add(MessageAsset(id='raced', message_id='prior', asset_id='a1'))
            writer.commit()
        return {'m1': 'a1'}
    monkeypatch.setattr(transport, 'decide_assets', decide)
    assert run(fixture) == []


def test_manual_on_nontarget_context_reserves_asset(fixture):
    session, _, calls = fixture
    session.get(Message, 'm1').generation_job_id = 'job'
    session.add_all([Message(id='manual', conversation_id='room', generation_job_id='job', reply_index=1,
        speaker_type='character', speaker_id='char', content='Already shown'),
        MessageAsset(id='link', message_id='manual', asset_id='a1')])
    session.commit()
    assert run(fixture) == [] and calls == []


def test_reservation_callback_after_admission_before_send(fixture, monkeypatch):
    session, _, calls = fixture
    events = []
    def reserve():
        assert not session.in_transaction()
        events.append('reserve')
        return False
    diagnostics = {}
    result = asyncio.run(service.select_turn_assets(session, conversation_id='room', message_ids=['m1'],
        model_option_key='decision', reserve_send=reserve, diagnostics=diagnostics))
    assert events == ['reserve'] and calls == [] and result == []
    assert diagnostics['outcome'] == 'send_recovery_abstention'


@pytest.mark.parametrize('preparation', ['images', 'empty', 'error'])
def test_vision_fair_subset_does_not_limit_textual_catalog(fixture, monkeypatch, preparation):
    from app.db.models import ModelOption
    from app.services import decision_asset_media as media
    session, _, _ = fixture
    option = session.get(ModelOption, 'option')
    option.input_modalities = ['text', 'image']
    option.image_mime_types = ['image/png']
    for c in ['char', 'other']:
        for i in range(12):
            session.add(CharacterAsset(id=f'{c}_{i:02}', character_id=c, label='Caption', image_url='x'))
    session.add(Message(id='m2', conversation_id='room', speaker_type='character', speaker_id='other', content='Other'))
    session.commit()
    prepared = []
    async def prepare(route, candidates):
        assert not session.in_transaction()
        assert len(candidates) == 8
        prepared.extend(candidates)
        if preparation == 'error':
            raise RuntimeError('PRIVATE_PREPARATION_ERROR')
        if preparation == 'empty':
            return []
        png = b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR' + b'0'*17
        return [transport.DecisionImage(id=c['id'], data=png, mime_type='image/png') for c in candidates]
    calls = []
    async def decide(route, **kwargs):
        calls.append(kwargs)
        questions = kwargs['questions']
        assert len(questions[0].candidates) == 14
        assert len(questions[1].candidates) == 13
        assert all(a.startswith('other_') or a == 'none' for a in questions[1].candidates)
        assert kwargs['context']['visual_asset_ids'] == ([c['id'] for c in prepared] if preparation == 'images' else [])
        assert bool(kwargs.get('images')) == (preparation == 'images')
        return {q.id: None for q in questions}
    monkeypatch.setattr(media, 'prepare_images', prepare)
    monkeypatch.setattr(transport, 'decide_assets', decide)
    assert run(fixture, ['m1', 'm2']) == []
    assert len(calls) == 1
    assert sum(c['id'].startswith('other_') for c in prepared) == 4


def test_narration_source_edit_invalidates_entire_turn(fixture, monkeypatch):
    session, engine, _ = fixture
    session.get(Message, 'm1').generation_job_id = 'job'
    session.add(Message(id='n', conversation_id='room', generation_job_id='job', reply_index=1,
        speaker_type='narration', speaker_id='', content='Current scene'))
    session.commit()
    async def decide(*args, **kwargs):
        with Session(engine) as writer:
            writer.get(Message, 'n').content = 'Changed scene'
            writer.commit()
        return {'m1': 'a1'}
    monkeypatch.setattr(transport, 'decide_assets', decide)
    assert run(fixture) == []


def test_later_korean_scene_evidence_allows_required_outfit(fixture):
    session, _, calls = fixture
    asset = session.get(CharacterAsset, 'a1')
    asset.metadata_ = {}
    asset.outfit_tags = ['비키니']
    session.get(Message, 'm1').generation_job_id = 'job'
    session.add(Message(id='n', conversation_id='room', generation_job_id='job', reply_index=1,
        speaker_type='narration', speaker_id='', content='해변에서 파도를 바라본다.'))
    session.commit()
    assert run(fixture) == [('m1', 'a1')]
    assert len(calls) == 1


def test_prior_facts_shared_once_and_audio_does_not_hide_repeat(fixture):
    session, _, calls = fixture
    prior(session)
    base = datetime(2026, 1, 1)
    session.add(CharacterAsset(id='audio', character_id='char', label='Audio', asset_type='audio', image_url='x'))
    for i in range(10):
        session.add(Message(id=f'audio_message{i}', conversation_id='room', speaker_type='character', speaker_id='char',
            content='ignored', created_at=base+timedelta(seconds=i+1)))
        session.add(MessageAsset(id=f'audio_link{i}', message_id=f'audio_message{i}', asset_id='audio'))
    session.add(CharacterAsset(id='alternative', character_id='char', label='Another', image_url='x'))
    session.commit()
    assert run(fixture) == [('m1', 'alternative')]
    context = calls[0][1]
    assert context['prior_images'] == [{'message_id': 'prior', 'speaker_id': 'char', 'asset_id': 'a1'}]
    assert 'a1' not in calls[0][2][0].candidates
    assert context['asset_catalog']['a1']['label'] == 'Reflective'


def test_later_task_finishes_first_cannot_commit_reverse_repeat(fixture, monkeypatch):
    session, engine, _ = fixture
    stamp = session.get(Message, 'm1').created_at + timedelta(days=1)
    async def decide(*args, **kwargs):
        with Session(engine) as writer:
            writer.add(Message(id='later', conversation_id='room', speaker_type='character', speaker_id='char',
                content='Later turn finished first', created_at=stamp))
            writer.add(MessageAsset(id='later-link', message_id='later', asset_id='a1'))
            writer.commit()
        return {'m1': 'a1'}
    monkeypatch.setattr(transport, 'decide_assets', decide)
    assert run(fixture) == []
