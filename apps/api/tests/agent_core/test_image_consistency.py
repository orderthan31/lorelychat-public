import asyncio
import hashlib
import io
import json
import pytest
from PIL import Image
from sqlmodel import Session
from sqlalchemy import select
from app.agent import models as m
from app.agent.contracts import AgentError
from app.agent.operations import AgentOperations
from app.db.models import Character, CharacterAsset


def setup_image(service, monkeypatch, tmp_path, avatar=True):
    from app.core.config import get_settings
    root=tmp_path/'uploads'; root.mkdir()
    monkeypatch.setattr(get_settings(), 'upload_root', str(root))
    monkeypatch.setattr(get_settings(), 'shared_upload_root', None)
    out=io.BytesIO(); Image.new('RGB',(4,4),'red').save(out,format='PNG')
    data=out.getvalue(); (root/'avatar.png').write_bytes(data)
    with Session(service.engine) as s:
        s.add(Character(id='c',name='Ada',persona='Navigator',avatar_url='/uploads/avatar.png' if avatar else None)); s.commit()
    # Core-only capability fixture; actual SDK wire admission is tested by its owner.
    from app.agent import image_generation
    monkeypatch.setattr(image_generation,'list_image_models',lambda provider:[{'id':'gpt-image-2.5-sunburst','provider_type':'openai','supports_reference':True}])
    monkeypatch.setattr(service,'settings_view',lambda:{'image_provider_account_id':'a','image_model_id':'gpt-image-2.5-sunburst','image_accounts':[{'id':'a','provider_type':'openai'}],'image_models':[{'id':'gpt-image-2.5-sunburst','provider_type':'openai','supports_reference':True}]})
    return root,data


def plan(service,owner):
    return service.plan_image(owner,'c','A new pose')


def approve(service,owner,d):
    return service.approve_draft(owner,d['id'],d['version'],d['preview_hash'])


def claim(service,owner,d):
    return service.claim_image_generation(owner,d['id'],'paid',d['version'],d['preview_hash'])


def test_existing_requires_owner_choice(service,owner,monkeypatch,tmp_path):
    setup_image(service,monkeypatch,tmp_path)
    d=plan(service,owner)
    assert d['after']['generation_mode']=='undecided'
    assert not d['after']['owner_choice_confirmed']
    with pytest.raises(AgentError): approve(service,owner,d)
    assert 'lore_image_context' in AgentOperations.tool_specs()
    assert not any('choice' in n or 'confirm' in n for n in AgentOperations.tool_specs())
    for fields in ({'generation_mode':'new'},{'owner_choice_confirmed':True},{'_image_choice':{'owner':'owner'}},{'reference':None}):
        with pytest.raises(AgentError): service.revise_draft(owner,d['id'],1,fields)
    d=service.choose_image_mode(owner,d['id'],1,'new')
    assert d['version']==2 and d['after']['owner_choice_confirmed']
    approve(service,owner,d)
    assert claim(service,owner,d)['claimed']
    assert not claim(service,owner,d)['claimed']


def test_reference_snapshot_and_source_change(service,owner,monkeypatch,tmp_path):
    root,data=setup_image(service,monkeypatch,tmp_path)
    d=plan(service,owner)
    ref=d['after']['image_context']['candidates'][0]['id']
    d=service.choose_image_mode(owner,d['id'],1,'reference',ref)
    assert service.load_image_reference(owner,d['after'])['data']==data
    assert '/uploads/' not in json.dumps(d)
    approve(service,owner,d)
    (root/'avatar.png').unlink()
    with pytest.raises(AgentError): claim(service,owner,d)
    assert service.draft_image_reference(owner,d['id'])['data']==data


def test_noimage_late_asset_blocks_paid(service,owner,monkeypatch,tmp_path):
    setup_image(service,monkeypatch,tmp_path,False)
    d=plan(service,owner)
    assert d['after']['generation_mode']=='new'
    approve(service,owner,d)
    with Session(service.engine) as s:
        s.add(CharacterAsset(id='late',character_id='c',label='Late',image_url='/uploads/avatar.png')); s.commit()
    with pytest.raises(AgentError): claim(service,owner,d)
    with pytest.raises(AgentError): approve(service,owner,d)


def test_remote_and_symlink_unavailable(service,owner,monkeypatch,tmp_path):
    root,data=setup_image(service,monkeypatch,tmp_path)
    (root/'link.png').symlink_to(root/'avatar.png')
    with Session(service.engine) as s:
        c=s.get(Character,'c'); c.avatar_url='https://evil.example/uploads/avatar.png'; s.add(c)
        s.add(CharacterAsset(id='link',character_id='c',label='Link',image_url='/uploads/link.png')); s.commit()
    ctx=service.image_context(owner,'c')
    assert ctx['has_existing_images'] and all(not c['available'] for c in ctx['candidates'])
    assert 'evil.example' not in json.dumps(ctx)
    d=plan(service,owner)
    with pytest.raises(AgentError): service.choose_image_mode(owner,d['id'],1,'reference',ctx['candidates'][0]['id'])


def test_reference_claim_retained_and_forgery(service,owner,monkeypatch,tmp_path):
    setup_image(service,monkeypatch,tmp_path)
    d=plan(service,owner)
    d=service.choose_image_mode(owner,d['id'],1,'reference','avatar')
    approve(service,owner,d); claim(service,owner,d)
    assert service.load_image_reference(owner,d['after'])['sha256']==d['after']['reference']['sha256']
    from copy import deepcopy
    for key,value in [('prompt','forged'),('generation_mode','undecided'),('target_id','other')]:
        fake=deepcopy(d['after']); fake[key]=value
        with pytest.raises(AgentError): service.load_image_reference(owner,fake)
    with service.engine.begin() as c:
        c.execute(m.image_references.update().values(digest='0'*64))
    with pytest.raises(AgentError): service.load_image_reference(owner,d['after'])


def test_revision_invalidates_choice_and_paid_approval(service,owner,monkeypatch,tmp_path):
    setup_image(service,monkeypatch,tmp_path)
    d=plan(service,owner)
    d=service.choose_image_mode(owner,d['id'],1,'reference','avatar')
    approve(service,owner,d)
    revised=service.revise_draft(owner,d['id'],d['version'],{'prompt':'Different pose'})
    assert revised['version']==3 and revised['status']=='draft'
    assert revised['after']['generation_mode']=='undecided'
    assert not revised['after']['owner_choice_confirmed'] and revised['after']['reference'] is None
    with pytest.raises(AgentError): approve(service,owner,revised)
    with pytest.raises(AgentError): claim(service,owner,d)
    with service.engine.connect() as c:
        row=service._get(c,m.drafts,d['id'])
        assert '_image_choice' not in row and '_approval' not in row


def test_owner_rest_boundary_and_preview(service,owner,monkeypatch,tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.api.lore_agent import router
    _,data=setup_image(service,monkeypatch,tmp_path)
    monkeypatch.setenv('LORE_AGENT_OWNER_KEY_SHA256',hashlib.sha256(b'synthetic').hexdigest())
    app=FastAPI(); app.state.agent_service=service; app.include_router(router)
    d=plan(service,owner)
    endpoint=f"/lore-agent/drafts/{d['id']}/image-choice"
    with TestClient(app) as client:
        assert client.post(endpoint,json={'expected_version':1,'generation_mode':'new'}).status_code==401
        headers={'X-Lore-Owner-Key':'synthetic'}
        assert client.post('/lore-agent/tools/lore_image_choice',headers=headers,json={}).status_code==404
        ctx=client.get('/lore-agent/image-context/character/c',headers=headers)
        assert ctx.status_code==200 and ctx.json()['has_existing_images']
        preview=client.get('/lore-agent/image-context/character/c/references/avatar',headers=headers)
        assert preview.content==data and preview.headers['cache-control']=='private, no-store'
        response=client.post(endpoint,headers=headers,json={'expected_version':1,'generation_mode':'reference','reference_id':'avatar'})
        assert response.status_code==200
        chosen=response.json(); assert chosen['version']==2
        assert client.get(f"/lore-agent/drafts/{d['id']}/reference",headers=headers).content==data
        assert client.post(endpoint,headers=headers,json={'expected_version':1,'generation_mode':'new'}).status_code==409
        assert client.post(endpoint,headers=headers,json={'expected_version':2,'generation_mode':'new','owner_choice_confirmed':True}).status_code==422
        assert service.get_draft(d['id'],owner)==chosen


def test_external_cannot_choose_and_foreign_reference(service,owner,monkeypatch,tmp_path):
    setup_image(service,monkeypatch,tmp_path)
    service.update_settings({'enabled':True,'mcp_enabled':True})
    client=service.create_client('external',['lore:read','lore:draft','lore:commit','lore:image:generate'])
    p=service.principal_for_token(client['token'])
    ops=AgentOperations(service,p)
    ctx=asyncio.run(ops.execute('lore_image_context',{'target_id':'c'}))
    assert ctx['has_existing_images']
    d=service.plan_image(p,'c','Pose')
    with pytest.raises(AgentError): service.choose_image_mode(p,d['id'],1,'new')
    with pytest.raises(AgentError): asyncio.run(ops.execute('lore_confirm_image',{}))
    with pytest.raises(AgentError): service.choose_image_mode(owner,d['id'],1,'reference','asset:foreign')
    d=service.choose_image_mode(owner,d['id'],1,'reference','avatar')
    approve(service,owner,d)
    assert claim(service,p,d)['claimed']
    assert service.load_image_reference(p,d['after'])['data']
    other=service.create_client('other',['lore:read','lore:image:generate'])
    outsider=service.principal_for_token(other['token'])
    with pytest.raises(AgentError): service.load_image_reference(outsider,d['after'])


@pytest.mark.parametrize('url',['/uploads/.image-cache/x.png','/uploads/.image-cleanup/x.png','file:///uploads/avatar.png','http://localhost/uploads/avatar.png','/uploads/../avatar.png','/uploads/%2e%2e/avatar.png'])
def test_private_and_remote_paths_never_available(service,owner,monkeypatch,tmp_path,url):
    setup_image(service,monkeypatch,tmp_path)
    with Session(service.engine) as s:
        c=s.get(Character,'c'); c.avatar_url=url; s.add(c); s.commit()
    ctx=service.image_context(owner,'c')
    assert ctx['has_existing_images'] and not ctx['candidates'][0]['available']
    assert url not in json.dumps(ctx)


def test_unsupported_reference_and_legacy_plan_rejected(service,owner,monkeypatch,tmp_path):
    from app.agent import image_generation
    setup_image(service,monkeypatch,tmp_path)
    d=plan(service,owner)
    monkeypatch.setattr(image_generation,'list_image_models',lambda p:[{'id':'gpt-image-2.5-sunburst','supports_reference':False}])
    with pytest.raises(AgentError,match='does not support reference'): service.choose_image_mode(owner,d['id'],1,'reference','avatar')
    assert service.get_draft(d['id'],owner)==d
    with service._write() as c:
        raw=service._get(c,m.drafts,d['id'])
        raw['after'].pop('generation_mode'); service._hash_draft(raw); service._put(c,m.drafts,d['id'],raw)
    with pytest.raises(AgentError): approve(service,owner,service.get_draft(d['id'],owner))


def test_noimage_simple_default_claim(service,owner,monkeypatch,tmp_path):
    setup_image(service,monkeypatch,tmp_path,False)
    d=plan(service,owner); approve(service,owner,d)
    assert claim(service,owner,d)['claimed']
    assert service.load_image_reference(owner,d['after']) is None


def test_real_sdk_reference_dispatch_and_replay(service,owner,monkeypatch,tmp_path):
    import base64
    import httpx
    from app.core.config import get_settings
    from app.db.models import ModelProviderAccount
    from app.engine import litellm_transport as transport
    root=tmp_path/'uploads'; root.mkdir()
    monkeypatch.setattr(get_settings(),'upload_root',str(root))
    monkeypatch.setattr(get_settings(),'shared_upload_root',None)
    image=io.BytesIO(); Image.new('RGB',(8,8),'blue').save(image,format='PNG')
    raw=image.getvalue(); (root/'original.png').write_bytes(raw)
    monkeypatch.setattr('app.services.provider_secret_service.get_secret',lambda ref:'synthetic-key')
    with Session(service.engine) as s:
        s.add(Character(id='c',name='C',persona='P',avatar_url='/uploads/original.png'))
        s.add(ModelProviderAccount(id='a',provider_type='openai',alias='Fixture',enabled=True,api_key_secret_ref='fixture'))
        s.commit()
    service.update_settings({'enabled':True,'image_provider_account_id':'a','image_model_id':'gpt-image-2.5-sunburst'})
    calls=[]
    def wire(request):
        calls.append(request)
        assert request.url.path=='/v1/images/edits'
        assert raw in request.content and b'gpt-image-2.5-sunburst' in request.content
        return httpx.Response(200,json={'created':1,'data':[{'b64_json':base64.b64encode(raw).decode()}]})
    monkeypatch.setattr(transport,'new_http_transport',lambda:httpx.MockTransport(wire))
    d=plan(service,owner)
    with pytest.raises(AgentError): approve(service,owner,d)
    assert calls==[]
    d=service.choose_image_mode(owner,d['id'],1,'reference','avatar')
    approve(service,owner,d)
    def dispatch():
        return asyncio.run(service.commit_draft(owner,d['id'],'sdk-paid',d['version'],d['preview_hash']))
    receipt=dispatch()
    assert receipt['status']=='completed'
    assert dispatch()==receipt and len(calls)==1
    job=service.get_image_job(receipt['result']['job_id'],owner)
    assert len(job['outputs'])==1
    assert service.draft_image_reference(owner,d['id'])['data']==raw
    assert service.image_output(job['outputs'][0]['id'],owner)['data']==raw
    with Session(service.engine) as s:
        assert s.get(Character,'c').avatar_url=='/uploads/original.png'
    with service.engine.connect() as c:
        assert c.execute(select(m.image_references.c.id)).all()
        assert not c.execute(select(CharacterAsset.__table__)).first()
