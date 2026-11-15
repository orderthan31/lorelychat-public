import asyncio
import hashlib
import pytest
from sqlalchemy import text
from sqlmodel import Session
from app.agent.contracts import Principal, AgentError
from app.agent.operations import AgentOperations
from app.db.models import Character


def stage(service, owner, target_id=None, **fields):
    return service.create_draft(owner, 'character', fields or {'name':'Ada', 'persona':'Navigator'}, target_id=target_id)

def commit(service, owner, draft, key='commit-1'):
    return asyncio.run(service.commit_draft(owner, draft['id'], key, draft['version'], draft['preview_hash']))

def approve(service, owner, draft):
    return service.approve_draft(owner, draft['id'], draft['version'], draft['preview_hash'])

def test_approval_idempotency_and_new_key(service, owner):
    d = stage(service, owner)
    with pytest.raises(AgentError): commit(service, owner, d)
    approve(service, owner, d)
    a = commit(service, owner, d)
    assert a == commit(service, owner, d)
    b = commit(service, owner, d, 'new-key')
    assert b['result'] == a['result']
    with service.engine.connect() as c:
        assert c.execute(text('select count(*) from characters')).scalar_one() == 1
    with pytest.raises(AgentError):
        asyncio.run(service.commit_draft(owner, d['id'], 'commit-1', 999, d['preview_hash']))

def test_sparse_update_preserves_private_fields_and_external_cas(service, owner):
    with Session(service.engine) as s:
        s.add(Character(id='c',name='Before',persona='p',tts_voice_style='M5',trait_scores={'custom':4}))
        s.commit()
    d=stage(service,owner,'c',description='new')
    assert 'tts_voice_style' not in d['after']
    approve(service,owner,d)
    result=commit(service,owner,d)
    with Session(service.engine) as s:
        c=s.get(Character,'c')
        assert c.tts_voice_style=='M5' and c.trait_scores=={'custom':4} and c.description=='new'
        assert 'agent_revision' not in c.model_dump()
    d=stage(service,owner,'c',description='stale')
    approve(service,owner,d)
    with service.engine.begin() as c:
        c.execute(text("update characters set name='Concurrent' where id='c'"))
    with pytest.raises(AgentError): commit(service,owner,d,'stale')
    assert service.get_draft(d['id'],owner)['status']=='conflict'

def test_revision_invalidates_approval(service,owner):
    d=stage(service,owner); approve(service,owner,d)
    revised=service.revise_draft(owner,d['id'],1,{'name':'Grace'})
    assert revised['version']==2 and revised['status']=='draft'
    with pytest.raises(AgentError): commit(service,owner,revised)
    with pytest.raises(AgentError): approve(service,owner,d)

def test_scopes_revocation_owner_and_no_approve_tool(service,owner,monkeypatch):
    monkeypatch.delenv('LORE_AGENT_OWNER_KEY_SHA256',raising=False)
    with pytest.raises(AgentError): service.require_owner('anything')
    monkeypatch.setenv('LORE_AGENT_OWNER_KEY_SHA256',hashlib.sha256(b'synthetic').hexdigest())
    assert service.require_owner('synthetic').is_owner
    client=service.create_client('reader',['lore:read'])
    service.update_settings({'enabled':True,'mcp_enabled':True})
    p=service.principal_for_token(client['token'])
    ops=AgentOperations(service,p)
    assert 'lore_approve_draft' not in ops.tool_specs()
    with pytest.raises(AgentError): asyncio.run(ops.execute('lore_stage_character',{'fields':{'name':'N','persona':'P'}}))
    service.revoke_client(client['client']['id'])
    with pytest.raises(AgentError): asyncio.run(ops.execute('lore_capabilities',{}))
    with service.engine.connect() as c:
        assert client['token'] not in str(c.execute(text('select * from agent_clients')).all())

def test_strict_tools_bounded_reads_and_master_off(service,owner):
    from pydantic import ValidationError
    ops=AgentOperations(service,owner)
    with pytest.raises(ValidationError): asyncio.run(ops.execute('lore_search',{'limit':10000}))
    with pytest.raises(ValidationError): asyncio.run(ops.execute('lore_search',{'query':'x','sql':'delete'}))
    service.update_settings({'enabled':False})
    with pytest.raises(AgentError): asyncio.run(ops.execute('lore_capabilities',{}))

def test_session_run_acceptance_replay_and_isolation(service,owner):
    session=service.create_session(owner,'session-1')
    assert service.create_session(owner,'session-1')==session
    first=service.accept_run(owner,session['id'],'run-1','Hello',[])
    assert first['claimed'] is True
    assert service.accept_run(owner,session['id'],'run-1','Hello',[])['claimed'] is False
    with pytest.raises(AgentError): service.accept_run(owner,session['id'],'run-1','Changed',[])
    final=service.complete_run(owner,session['id'],'run-1',content='Hello back')
    assert not final['busy'] and len(final['messages'])==2
    assert service.get_operation('run-1',owner)['status']=='completed'
    outsider=Principal('other',frozenset({'lore:read'}))
    with pytest.raises(AgentError): service.get_session(session['id'],outsider)
