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
