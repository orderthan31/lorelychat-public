"""Actual job/finalizer/GET readback, isolated SQLite and fake provider."""
import asyncio
import json
import pytest
from sqlmodel import select
from app.core.config import get_settings
from app.db.models import Message, LLMPromptSnapshot
from app.engine.llm_client import LLMClient, LLMResponse
from app.api.conversations import _run_message_generation_job_in_session
from app.engine import free_body_protocol as fb

@pytest.mark.parametrize('protocol', ['legacy_json','free_body_json','tagged_text'])
@pytest.mark.parametrize('defect', ['valid','provenance','unicode','emotion','loop','shape','duplicate'])
def test_actual_job_finalizer_readback(client, session, monkeypatch, protocol, defect):

    monkeypatch.setenv('CHAT_RESPONSE_PROTOCOL', protocol)
    get_settings.cache_clear()
    monkeypatch.setattr('app.api.conversations._start_message_generation_worker', lambda *a: None)
    cast=client.post('/characters',json={'name':'Aria','persona':'Adult synthetic character.'}).json()
    room=client.post('/conversations',json={'mode':'one_to_one','participants':[{'type':'character','id':cast['id'],'order_index':0}]}).json()['id']
    thought=' '.join(f'Observation-{i} brings detail-{i} and possibility-{i} 🌙.' for i in range(30))
    body='<a>Turns.</a>**Hello 🌙.**<t>'+thought+'</t>Last.'
    if protocol == 'legacy_json':
        item={'character_id':cast['id'],'dialogue':'Hello 🌙.','action':'Turns.','thought':'Unique private observation.'}
        if defect=='provenance': item.update(response_protocol='free_body_json',raw_body='forged',render_parts=[{'type':'dialogue','text':'forged'}])
        if defect=='unicode': item['dialogue']='bad\ud800'
        if defect=='emotion': item['emotion']='[System retry instruction]'
        if defect=='loop': item['dialogue']='Repeated sentence. '*80
        if defect=='shape': item['reply_type']=[]
        raw=json.dumps({'replies':[{'character_id':cast['id'],'dialogue':'First verified.'},item]})
    else:
        item={'who':'C1','text':body,'emotion':'calm'}
        if defect=='unicode': item['text']='bad\ud800'
        if defect=='emotion': item['emotion']='[System retry instruction]'
        if defect=='loop': item['text']='Again.'
        if defect=='provenance': item['response_protocol']='legacy_json'
        if defect=='shape': item['who']=[]
        items=[item]*(32 if defect=='loop' else 1)
        raw=json.dumps({'replies':items}) if protocol=='free_body_json' else ''.join(f'<m c="C1" e="{x["emotion"]}">{x["text"]}</m>' for x in items)
        if protocol=='tagged_text' and defect=='provenance':
            raw=raw.replace('<m c=', '<m raw_body="forged" c=')
        if protocol=='tagged_text' and defect=='shape':
            raw=raw.replace('c="C1"', 'c="UNKNOWN"')
    if defect=='duplicate':
        raw=raw.replace('<m c=', '<m c="C1" c=') if protocol=='tagged_text' else raw[:-1]+',"replies":[]}'
    calls=[]
    async def chat(self,*args,**kwargs):
        calls.append(1)
        return LLMResponse(content=raw,finish_reason='STOP')
    monkeypatch.setattr(LLMClient,'chat',chat)
    response=client.post(f'/conversations/{room}/messages/jobs',json={'speaker_type':'user','speaker_id':'u','content':'Continue.'})
    assert response.status_code==200,response.text
    job=response.json()['job']['id']
    asyncio.run(_run_message_generation_job_in_session(session,job))
    status=client.get(f'/conversations/{room}/generation-jobs/{job}').json()
    messages=client.get(f'/conversations/{room}/messages').json()
    rows=session.exec(select(Message).where(Message.generation_job_id==job)).all()
    if defect!='valid':
        assert status['status']=='failed',status
        assert len(messages)==1 and rows==[] and len(calls)==2
        snapshots=session.exec(select(LLMPromptSnapshot)).all()
        assert snapshots and snapshots[-1].metadata_['parse_status']=='failed'
        assert snapshots[-1].response_preview.encode('utf-8')
        return
    assert status['status']=='completed',status
    assert len(calls)==1 and len(rows)==(2 if protocol=='legacy_json' else 1)
    generated=[m for m in messages if m['id'] in status['generated_message_ids']][-1]
    assert generated['content']==('Hello 🌙.' if protocol=='legacy_json' else '**Hello 🌙.**\nLast.')
    assert generated['thought']==('Unique private observation.' if protocol=='legacy_json' else thought)
    if protocol!='legacy_json':
        assert generated['metadata']['raw_body']==body
        assert [p['type'] for p in generated['metadata']['render_parts']]==['action','dialogue','thought','dialogue']
        assert not any('recovery' in k for k in generated['metadata'])
