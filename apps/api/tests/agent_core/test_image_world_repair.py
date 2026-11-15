"""I1/I2 regressions: isolated real core + actual SDK on synthetic wire."""
import asyncio
import base64
import hashlib
import io
import json
from pathlib import Path

import httpx
import pytest
from PIL import Image
from pydantic import ValidationError
from sqlalchemy import select, text
from sqlmodel import Session

from app.agent import models as m
from app.agent.contracts import AgentError, ImageJobView, ImagePlanInput, RegistrationBody
from app.agent.operations import AgentOperations
from app.db.models import Character, ModelProviderAccount, WorldSetting
from app.engine import litellm_transport as transport


def accounts(service, monkeypatch):
    monkeypatch.setattr('app.services.provider_secret_service.get_secret', lambda ref: 'synthetic-unused-key' if ref == 'fixture-ref' else None)
    with Session(service.engine) as s:
        for id, provider, enabled, ref in [('oa','openai',True,'fixture-ref'),('oa2','openai',True,'fixture-ref'),('xa','xai',True,'fixture-ref'),('disabled','xai',False,'fixture-ref'),('missing','openai',True,'absent'),('unsupported','anthropic',True,'fixture-ref')]:
            s.add(ModelProviderAccount(id=id,provider_type=provider,alias=id,enabled=enabled,api_key_secret_ref=ref,base_url='https://api.x.ai/v1' if provider=='xai' else None))
        s.add(WorldSetting(id='w',title='World',description='Preserved description',thumbnail_url='/old.png',tags=['preserved']))
        s.add(Character(id='c',name='Character',persona='Preserved persona'))
        s.commit()


def choose(service, account='oa', model='gpt-image-2.5-sunburst'):
    return service.update_settings({'enabled':True,'mcp_enabled':True,'image_provider_account_id':account,'image_model_id':model})


def approve(service, owner, d):
    return service.approve_draft(owner,d['id'],d['version'],d['preview_hash'])


def commit(service, owner, d, key):
    return asyncio.run(service.commit_draft(owner,d['id'],key,d['version'],d['preview_hash']))


def world(service):
    with service.engine.connect() as c:
        return dict(c.execute(select(WorldSetting.__table__).where(WorldSetting.__table__.c.id=='w')).mappings().one())


def generated(service, owner, monkeypatch, kind='world'):
    accounts(service,monkeypatch)
    choose(service)
    calls=[]
    buf=io.BytesIO()
    Image.new('RGB',(8,8),'green').save(buf,format='PNG')
    raw=buf.getvalue()
    def wire(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200,json={'created':1,'data':[{'b64_json':base64.b64encode(raw).decode()} for _ in range(2)]})
    monkeypatch.setattr(transport,'new_http_transport',lambda:httpx.MockTransport(wire))
    args={'target_id':'w' if kind=='world' else 'c','prompt':'Representative background landscape','count':2}
    if kind=='world': args['target_kind']='world'
    d=asyncio.run(AgentOperations(service,owner).execute('lore_plan_image',args))
    before=world(service)
    with pytest.raises(AgentError): commit(service,owner,d,'paid')
    assert calls==[]
    if kind=='world':
        assert d['after']['generation_mode']=='undecided'
        with pytest.raises(AgentError): approve(service,owner,d)
        d=service.choose_image_mode(owner,d['id'],d['version'],'new')
        assert d['after']['owner_choice_confirmed']
    approve(service,owner,d)
    receipt=commit(service,owner,d,'paid')
    assert receipt['status']=='completed'
    assert commit(service,owner,d,'paid')==receipt
    assert len(calls)==1
    job=service.get_image_job(receipt['result']['job_id'],owner)
    assert ImageJobView.model_validate(job).plan['target_kind']==kind
    assert len(job['outputs'])==2
    assert world(service)==before
    return job,raw,calls


def registration(service,owner,job,key='stage',**overrides):
    args={'job_id':job['id'],'request_id':key,'output_ids':[job['outputs'][0]['id']],'target_id':'w','target_kind':'world','metadata':{}}
    args.update(overrides)
    return asyncio.run(AgentOperations(service,owner).execute('lore_stage_image_registration',args))


def test_fresh_catalog_cross_provider_disabled_missing_and_invalid(service,owner,monkeypatch):
    accounts(service,monkeypatch)
    initial=service.settings_view()
    assert initial['image_provider_account_id'] is None
    assert {a['id'] for a in initial['image_accounts']}=={'oa','oa2','xa'}
    pairs={(x['provider_type'],x['id']) for x in initial['image_models']}
    # Catalog aggregation follows active routes, including automatic shutdown.
    # Exact route admission/cutoff is asserted by the SDK-specific tests.
    from app.agent.image_generation import list_image_models
    expected={(provider,x['id']) for provider in ('openai','xai') for x in list_image_models(provider)}
    assert pairs==expected and len(pairs)==len(initial['image_models'])
    assert ('openai','gpt-image-2.5-sunburst') in pairs and ('xai','grok-imagine-image') in pairs
    assert 'fixture-ref' not in json.dumps(initial) and 'synthetic-unused-key' not in json.dumps(initial)
    choose(service)
    assert service.settings_view()['image_models']==initial['image_models']
    choose(service,'xa','grok-imagine-image')
    assert service.resolve_image_route(owner,'xa','grok-imagine-image').provider=='xai'
    for account,model in [('xa','gpt-image-2.5-sunburst'),('oa','grok-imagine-image'),('disabled','grok-imagine-image'),('missing','gpt-image-2.5-sunburst'),('oa',None)]:
        with pytest.raises(AgentError): choose(service,account,model)
    with Session(service.engine) as s:
        a=s.get(ModelProviderAccount,'xa'); a.enabled=False; s.add(a); s.commit()
    assert {x['provider_type'] for x in service.settings_view()['image_models']}=={'openai'}
    with pytest.raises(AgentError): service.resolve_image_route(owner,'xa','grok-imagine-image')


def test_world_sdk_separate_registration_approval_and_receipt(service,owner,monkeypatch):
    job,raw,calls=generated(service,owner,monkeypatch)
    before=world(service)
    d=registration(service,owner,job)
    assert d['before']=={'thumbnail_url':'/old.png'}
    assert d['after']['output_digest']==hashlib.sha256(raw).hexdigest()
    assert d==registration(service,owner,job)
    assert world(service)==before
    with pytest.raises(AgentError): commit(service,owner,d,'register')
    assert world(service)==before
    approve(service,owner,d)
    receipt=commit(service,owner,d,'register')
    assert receipt==commit(service,owner,d,'register')
    assert commit(service,owner,d,'another-key')['result']==receipt['result']
    result=receipt['result']
    assert result['id']=='w' and result['path']=='/world-settings/w'
    after=world(service)
    assert after['thumbnail_url']==d['after']['thumbnail_url']==result['thumbnail_url']
    assert {k:v for k,v in before.items() if k not in ('thumbnail_url','updated_at')}=={k:v for k,v in after.items() if k not in ('thumbnail_url','updated_at')}
    from app.core.config import get_settings
    assert (Path(get_settings().upload_root)/after['thumbnail_url'].removeprefix('/uploads/')).read_bytes()==raw
    assert len(service.get_image_job(job['id'],owner)['outputs'])==2 and len(calls)==1
    with service.engine.connect() as c:
        assert c.execute(text('select count(*) from character_assets')).scalar_one()==0


@pytest.mark.parametrize('mutation',['text','thumbnail_aba'])
def test_world_registration_stale_domain_edits_including_aba(service,owner,monkeypatch,mutation):
    job,_,calls=generated(service,owner,monkeypatch)
    d=registration(service,owner,job); approve(service,owner,d)
    with service.engine.begin() as c:
        if mutation=='text': c.execute(text("update world_settings set description='Concurrent' where id='w'"))
        else:
            c.execute(text("update world_settings set thumbnail_url='/other.png' where id='w'"))
            c.execute(text("update world_settings set thumbnail_url='/old.png' where id='w'"))
    before=world(service)
    with pytest.raises(AgentError): commit(service,owner,d,'stale')
    assert world(service)==before and len(calls)==1
    assert service.get_draft(d['id'],owner)['status']=='conflict'


def test_world_registration_receipt_failure_rolls_back(service,owner,monkeypatch):
    job,_,_=generated(service,owner,monkeypatch)
    d=registration(service,owner,job); approve(service,owner,d)
    before=world(service)
    def fail(*args,**kwargs): raise RuntimeError('synthetic receipt failure')
    with monkeypatch.context() as patch:
        patch.setattr(service,'_receipt',fail)
        with pytest.raises(RuntimeError): commit(service,owner,d,'rollback')
    assert world(service)==before
    assert service.get_draft(d['id'],owner)['status']=='approved'
    assert service.get_operation('rollback',owner)['status']=='unknown'
    assert commit(service,owner,d,'rollback')['status']=='completed'


def test_world_kind_selection_scope_reference_and_url_guards(service,owner,monkeypatch):
    job,_,_=generated(service,owner,monkeypatch)
    for overrides in [{'output_ids':[x['id'] for x in job['outputs']]},{'metadata':{'label':'no world asset metadata'}}]:
        with pytest.raises(ValidationError): registration(service,owner,job,**overrides)
    for overrides in [{'target_kind':'character'},{'target_id':'c'},{'output_ids':['other']}]:
        with pytest.raises(AgentError): registration(service,owner,job,**overrides)
    with pytest.raises(AgentError): service.plan_image(owner,'c','World scene',target_kind='world')
    with pytest.raises(AgentError): service.plan_image(owner,'w','World scene',target_kind='world',reference_output_id=job['outputs'][0]['id'])
    with pytest.raises(AgentError): service.create_draft(owner,'world',{'thumbnail_url':'https://arbitrary.test/a.png'},'w')
    client=service.create_client('outsider',['lore:read','lore:draft','lore:image:generate','lore:commit'])
    outsider=service.principal_for_token(client['token'])
    with pytest.raises(AgentError): registration(service,outsider,job)
    d=registration(service,owner,job)
    with pytest.raises(AgentError): approve(service,outsider,d)
    assert world(service)['thumbnail_url']=='/old.png'


def test_world_registration_parallel_duplicates_and_output_binding(service,owner,monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    job,raw,calls=generated(service,owner,monkeypatch)
    d=registration(service,owner,job); approve(service,owner,d)
    # Even a coherently replaced retained blob cannot change approved bytes.
    buf=io.BytesIO(); Image.new('RGB',(8,8),'red').save(buf,format='PNG')
    changed=buf.getvalue()
    with service.engine.begin() as c:
        c.execute(m.image_outputs.update().where(m.image_outputs.c.id==job['outputs'][0]['id']).values(data=changed,size=len(changed),digest=hashlib.sha256(changed).hexdigest()))
    with pytest.raises(AgentError): commit(service,owner,d,'bound')
    assert world(service)['thumbnail_url']=='/old.png'
    with service.engine.begin() as c:
        c.execute(m.image_outputs.update().where(m.image_outputs.c.id==job['outputs'][0]['id']).values(data=raw,size=len(raw),digest=hashlib.sha256(raw).hexdigest()))
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda _:commit(service,owner,d,'bound'),range(2)))
    assert results[0]==results[1] and len(calls)==1
    assert service.get_operation('bound',owner)==results[0]


def test_world_generation_stale_plan_rejected_before_sdk(service,owner,monkeypatch):
    accounts(service,monkeypatch); choose(service)
    d=service.plan_image(owner,'w','Landscape',target_kind='world')
    assert d['after']['generation_mode']=='undecided'
    d=service.choose_image_mode(owner,d['id'],d['version'],'new')
    approve(service,owner,d)
    with service.engine.begin() as c:
        c.execute(text("update world_settings set title='Edited' where id='w'"))
    def forbidden(): raise AssertionError('No SDK transport may be constructed')
    monkeypatch.setattr(transport,'new_http_transport',forbidden)
    with pytest.raises(AgentError): commit(service,owner,d,'stale-generation')
    assert world(service)['thumbnail_url']=='/old.png'
    with service.engine.connect() as c:
        assert not c.execute(select(m.image_jobs)).first()


def test_old_character_payload_and_stored_plan_compatibility(service,owner,monkeypatch):
    assert ImagePlanInput(target_id='c',prompt='x').target_kind=='character'
    assert RegistrationBody(request_id='x',output_ids=['o'],target_id='c').target_kind=='character'
    job,_,calls=generated(service,owner,monkeypatch,'character')
    # Simulate a retained job written before target_kind existed.
    with service._write() as c:
        data=service._get(c,m.image_jobs,job['id'],owner)
        data['_plan'].pop('target_kind')
        service._put(c,m.image_jobs,job['id'],data)
    d=service.stage_image_registration(owner,job['id'],'old-stage',[job['outputs'][0]['id']],'c',{})
    assert d==service.stage_image_registration(owner,job['id'],'old-stage',[job['outputs'][0]['id']],'c',{})
    approve(service,owner,d)
    result=commit(service,owner,d,'old-register')['result']
    assert set(result)=={'asset_ids','target_id'} and len(result['asset_ids'])==1
    assert len(calls)==1 and world(service)['thumbnail_url']=='/old.png'
