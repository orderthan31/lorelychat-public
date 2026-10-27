import json
import pytest
from app.services.chat_command_contract import command_response_format, parse_command_payload
from app.services.chat_command_service import command_response_instruction


def test_explicit_contract_crud_and_seed_preservation(client):
    body = {'name': 'contract-test', 'prompt': 'authored', 'postprocess_block_types': ['comments', 'note', 'table']}
    response = client.post('/chat-commands', json=body)
    assert response.status_code == 200
    row = response.json()
    assert row['postprocess_block_types'] == body['postprocess_block_types']
    assert client.patch('/chat-commands/' + row['id'], json={'postprocess_block_types': []}).json()['postprocess_block_types'] == []
    assert client.get('/chat-commands/' + row['id']).json()['postprocess_block_types'] == []
    assert client.patch('/chat-commands/' + row['id'], json={'postprocess_block_types': ['unknown']}).status_code == 422
    builtin = next(x for x in client.get('/chat-commands').json() if x['name'] == '방송마지막')
    changes = {'generation_prompt': 'authored generation', 'postprocess_prompt': 'authored post', 'postprocess_block_types': [], 'postprocess_context_options': []}
    assert client.patch('/chat-commands/' + builtin['id'], json=changes).status_code == 200
    listed = next(x for x in client.get('/chat-commands').json() if x['id'] == builtin['id'])
    assert all(listed[k] == v for k, v in changes.items())


def test_specialized_schema_examples_and_validation():
    kinds = ['comments', 'note', 'table']
    schema = command_response_format(1, overlay=True, block_types=kinds)
    wire = json.dumps(schema)
    instructions = command_response_instruction(1, overlay_mode=True, block_types=kinds)
    assert 'checklist' not in wire + instructions
    for kind in kinds:
        assert kind in wire and kind in instructions
    payload = {'messages': [{'content': '', 'blocks': [{'type': 'note', 'text': 'status'}]}]}
    assert parse_command_payload(json.dumps(payload), 1, overlay=True, block_types=kinds)
    assert parse_command_payload(json.dumps(payload), 1, overlay=True, block_types=['comments']) is None
    assert parse_command_payload(json.dumps(payload), 1, overlay=True, block_types=[])
    payload['messages'][0]['blocks'][0] = {'type': 'comments', 'text': 'not a comments shape'}
    assert parse_command_payload(json.dumps(payload), 1, overlay=True, block_types=kinds) is None
    assert parse_command_payload('[' * 2000, 1, block_types=kinds) is None


def test_combined_plan_and_panel_isolation(monkeypatch):
    from app.engine.command_overlay import build_plan, split_response
    from app.core.config import get_settings
    from app.db.models import ChatCommand
    monkeypatch.setattr(get_settings(), 'chat_command_postprocess_mode', 'combined_overlay')
    cmd = ChatCommand(id='one', name='one', prompt='panel', postprocess_target='last_bubble', postprocess_block_types=['note'], postprocess_context_options=['scene', 'world', 'turn_messages'])
    plan = build_plan([cmd], 'tagged_text')
    assert list(plan.eligible) == ['K1']
    primary, panels, outcomes = split_response('<m c="C1">body `<panels>` literal</m>\n<panels><panel k="K1"><note title="state">ready</note></panel></panels>', 'tagged_text', plan)
    assert primary == '<m c="C1">body `<panels>` literal</m>\n'
    assert panels['one'][0]['blocks'][0]['text'] == 'ready'
    assert outcomes['one']['status'] == 'combined_success'
    _, panels, outcomes = split_response('<m c="C1">body</m><panels><panel k="K1"><note>ready</note></panel><panel k="K1"><note>duplicate</note></panel></panels>', 'tagged_text', plan)
    assert panels == {} and outcomes['one']['reason'] == 'duplicate_alias'
    cmd.postprocess_context_options = ['relationship_memory']
    assert build_plan([cmd], 'tagged_text').outcomes['one']['reason'] == 'unsupported_context'
