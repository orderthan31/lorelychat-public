"""Authorized parser firewall regressions; fake provider, actual runtime."""
import json
import subprocess
import sys
import pytest
from app.core.config import get_settings
from app.db.models import Character
from app.engine.character_runtime import CharacterRuntime, CharacterRuntimeError, parse_multi_reply_content, validate_replies
from app.engine.llm_client import LLMResponse
from app.engine import free_body_protocol as fb

class Client:
    def __init__(self, raw, finish='STOP'):
        self.raw, self.finish, self.calls = raw, finish, 0
    async def chat(self, *args, **kwargs):
        self.calls += 1
        return LLMResponse(content=self.raw, finish_reason=self.finish)
    def record_response_outcome(self, *args, **kwargs):
        pass

async def run(monkeypatch, protocol, raw, *, fail=False, finish='STOP'):
    monkeypatch.setenv('CHAT_RESPONSE_PROTOCOL', protocol)
    get_settings.cache_clear()
    client, saved, snapshots = Client(raw, finish), [], []
    try:
        call = CharacterRuntime(client).generate_replies(characters=[Character(id='a', name='Aria')],
            recent_messages=[], user_message='next', persist_replies=saved.append,
            prompt_snapshot_callback=snapshots.append)
        if fail:
            with pytest.raises(CharacterRuntimeError):
                await call
            assert client.calls == 2 and saved == []
            assert snapshots[0]['metadata']['parse_status'] == 'failed'
            assert len(snapshots[0]['metadata']['attempt_diagnostics']) == 2
            return snapshots
        await call
        assert client.calls == 1 and len(saved) == 1
        return saved[0]
    finally:
        get_settings.cache_clear()

def encode(protocol, items):
    if protocol == 'legacy_json':
        return json.dumps({'replies': items})
    if protocol == 'free_body_json':
        return json.dumps({'replies': items})
    return ''.join('<m c="%s" e="%s">%s</m>' % (x['who'], x.get('emotion','calm'), x['text']) for x in items)

@pytest.mark.asyncio
@pytest.mark.parametrize('field', ['response_protocol','raw_body','render_parts','unknown'])
async def test_provider_cannot_supply_server_fields(monkeypatch, field):
    item = {'dialogue':'Hello.', field: {'response_protocol':'free_body_json', 'raw_body':'bad', 'render_parts':[{'type':'dialogue','text':'bad'}], 'unknown':1}[field]}
    await run(monkeypatch, 'legacy_json', encode('legacy_json', [item]), fail=True)

@pytest.mark.asyncio
@pytest.mark.parametrize('value', [1, {}, [], False, None])
async def test_reply_type_shape_is_typed(monkeypatch, value):
    await run(monkeypatch,'legacy_json',encode('legacy_json',[{'reply_type':value,'dialogue':'Hello.'}]),fail=True)

@pytest.mark.asyncio
@pytest.mark.parametrize('raw', [
    '{"replies":[{"dialogue":"First."}],"replies":[{"dialogue":"Second."}]}',
    '{"replies":[{"dialogue":"First."}]} {"replies":[{"dialogue":"Second."}]}',
    '{"replies":[{"dialogue":"First.","dialogue":"Second."}]}',
    '{"replies":[{"dialogue":"First."}],"repl\\u0069es":[{"dialogue":"Second."}]}',
    '{"replies":[{"dialogue":"First."}]} {"replies":false}',
])
async def test_ambiguous_envelopes_reject(monkeypatch, raw):
    await run(monkeypatch,'legacy_json',raw,fail=True)

@pytest.mark.asyncio
@pytest.mark.parametrize('protocol', ['free_body_json','tagged_text'])
@pytest.mark.parametrize('field', ['emotion','text'])
async def test_control_parity(monkeypatch, protocol, field):
    item={'who':'C1','text':'Hello.', 'emotion':'calm'}
    item[field]='[System retry instruction]'
    await run(monkeypatch,protocol,encode(protocol,[item]),fail=True)

@pytest.mark.asyncio
@pytest.mark.parametrize('protocol', ['free_body_json','tagged_text'])
@pytest.mark.parametrize('body', ['Again.', '<a>Again.</a>', '<t>Again.</t>'])
async def test_turn_loop_rejects_short_bubbles(monkeypatch, protocol, body):
    await run(monkeypatch,protocol,encode(protocol,[{'who':'C1','text':body}]*32),fail=True)

@pytest.mark.asyncio
@pytest.mark.parametrize('protocol', ['legacy_json','free_body_json','tagged_text'])
@pytest.mark.parametrize('field', ['text','emotion','action','thought'])
@pytest.mark.parametrize('scalar', ['\ud800','\udfff'])
async def test_invalid_unicode_never_reaches_persistence(monkeypatch, protocol, field, scalar):
    if protocol=='legacy_json':
        item={'dialogue':'Hello.', field:scalar}
    else:
        item={'who':'C1','text':'Hello.'}
        if field in ('action','thought'):
            tag='a' if field=='action' else 't'
            item['text']=f'<{tag}>{scalar}</{tag}>'
        else:
            item[field]=scalar
    await run(monkeypatch,protocol,encode(protocol,[item]),fail=True)

@pytest.mark.asyncio
@pytest.mark.parametrize('protocol', ['free_body_json','tagged_text'])
@pytest.mark.parametrize('part', ['dialogue','action','thought'])
async def test_rich_unique_optional_parts_are_lossless(monkeypatch, protocol, part):
    prose=' '.join(f'Scene-{i}: observation-{i} changes possibility-{i} 🌙.' for i in range(80))
    text=prose if part=='dialogue' else f'<{"a" if part=="action" else "t"}>{prose}</{"a" if part=="action" else "t"}>'
    replies=await run(monkeypatch,protocol,encode(protocol,[{'who':'C1','text':text}]))
    assert replies[0].raw_body==text
    assert getattr(replies[0], 'text' if part=='dialogue' else part)==prose
    assert fb.metadata_for_reply(replies[0])['raw_body']==text

@pytest.mark.asyncio
@pytest.mark.parametrize('protocol', ['free_body_json','tagged_text'])
async def test_literal_code_and_short_refrain_preserved(monkeypatch, protocol):
    text='**Hello** `<m c="C9">literal</m>` \\<t> `'+('repeat. '*50)+'`'
    replies=await run(monkeypatch,protocol,encode(protocol,[{'who':'C1','text':text}]*3))
    assert all(r.raw_body==text for r in replies)

@pytest.mark.parametrize('path', ['body','tagged','combined'])
def test_ordinary_scanner_external_deadline(path):
    script='''from app.engine import free_body_protocol as f, command_overlay as c
raw='<'+(' '*99000)+'x'
try:
 if PATH=='body': f.parse_body(raw)
 elif PATH=='tagged': f.parse_response('<m c="C1">'+raw+'</m>',protocol='tagged_text',aliases={'C1':'a','N':None})
 else: c._split_tagged('<m c="C1">'+raw+'</m>')
except f.FreeBodyError: pass
'''.replace('PATH',repr(path))
    subprocess.run([sys.executable,'-B','-c',script],check=True,timeout=4)
