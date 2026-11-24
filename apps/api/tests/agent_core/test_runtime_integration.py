"""Offline real LangGraph + core integration with a deterministic model fixture."""
import asyncio
from typing import Any
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import PrivateAttr
from sqlalchemy import text


class ScriptedToolModel(BaseChatModel):
    _calls: int = PrivateAttr(default=0)
    @property
    def _llm_type(self): return 'offline-core-integration-fixture'
    def bind_tools(self,tools,**kwargs): return self
    def _generate(self,*args,**kwargs): raise AssertionError('async only')
    async def _agenerate(self,messages,stop=None,run_manager=None,**kwargs):
        self._calls+=1
        if isinstance(messages[-1],HumanMessage):
            message=AIMessage(content='',tool_calls=[{'name':'lore_stage_character','args':{'fields':{'name':'Fixture','persona':'Navigator'}},'id':'call-1','type':'tool_call'}])
        else:
            assert isinstance(messages[-1],ToolMessage)
            assert 'preview_hash' in messages[-1].content
            message=AIMessage(content='Review the staged character draft.')
        return ChatResult(generations=[ChatGeneration(message=message)])


def test_real_graph_uses_core_registry_durable_run_and_draft(service,owner,monkeypatch):
    from app.agent import runtime
    fixture=ScriptedToolModel()
    monkeypatch.setattr(runtime,'LoreAgentChatModel',lambda **kwargs:fixture)
    sid=service.create_session(owner,'framework-session')['id']
    result=asyncio.run(runtime.run_agent_turn(service,owner,sid,'framework-run','Create a navigator',[]))
    assert result['last_error'] is None
    assert result['busy'] is False
    assert len(result['messages'])==2 and len(result['drafts'])==1
    assert result['drafts'][0]['status']=='draft'
    assert fixture._calls==2
    assert service.checkpoint_path.exists()
    replay=asyncio.run(runtime.run_agent_turn(service,owner,sid,'framework-run','Create a navigator',[]))
    assert replay==result and fixture._calls==2
    with service.engine.connect() as c:
        assert c.execute(text('select count(*) from characters')).scalar_one()==0


def test_failed_tool_turn_recovers_without_replaying_private_state(service,owner,monkeypatch):
    from app.agent import runtime, models
    from app.agent.contracts import AgentSessionView
    from app.agent.service import AgentService
    calls = []
    class Model(ScriptedToolModel):
        async def _agenerate(self, messages, **kwargs):
            calls.append(messages)
            if isinstance(messages[-1], ToolMessage):
                raise RuntimeError('offline failure after staging')
            if messages[-1].content == 'Stage once':
                return await super()._agenerate(messages, **kwargs)
            return ChatResult(generations=[ChatGeneration(message=AIMessage(content='Acknowledged'))])
    monkeypatch.setattr(runtime, 'LoreAgentChatModel', lambda **kw: Model())
    sid = service.create_session(owner, 'session')['id']
    def turn(key, content):
        return asyncio.run(runtime.run_agent_turn(AgentService(service.engine), owner, sid, key, content, []))
    turn('first', 'Remember the blue coat')
    failed = turn('second', 'Stage once')
    assert failed['last_error'] and not failed['busy'] and len(failed['drafts']) == 1
    assert failed['messages'][-1]['request_id'] == 'second'
    receipt = service.get_operation('second',owner)
    assert receipt['status'] == 'failed'
    assert turn('second','Stage once') == failed and len(calls) == 3
    # Legacy C was accepted, saw pending B, and failed before model dispatch.
    service.accept_run(owner, sid, 'guard-only', 'A separate follow-up', [])
    service.complete_run(owner, sid, 'guard-only', error='Previous run ambiguous')
    guard_receipt = service.get_operation('guard-only',owner)
    assert guard_receipt['status'] == 'failed' and len(calls) == 3
    async def pending_b():
        async with runtime.AsyncSqliteSaver.from_conn_string(str(service.checkpoint_path)) as saver:
            graph = runtime.build_agent(Model(), [], saver)
            old = await graph.aget_state({'configurable': {'thread_id': f'lore:{owner.id}:{sid}'}})
            assert old.next and [m.id for m in old.values['messages'] if isinstance(m, HumanMessage)][-1] == 'second'
    asyncio.run(pending_b())
    # Pre-upgrade transcript: neither failed user message had a public key.
    with service._write() as c:
        row = service._get(c, models.sessions, sid)
        for msg in row['messages'][-2:]:
            msg.pop('request_id')
        service._put(c, models.sessions, sid, row)
    result = turn('third', 'Hello')
    assert not result['last_error'] and not result['busy'] and result['id'] == sid
    assert len(calls) == 4 and result['drafts'] == failed['drafts']
    assert [m.content for m in calls[-1][1:]] == ['Remember the blue coat','Acknowledged','Hello']
    view = AgentSessionView.model_validate(service.get_session(sid, owner))
    assert [m.request_id for m in view.messages] == ['first', None, None, None, 'third', None]
    assert service.get_operation('guard-only',owner) == guard_receipt
    assert service.get_operation('second',owner) == receipt
    assert turn('third','Hello') == result and len(calls) == 4
    turn('fourth','Hello')
    assert len(calls) == 5 and [m.content for m in calls[-1][1:]] == [
        'Remember the blue coat','Acknowledged','Hello','Acknowledged','Hello']
    assert service.get_session(sid,owner)['messages'][-2]['request_id'] == 'fourth'
    import sqlite3
    with sqlite3.connect(service.checkpoint_path) as db:
        assert db.execute('select count(distinct thread_id) from checkpoints').fetchone() == (2,)


def test_recovery_rejects_unproven_legacy_receipt(service, owner):
    import pytest
    from app.agent import models
    from app.agent.contracts import AgentError
    sid = service.create_session(owner, 'session')['id']
    service.accept_run(owner, sid, 'old', 'Hello', [])
    service.complete_run(owner, sid, 'old', error='Terminal failure')
    with service._write() as c:
        row = service._get(c, models.sessions, sid)
        row['messages'][-1].pop('request_id')
        row['messages'][-1]['content'] = 'Different'
        service._put(c, models.sessions, sid, row)
    before = service.get_session(sid, owner)
    with pytest.raises(AgentError, match='Cannot verify'):
        service.accept_run(owner, sid, 'new', 'Hello', [])
    assert service.get_session(sid, owner) == before
    assert service.get_operation('new', owner)['status'] == 'unknown'


def test_unknown_pending_checkpoint_is_not_adopted(service, owner, monkeypatch):
    from app.agent import runtime
    from app.agent.contracts import AgentError
    import pytest
    sid = service.create_session(owner, 'session')['id']
    # Real orphaned graph with no corresponding receipt or transcript.
    async def orphan():
        class Interrupted(ScriptedToolModel):
            async def _agenerate(self, *args, **kwargs):
                raise ValueError('synthetic interruption')
        path = service.checkpoint_path
        path.parent.mkdir(parents=True, exist_ok=True)
        async with runtime.AsyncSqliteSaver.from_conn_string(str(path)) as saver:
            graph = runtime.build_agent(Interrupted(), [], saver)
            config = {'configurable': {'thread_id': f'lore:{owner.id}:{sid}'}}
            with pytest.raises(ValueError, match='synthetic interruption'):
                await graph.ainvoke({'messages':[HumanMessage(content='Orphan', id='orphan')]}, config)
            assert (await graph.aget_state(config)).next
    asyncio.run(orphan())
    service.accept_run(owner, sid, 'failed', 'Prior', [])
    service.complete_run(owner, sid, 'failed', error='Terminal failure')
    model = ScriptedToolModel()
    monkeypatch.setattr(runtime, 'LoreAgentChatModel', lambda **kw: model)
    result = asyncio.run(runtime.run_agent_turn(service,owner,sid,'new','Hello',[]))
    assert result['busy'] and model._calls == 0
    assert service.get_operation('new', owner)['status'] == 'running'
    with pytest.raises(AgentError, match='busy'):
        service.accept_run(owner, sid, 'later', 'Hello', [])
