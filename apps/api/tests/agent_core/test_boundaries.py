import asyncio
import hashlib
import io
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text, select
from sqlmodel import Session
from app.agent.contracts import AgentError
from app.agent import models as m
from app.agent.operations import AgentOperations, TOOL_SPECS
from app.db.models import Character, WorldSetting, CharacterAsset, ModelProviderAccount, ModelOption
from test_core import stage, approve, commit


def test_parallel_commit_and_atomic_receipt_rollback(service,owner,monkeypatch):
    d=stage(service,owner); approve(service,owner,d)
    original=service._receipt
    def fail(*a,**kw): raise RuntimeError('injected receipt persistence failure')
    monkeypatch.setattr(service,'_receipt',fail)
    with pytest.raises(RuntimeError): commit(service,owner,d)
    with service.engine.connect() as c:
        assert c.execute(text('select count(*) from characters')).scalar_one()==0
    assert service.get_draft(d['id'],owner)['status']=='approved'
    monkeypatch.setattr(service,'_receipt',original)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda _:commit(service,owner,d),range(2)))
    assert results[0]==results[1]
    with service.engine.connect() as c:
        assert c.execute(text('select count(*) from characters')).scalar_one()==1
        assert c.execute(text('select count(*) from agent_operations')).scalar_one()==1


def test_world_sparse_normalization_and_revision_triggers(service,owner):
    with Session(service.engine) as s:
        s.add(WorldSetting(id='w',title='World',location='old',thumbnail_url='/keep.png'))
        s.commit()
    d=service.create_draft(owner,'world',{'location':'  New  '},'w')
    assert d['before']=={'location':'old'} and d['after']=={'location':'New'}
    approve(service,owner,d); commit(service,owner,d)
    with Session(service.engine) as s:
        w=s.get(WorldSetting,'w'); assert w.thumbnail_url=='/keep.png' and w.location=='New'
        w.mood='calm'; s.add(w); s.commit()
    with service.engine.connect() as c:
        assert c.execute(text("select agent_revision from world_settings where id='w'")).scalar_one()==2
    from app.agent.schema import initialize_agent_schema
    initialize_agent_schema(service.engine)
    with service.engine.begin() as c:
        c.exec_driver_sql('PRAGMA recursive_triggers=ON')
        c.execute(text("update world_settings set title='Other' where id='w'"))
        assert c.execute(text("select agent_revision from world_settings where id='w'")).scalar_one()==3


def test_sparse_traits_and_strict_authored_fields(service,owner):
    with Session(service.engine) as s:
        s.add(Character(id='c',name='C',persona='secret',trait_scores={'confidence':5,'unknown_authored':4}))
        s.commit()
    d=stage(service,owner,'c',trait_scores={'kindness':4})
    assert set(d['before'])=={'trait_scores'} and 'persona' not in str(d)
    approve(service,owner,d); commit(service,owner,d)
    with Session(service.engine) as s:
        assert s.get(Character,'c').trait_scores['unknown_authored']==4
        assert s.get(Character,'c').trait_scores['confidence']==5
    for field in ('tts_voice_style','avatar_url','default_model','arbitrary'):
        with pytest.raises(AgentError): stage(service,owner,'c',**{field:'forbidden'})


def test_owner_routes_and_settings_shape(service,owner,monkeypatch):
    from app.api.lore_agent import router
    monkeypatch.setenv('LORE_AGENT_OWNER_KEY_SHA256',hashlib.sha256(b'owner-test-only').hexdigest())
    app=FastAPI(); app.state.agent_service=service; app.include_router(router)
    with TestClient(app) as client:
        public=client.get('/lore-agent/settings')
        assert public.status_code==200
        assert set(public.json())=={'enabled','llm_model_option_key','image_provider_account_id','image_model_id','mcp_enabled','owner_configured','llm_options','image_accounts','image_models'}
        assert 'owner-test-only' not in public.text
        assert client.post('/lore-agent/sessions',json={'request_id':'new'}).status_code==401
        headers={'X-Lore-Owner-Key':'owner-test-only'}
        session=client.post('/lore-agent/sessions',headers=headers,json={'request_id':'new'})
        assert session.status_code==200
        assert client.get('/lore-agent/sessions/'+session.json()['id'],headers=headers).json()==session.json()
        assert client.post('/lore-agent/tools/lore_approve_draft',headers=headers,json={}).status_code==404
        assert client.post('/lore-agent/tools/lore_search',headers=headers,json={'sql':'select'}).status_code==422
        d=client.post('/lore-agent/tools/lore_stage_character',headers=headers,json={'fields':{'name':'API','persona':'Navigator'}}).json()
        request={'expected_version':1,'preview_hash':d['preview_hash']}
        assert client.post('/lore-agent/drafts/'+d['id']+'/approve',json=request).status_code==401
        assert client.post('/lore-agent/drafts/'+d['id']+'/approve',headers=headers,json=request).status_code==200
        assert client.post('/lore-agent/drafts/'+d['id']+'/commit',headers=headers,json={**request,'request_id':'api-commit'}).json()['status']=='completed'
        token=client.post('/lore-agent/mcp/clients',headers=headers,json={'name':'external','scopes':['lore:read']}).json()
        assert client.get('/lore-agent/mcp/clients',headers=headers).json()==[token['client']]
        assert client.delete('/lore-agent/mcp/clients/'+token['client']['id'],headers=headers).status_code==204
        assert client.get('/lore-agent/mcp/clients',headers=headers).json()[0]['enabled'] is False


def test_context_permissions_apply_to_tools(service,owner):
    with Session(service.engine) as s:
        s.add(Character(id='c',name='C',persona='not selected')); s.commit()
    sid=service.create_session(owner,'session')['id']
    service.accept_run(owner,sid,'turn','Update', [{'kind':'character','id':'c','fields':['name']}])
    ops=AgentOperations(service,owner,sid)
    view=asyncio.run(ops.execute('lore_get_character',{'id':'c'}))
    assert view['fields']=={'name':'C'}
    with pytest.raises(AgentError): asyncio.run(ops.execute('lore_get_character',{'id':'c','fields':['persona']}))
    with pytest.raises(AgentError): asyncio.run(ops.execute('lore_stage_character',{'target_id':'c','fields':{'persona':'oops'}}))
    assert TOOL_SPECS['lore_search'].read_only
    assert TOOL_SPECS['lore_commit_draft'].external_effect
    with pytest.raises(AgentError): service.get_context([{'kind':'character','id':'missing','fields':['name']}],owner)


def test_model_selection_independent_and_missing_llm_fails(service,owner):
    from app.db.models import RuntimeSetting
    with Session(service.engine) as s:
        s.add(ModelProviderAccount(id='a',provider_type='openai_compatible',alias='Local',base_url='http://127.0.0.1:9999/v1',preset='custom'))
        s.add(ModelOption(id='o',key='local-option',provider_account_id='a',provider_type='openai_compatible',model='local-model',label='Local'))
        s.commit()
    view=service.settings_view()
    assert view['llm_options'][0]['key']=='local-option' and view['image_models']==[]
    with pytest.raises(AgentError): service.resolve_llm_route(owner)
    service.update_settings({'enabled':True,'llm_model_option_key':'local-option'})
    route=service.resolve_llm_route(owner)
    assert route.model=='local-model' and route.provider=='openai_compatible'
    with Session(service.engine) as s:
        assert s.exec(select(RuntimeSetting)).first() is None
    with pytest.raises(AgentError): service.update_settings({'enabled':True,'llm_model_option_key':'missing'})


def test_image_single_claim_outputs_and_separate_registration(service,owner,monkeypatch,tmp_path):
    from PIL import Image
    from app.core.config import get_settings
    monkeypatch.setenv('UPLOAD_ROOT',str(tmp_path/'uploads')); get_settings.cache_clear()
    with Session(service.engine) as s:
        s.add(Character(id='c',name='C',persona='P',avatar_url='/unchanged.png')); s.commit()
    monkeypatch.setattr(service,'settings_view',lambda:{'image_accounts':[{'id':'a','provider_type':'openai'}],'image_provider_account_id':'a','image_models':[{'id':'gpt-image-2.5-sunburst','supports_reference':False}],'image_model_id':'gpt-image-2.5-sunburst'})
    d=service.plan_image(owner,'c','An observatory')
    with pytest.raises(AgentError): service.claim_image_generation(owner,d['id'],'paid',1,d['preview_hash'])
    assert d['after']['generation_mode']=='undecided'
    with pytest.raises(AgentError): approve(service,owner,d)
    d=service.choose_image_mode(owner,d['id'],d['version'],'new')
    assert d['after']['owner_choice_confirmed']
    approve(service,owner,d)
    claim=service.claim_image_generation(owner,d['id'],'paid',d['version'],d['preview_hash'])
    assert claim['claimed']
    assert not service.claim_image_generation(owner,d['id'],'paid',d['version'],d['preview_hash'])['claimed']
    assert not service.claim_image_generation(owner,d['id'],'paid-new',d['version'],d['preview_hash'])['claimed']
    raw=io.BytesIO(); Image.new('RGB',(8,8),'blue').save(raw,format='PNG')
    job=claim['job_id']
    service.complete_image_job(owner,job,[{'data':raw.getvalue(),'mime_type':'image/png'}])
    assert service.get_operation('paid-new',owner)['status']=='completed'
    output=service.get_image_job(job,owner)['outputs'][0]['id']
    assert service.image_output(output,owner)['data']==raw.getvalue()
    with Session(service.engine) as s: assert not s.exec(select(CharacterAsset)).first()
    registration=service.stage_image_registration(owner,job,'reg',[output],'c',{'label':'Observatory'})
    with pytest.raises(AgentError): commit(service,owner,registration,'register')
    approve(service,owner,registration)
    result=commit(service,owner,registration,'register')
    with Session(service.engine) as s:
        asset=s.get(CharacterAsset,result['result']['asset_ids'][0])
        assert asset.enabled and not asset.is_default
        assert asset.image_url.startswith('/uploads/lore-agent/')
        assert s.get(Character,'c').avatar_url=='/unchanged.png'
    assert len(list((tmp_path/'uploads'/'lore-agent').iterdir()))==1
    assert commit(service,owner,registration,'register-new')['result']==result['result']
    with pytest.raises(AgentError): service._verified_image(b'not-image','image/png')
