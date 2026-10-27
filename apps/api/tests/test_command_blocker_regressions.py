"""Bounded regressions for auxiliary ownership and lossless standalone inputs."""
import json
from datetime import datetime, timedelta

import pytest
from app.core.config import get_settings
from app.db.models import Character, Conversation, Message
from app.engine import command_overlay as co, prompts
from app.engine.character_runtime import CharacterRuntime, CharacterRuntimeError, generation_control_reserve_tokens
from app.engine.llm_client import LLMResponse
from app.services import chat_command_service as cs, context_preview_service as preview
from test_combined_command_runtime import Client, command, mode


@pytest.mark.asyncio
@pytest.mark.parametrize('suffix', [
    '<panel k="K1"><note>Ready</note></panel><m c="C1">SECOND PRIMARY</m></panels>',
    '<panel k="K1"><note>Ready</note></panel><m c="C1">SECOND PRIMARY</m>',
    '<panel k="K1"><note><m c="C1">SECOND PRIMARY</m></note></panel></panels>',
    '<panel k="K1"><note>Ready</note></panel></m>',
])
async def test_auxiliary_primary_is_atomic_failure(mode, suffix):
    mode('tagged_text')
    raw = '<m c="C1">First primary.</m><panels>' + suffix
    provider = Client([LLMResponse(content=raw)] * 2)
    persisted, snapshots = [], []
    with pytest.raises(CharacterRuntimeError):
        await CharacterRuntime(provider).generate_replies(
            characters=[Character(id='a', name='Aria')], recent_messages=[], user_message='source',
            command_overlay_plan=co.build_plan([command()]), persist_replies=persisted.append,
            prompt_snapshot_callback=snapshots.append)
    assert len(provider.calls) == 2 and persisted == []
    assert snapshots[0]['metadata']['parse_status'] == 'failed'
    assert snapshots[0]['response_preview'] == raw


@pytest.mark.parametrize('literal', ['`<m c="C1">example</m>`', r'\<m c="C1">example\</m>', '&lt;m&gt;example&lt;/m&gt;'])
def test_auxiliary_primary_literals_do_not_regenerate(mode, literal):
    mode('tagged_text')
    primary, _, _ = co.split_response('<m c="C1">Safe.</m><panels><panel k="K1"><note>' + literal + '</note></panel></panels>', 'tagged_text', co.build_plan([command()]))
    assert primary == '<m c="C1">Safe.</m>'


@pytest.mark.parametrize('context,eligible', [(['turn_messages'], True), (['recent_messages'], False)])
def test_actual_automatic_preview_reserves_active_plan(client, session, monkeypatch, mode, context, eligible):
    mode('free_body_json')
    monkeypatch.setenv('CONTEXT_MANAGEMENT_MODE', 'automatic')
    get_settings.cache_clear()
    actor = client.post('/characters', json={'name': 'Aria', 'persona': 'Synthetic.'}).json()
    room = client.post('/conversations', json={'mode': 'user_character', 'participants': [{'type': 'character', 'id': actor['id'], 'role': 'primary'}]}).json()['id']
    cmd = command(display_name='Synthetic command')
    cmd.postprocess_context_options = context
    session.add(cmd); session.commit()
    cs.set_active_command_ids(session, session.get(Conversation, room), [cmd.id])
    session.add(Message(id='source', conversation_id=room, speaker_type='user', speaker_id='u', content='Next.'))
    session.commit()
    captured = []
    def reserve(**kwargs):
        captured.append(kwargs)
        return generation_control_reserve_tokens(**kwargs)
    monkeypatch.setattr(preview, 'generation_control_reserve_tokens', reserve)
    response = client.get(f'/conversations/{room}/compression-preview')
    assert response.status_code == 200, response.text
    assert len(captured) == 1
    actual = captured[0]
    plan = actual['command_overlay_plan']
    assert bool(plan.eligible) is eligible
    assert actual['room_cast_roles'] == {actor['id']: 'primary'}
    expected = co.build_plan([cmd])
    assert plan.outcomes == expected.outcomes
    baseline = {k: v for k, v in actual.items() if k != 'command_overlay_plan'}
    assert generation_control_reserve_tokens(**actual) == generation_control_reserve_tokens(**baseline, command_overlay_plan=expected)
    if eligible:
        assert generation_control_reserve_tokens(**actual) > generation_control_reserve_tokens(**baseline)


@pytest.mark.asyncio
@pytest.mark.parametrize('speaker', ['user', 'system'])
@pytest.mark.parametrize('target', ['first_bubble', 'last_bubble', 'all_bubbles'])
async def test_standalone_full_public_text_once(session, speaker, target):
    source_text = ' '.join(f'directive-{i}' for i in range(140)) + ' SOURCE_TAIL'
    action = ' '.join(f'action-{i}' for i in range(100)) + ' ACTION_TAIL'
    dialogue = ' '.join(f'dialogue-{i}' for i in range(100)) + ' DIALOGUE_TAIL'
    source = Message(id='source', conversation_id='room', speaker_type=speaker, speaker_id='u', content=source_text)
    reply = Message(id='reply', conversation_id='room', speaker_type='character', speaker_id='a', content=dialogue, action=action, thought='PRIVATE_SECRET')
    cmd = command(target=target)
    cmd.postprocess_context_options = ['turn_messages', 'recent_messages']
    provider = Client([LLMResponse(content=json.dumps({'messages': [{'content': dialogue if target == 'all_bubbles' else '', 'blocks': [{'type': 'note', 'text': 'Ready'}]}]}))])
    result = await cs.apply_chat_command_postprocess(session=session, command=cmd, conversation_id='room', source_message=source, generated_messages=[reply], llm_client=provider, commit=False)
    wire = '\n'.join(m['content'] for m in provider.calls[0]['messages'])
    assert len(source_text) > 1200
    for text in [source_text, action, dialogue]:
        assert wire.count(text) == 1
    assert 'PRIVATE_SECRET' not in wire
    assert len(result) == (1 if target == 'all_bubbles' else 2)
    if target != 'all_bubbles':
        assert result[0 if target == 'first_bubble' else -1].metadata_['command_overlay']
    # General history remains bounded, independently of the standalone path.
    assert 'SOURCE_TAIL' not in prompts.format_message_for_context(source, include_thought=False)


def test_selected_turn_full_text_and_recent_identity_no_backfill(session):
    now = datetime(2025, 1, 1)
    rows = [Message(id=f'm{i:02}', conversation_id='room', speaker_type='character', speaker_id='a', content='SAME_PUBLIC_TEXT' if i >= 10 else f'OLDER_{i}', created_at=now + timedelta(seconds=i)) for i in range(12)]
    rows[8].content = 'SAME_PUBLIC_TEXT'
    rows[-1].action = 'full action ' * 100 + 'UNSELECTED_ACTION_TAIL'
    rows[-1].content = 'full dialogue ' * 100 + 'UNSELECTED_DIALOGUE_TAIL'
    rows[-1].thought = 'PRIVATE_SECRET'
    session.add_all(rows); session.commit()
    source = rows[9]
    cmd = command()
    cmd.postprocess_context_options = ['turn_messages', 'recent_messages']
    text = cs.build_command_postprocess_context(session, cmd, 'room', source, [rows[11]], represented_ids={source.id})
    assert rows[11].action in text and rows[11].content in text
    assert 'PRIVATE_SECRET' not in text
    # Selected window is m02..m11; removing identities does not refill m00/m01.
    assert 'OLDER_0\n' not in text and 'OLDER_1\n' not in text
    assert text.count('SAME_PUBLIC_TEXT') == 2
    identity = cs._build_recent_messages_context(session, 'room', {}, exclude_ids={rows[10].id, rows[11].id})
    assert '\n'.join(identity).count('SAME_PUBLIC_TEXT') == 1
    assert 'OLDER_0' not in '\n'.join(identity) and 'OLDER_1\n' not in '\n'.join(identity)
