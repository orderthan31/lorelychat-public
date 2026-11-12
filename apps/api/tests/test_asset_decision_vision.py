"""Actual selector -> media worker -> native HTTP wire -> fenced attachment."""
import asyncio
import base64
from io import BytesIO
import json
from types import SimpleNamespace

import httpx
from PIL import Image
import pytest
from sqlmodel import select

from app.api import conversations as api
from app.db.models import Character, CharacterAsset, Conversation, Message, MessageAsset, ModelOption, ModelProviderAccount, RuntimeSetting, PostCommitTask
from app.services import asset_decision_service as selector, decision_asset_media as media, decision_transport as transport


@pytest.mark.parametrize('provider,model,modalities,mimes,expected_mime', [
    ('google','gemini-decision-1',['text','image'],['image/webp'],'image/webp'),
    ('openrouter','typesafe/jev-1.13',['text','image'],['image/png','image/jpeg'],'image/jpeg'),
    ('openrouter','synthetic-text-only',['text'],[] ,None),
    ('openrouter','synthetic-unknown',None,None,None),
])
@pytest.mark.parametrize('outcome',['asset','none'])
def test_real_media_route_preserves_protocol_capability_and_none(session,tmp_path,monkeypatch,provider,model,modalities,mimes,expected_mime,outcome):
    monkeypatch.setattr(selector,'get_settings',lambda:SimpleNamespace(llm_mock=False))
    monkeypatch.setattr(media,'get_settings',lambda:SimpleNamespace(upload_root=str(tmp_path)))
    monkeypatch.setattr(selector.provider_secret_service,'get_secret',lambda _:'synthetic-key')
    original=BytesIO();Image.new('RGB',(256,1600),(100,130,180)).save(original,'WEBP',quality=80)
    original_bytes=original.getvalue();(tmp_path/'owned.webp').write_bytes(original_bytes)
    session.add_all([
        Character(id='c',name='Synthetic',persona='Synthetic'),
        Conversation(id='room',mode='user_character'),
        Message(id='m1',conversation_id='room',speaker_type='character',speaker_id='c',content='A quiet pause.',generation_job_id='job'),
        CharacterAsset(id='a1',character_id='c',label='Quiet portrait',description='Standing, thoughtful expression',image_url='/uploads/owned.webp',mood_tags=['sad'],generation_prompt='PRIVATE-PROMPT'),
        ModelProviderAccount(id='account',provider_type=provider,alias='Synthetic',configured=True,api_key_secret_ref='fake'),
        ModelOption(id='option',key='decision',provider_account_id='account',provider_type=provider,model=model,label='Synthetic',supports_decision=True,input_modalities=modalities,image_mime_types=mimes,capability_source='manual'),
        RuntimeSetting(id='global',decision_enabled=True,decision_model_option_key='decision'),
        PostCommitTask(id='task',unique_key='asset-vision',task_type='asset_decision',status='processing',lease_owner='worker',attempt_count=1,payload_={'conversation_id':'room','message_ids':['m1'],'decision_model_option_key':'decision'}),
    ]);session.commit()
    calls=[]
    def handler(request):
        assert not session.in_transaction()
        body=json.loads(request.content);calls.append(body)
        assert '/uploads/' not in request.content.decode() and 'PRIVATE-PROMPT' not in request.content.decode()
        parts=body['input'] if provider=='google' else body['state']
        images=[p for p in parts if isinstance(p,dict) and p.get('type') in ('image','image_url')] if isinstance(parts,list) else []
        if expected_mime:
            assert len(images)==1
            if provider=='google':
                image=images[0];assert image['mime_type']==expected_mime and image['resolution']=='low';data=base64.b64decode(image['data'])
            else:
                image=images[0]['image_url'];assert image['detail']=='low';prefix,encoded=image['url'].split(',',1);assert prefix=='data:'+expected_mime+';base64';data=base64.b64decode(encoded)
            with Image.open(BytesIO(data)) as decoded:
                assert max(decoded.size)<=512
                if provider=='openrouter':assert min(decoded.size)>=280
            assert data!=original_bytes and len(data)<=256*1024
        else:
            assert images==[]
        choice='none' if outcome=='none' else 'a1'
        return httpx.Response(200,json={'answers':{'m1':{'type':'choice','choice':choice,'confidence':.9,'probabilities':{'a1':.9 if choice=='a1' else .1,'none':.9 if choice=='none' else .1}}}})
    real=transport.decide_assets
    async def wire(route,**kwargs):
        return await real(route,**kwargs,_transport=httpx.MockTransport(handler))
    monkeypatch.setattr(transport,'decide_assets',wire)
    asyncio.run(api._execute_claimed_post_commit_task(session,session.get(PostCommitTask,'task'),lease_owner='worker'))
    session.expire_all()
    assert len(calls)==1
    assert [(x.message_id,x.asset_id) for x in session.exec(select(MessageAsset)).all()]==([('m1','a1')] if outcome=='asset' else [])
    assert session.get(PostCommitTask,'task').status=='completed'
    assert session.get(Message,'m1').content=='A quiet pause.'
    assert (tmp_path/'owned.webp').read_bytes()==original_bytes
