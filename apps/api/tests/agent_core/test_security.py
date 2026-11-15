import asyncio
import pytest
from sqlalchemy import text
from sqlmodel import Session
from app.agent.contracts import AgentError
from app.agent.operations import AgentOperations
from app.db.models import Character
from test_core import commit, approve


def test_external_request_does_not_approve_and_owner_sees_session(service,owner):
    service.update_settings({'enabled':True,'mcp_enabled':True})
    issued=service.create_client('editor',['lore:read','lore:draft','lore:commit'])
    external=service.principal_for_token(issued['token'])
    sid=service.create_session(external,'ext-session')['id']
    ops=AgentOperations(service,external,sid)
    d=asyncio.run(ops.execute('lore_stage_character',{'fields':{'name':'External','persona':'Draft'}}))
    requested=asyncio.run(ops.execute('lore_request_approval',{'draft_id':d['id']}))
    assert requested['approval_required'] and requested['draft']['status']=='draft'
    assert service.get_draft(d['id'],external)['warnings']
    assert any(s['id']==sid for s in service.list_sessions(owner))
    with pytest.raises(AgentError): approve(service,external,d)
    with pytest.raises(AgentError): commit(service,external,d)
    approve(service,owner,d)
    result=commit(service,external,d)
    assert result['status']=='completed'
    other=service.create_client('other',['lore:read','lore:draft','lore:commit'])
    other_principal=service.principal_for_token(other['token'])
    with pytest.raises(AgentError): service.get_draft(d['id'],other_principal)
    assert service.get_operation('commit-1',other_principal)['status']=='unknown'
    service.revoke_client(issued['client']['id'])
    assert service.principal_for_token(issued['token']) is None
    with pytest.raises(AgentError): asyncio.run(ops.execute('lore_search',{}))


def test_dto_read_is_not_a_write_and_unknown_column_writer_bumps(service,owner):
    with Session(service.engine) as s:
        s.add(Character(id='c',name='C',persona='P',trait_scores={})); s.commit()
    with service.engine.begin() as c:
        c.execute(text("alter table characters add column authored_future TEXT default 'keep'"))
        c.execute(text("update characters set authored_future='changed' where id='c'"))
    for _ in range(3):
        dto=service.get_entity(owner,'character','c')
        assert dto['fields']['trait_scores']['confidence']==3
    with service.engine.connect() as c:
        row=c.execute(text("select trait_scores,agent_revision,authored_future from characters where id='c'")).one()
        assert tuple(row)==('{}',1,'changed')


def test_invalid_context_and_busy_request_never_accept(service,owner):
    sid=service.create_session(owner,'s')['id']
    with pytest.raises(AgentError):
        service.accept_run(owner,sid,'bad','Hello',[{'kind':'character','id':'missing','fields':['name']}])
    assert service.get_session(sid,owner)['messages']==[]
    assert service.get_operation('bad',owner)['status']=='unknown'
    service.accept_run(owner,sid,'first','Hello',[])
    with pytest.raises(AgentError): service.accept_run(owner,sid,'second','Another',[])
    assert service.get_operation('second',owner)['status']=='unknown'
    assert len(service.get_session(sid,owner)['messages'])==1
