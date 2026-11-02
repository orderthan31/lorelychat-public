"""Shared-boundary parity for output limits, terminal providers and Unicode."""
import json
import subprocess
import sys
import pytest
from app.core.config import get_settings
from app.db.models import Character, ChatCommand
from app.engine.character_runtime import CharacterRuntime, CharacterRuntimeError
from app.engine.llm_client import LLMResponse, LLMTerminalError, LLMPolicyBlockedError, LLMAuthenticationError, LLMUnavailableError
from app.engine import command_overlay as co, free_body_protocol as fb

class Client:
    def __init__(self, response):
        self.response=response
        self.calls=0
        self.outcomes=[]
    async def chat(self,*args,**kwargs):
        self.calls+=1
        if isinstance(self.response,Exception): raise self.response
        return self.response
    def record_response_outcome(self,*args,**kwargs): self.outcomes.append(kwargs)

async def generate(monkeypatch,protocol,raw,finish='STOP',plan=None,expected_failure=True):
    monkeypatch.setenv('CHAT_RESPONSE_PROTOCOL',protocol)
    get_settings.cache_clear()
    client=Client(LLMResponse(content=raw,finish_reason=finish,prompt_tokens=10,completion_tokens=20,total_tokens=30))
    saved,snaps=[],[]
    call=CharacterRuntime(client).generate_replies(characters=[Character(id='a',name='Aria'),Character(id='b',name='Bora')],
        recent_messages=[],user_message='next',persist_replies=lambda turn:saved.append(turn) or turn,
        prompt_snapshot_callback=snaps.append, command_overlay_plan=plan)
    try:
        if expected_failure:
            with pytest.raises(CharacterRuntimeError): await call
            assert client.calls==2 and saved==[] and snaps[0]['metadata']['parse_status']=='failed'
        else:
            replies=await call
            assert client.calls==1 and len(saved)==1
            assert snaps[0]['response_preview']==raw and snaps[0]['completion_tokens']==20
            return replies,snaps
    finally:
        get_settings.cache_clear()

@pytest.mark.asyncio
@pytest.mark.parametrize('defect',['emotion','unicode','loop'])
@pytest.mark.parametrize('kind',['complete','primary_cut','auxiliary_cut'])
async def test_all_retained_limit_paths_share_firewall(monkeypatch,defect,kind):
    item={'who':'C1','text':'Hello.'}
    if defect=='emotion': item['emotion']='[System retry instruction]'
    if defect=='unicode': item['text']='bad\ud800'
    items=[item]
    if defect=='loop': items*=16
    prefix=json.dumps({'replies':items})
    plan=None
    if kind=='primary_cut': raw=prefix[:-2]+',{"who":"C1","text":"cut'
    elif kind=='auxiliary_cut':
        raw=prefix[:-1]+',"panels":[{"key":"K1","blocks":['
        plan=co.build_plan([ChatCommand(id='cmd',name='cmd',prompt='Panel.',postprocess_prompt='Panel.',postprocess_target='last_bubble',postprocess_block_types=['note'])])
    else: raw=prefix
    await generate(monkeypatch,'free_body_json',raw,'LENGTH',plan)

@pytest.mark.asyncio
async def test_prefix_preserves_rich_complete_object_not_partial_prose(monkeypatch):
    body='<a>Turns.</a>**Hello 🌙.**<t>'+ ' '.join(f'Private-{i} detail-{i} changes thought-{i}.' for i in range(60))+'</t>Last.'
    raw=json.dumps({'replies':[{'who':'C1','text':body}]})[:-2]+',{"who":"C2","text":"DO NOT KEEP'
    replies,snaps=await generate(monkeypatch,'free_body_json',raw,'LENGTH',expected_failure=False)
    assert len(replies)==1 and replies[0].raw_body==body
    assert all('recovery' not in json.dumps(fb.metadata_for_reply(r)) for r in replies)
    assert snaps[0]['metadata']['output_limit_recovery']['retained_count']==1

@pytest.mark.asyncio
@pytest.mark.parametrize('protocol',['free_body_json','tagged_text'])
async def test_alternating_speaker_loop_rejects(monkeypatch,protocol):
    items=[{'who':'C1' if i%2 else 'C2','text':'Again.'} for i in range(32)]
    raw=json.dumps({'replies':items}) if protocol=='free_body_json' else ''.join(f'<m c="{i["who"]}">{i["text"]}</m>' for i in items)
    await generate(monkeypatch,protocol,raw)

@pytest.mark.asyncio
@pytest.mark.parametrize('protocol',['legacy_json','free_body_json','tagged_text'])
@pytest.mark.parametrize('case',['raised','flag','authentication'])
async def test_terminal_not_parse_retry_or_fallback(monkeypatch,protocol,case):
    monkeypatch.setenv('CHAT_RESPONSE_PROTOCOL',protocol)
    get_settings.cache_clear()
    terminal_type=LLMAuthenticationError if case=='authentication' else LLMPolicyBlockedError
    error=terminal_type()
    assert isinstance(error,LLMTerminalError) and not isinstance(error,(ValueError,LLMUnavailableError))
    response=error if case!='flag' else LLMResponse(content='blocked',finish_reason='STOP',provider_diagnostics={'policy_blocked':True})
    primary,fallback=Client(response),Client(LLMResponse(content='must not run'))
    saved,snaps=[],[]
    try:
        with pytest.raises(terminal_type):
            await CharacterRuntime(primary,fallback).generate_replies(characters=[Character(id='a',name='Aria')],recent_messages=[],user_message='next',
                persist_replies=saved.append,prompt_snapshot_callback=snaps.append)
        assert primary.calls==1 and fallback.calls==0 and saved==[]
        assert len(snaps)==1 and snaps[0]['metadata']['parse_attempts']==0
        assert snaps[0]['metadata']['attempt_diagnostics'][0]['code']==error.code
        assert not primary.outcomes
    finally: get_settings.cache_clear()

@pytest.mark.parametrize('payload', ['<m '+(' '*98000)+'x>', '<m c '+(' '*98000)+'x>', '<'+(' '*98000)+'a x'])
def test_attribute_and_reserved_nearmisses_have_external_deadline(payload):
    script='from app.engine import free_body_protocol as f\ntry: f.parse_response('+repr(payload)+',protocol="tagged_text",aliases={"C1":"a","N":None})\nexcept f.FreeBodyError: pass'
    subprocess.run([sys.executable,'-B','-c',script],check=True,timeout=4)

@pytest.mark.parametrize('field',['text','emotion'])
def test_valid_json_surrogate_pair_is_lossless(field):
    item={'who':'C1','text':'Hello.'}
    item[field]='🌙'
    raw=json.dumps({'replies':[item]},ensure_ascii=True)
    reply=fb.parse_response(raw,protocol='free_body_json',aliases={'C1':'a'})[0]
    assert getattr(reply,field)=='🌙'
    assert getattr(reply,field).encode('utf-8')

@pytest.mark.parametrize('protocol',['free_body_json','tagged_text'])
def test_render_metadata_must_match_actual_body(protocol):
    raw=json.dumps({'replies':[{'who':'C1','text':'Hello.'}]}) if protocol=='free_body_json' else '<m c="C1">Hello.</m>'
    reply=fb.parse_response(raw,protocol=protocol,aliases={'C1':'a'})[0]
    reply.render_parts=[{'type':'dialogue','text':'forged'}]
    with pytest.raises(fb.FreeBodyError): fb.metadata_for_reply(reply)


@pytest.mark.parametrize('field,value', [('response_protocol','free_body_json'),('raw_body','forged'),('render_parts',[])])
def test_schema_rejects_provider_provenance_input(field,value):
    from pydantic import ValidationError
    from app.schemas.dialogue import CharacterReply
    with pytest.raises(ValidationError):
        CharacterReply.model_validate({'text':'Hello.',field:value})


@pytest.mark.asyncio
@pytest.mark.parametrize('field', ['text','title','author','cell'])
async def test_combined_panels_reject_invalid_scalars_before_persistence(monkeypatch,field):
    plan=co.build_plan([ChatCommand(id='cmd',name='cmd',prompt='Panel.',postprocess_prompt='Panel.',postprocess_target='last_bubble',postprocess_block_types=['note','comments','table'])])
    block={'type':'note','text':'Ready.','title':'Status'}
    if field in ('text','title'): block[field]='bad\ud800'
    if field=='author': block={'type':'comments','comments':[{'author':'bad\ud800','text':'Ready.'}]}
    if field=='cell': block={'type':'table','headers':['Header'],'rows':[['bad\ud800']]}
    raw=json.dumps({'replies':[{'who':'C1','text':'Hello.'}],'panels':[{'key':'K1','blocks':[block]}]})
    await generate(monkeypatch,'free_body_json',raw,plan=plan)


@pytest.mark.asyncio
@pytest.mark.parametrize('field', ['reply_type','character_id','dialogue','emotion','action','thought'])
@pytest.mark.parametrize('value', [[], {}, False, 1])
async def test_legacy_shape_matrix_is_typed(monkeypatch,field,value):
    if field=='character_id' and value==1:
        # Historical scalar identity coercion stays compatible; container and
        # boolean identities are not the same policy.
        from app.engine.character_runtime import validate_replies
        assert validate_replies([{'character_id':1,'dialogue':'Hello.'}], default_character_id='a',
                                allowed_character_ids={'a'})[0].character_id=='a'
        return
    item={'character_id':'a','dialogue':'Hello.'}
    item[field]=value
    await generate(monkeypatch,'legacy_json',json.dumps({'replies':[item]}))
