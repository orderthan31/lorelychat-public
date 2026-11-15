"""Offline execution progress: real callbacks, accepted run, no private text."""
import asyncio
import json
from typing import Any
from uuid import uuid4

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import PrivateAttr
from sqlalchemy import text


def test_progress_is_closed_metadata_bound_to_running_request(service, owner):
    from app.agent.progress import AgentProgressCallback
    from app.agent.contracts import Principal
    sid = service.create_session(owner, 'progress-session')['id']
    service.accept_run(owner, sid, 'progress-run', 'private-user-sentinel', [])
    observer = AgentProgressCallback(service, owner, sid, 'progress-run')
    async def exercise():
        await observer.on_chat_model_start({'name': 'private-provider-sentinel'}, [['private-message-sentinel']], run_id=uuid4())
        first = service.get_operation('progress-run', owner)
        assert first['progress'] == {'stage': 'model', 'sequence': 1, 'round': 1}
        await observer.on_tool_start({'name': 'lore_search'}, 'private-tool-arguments-sentinel', run_id=uuid4())
        second = service.get_operation('progress-run', owner)
        assert second['progress'] == {'stage': 'search', 'sequence': 2, 'round': 1}
        await observer.on_tool_start({'name': 'private-unknown-tool-sentinel'}, 'private-input', run_id=uuid4())
        assert service.get_operation('progress-run', owner)['progress'] == second['progress']
        other = AgentProgressCallback(service, Principal('unrelated-client', frozenset(), False), sid, 'progress-run')
        await other.on_tool_start({'name': 'lore_stage_world'}, '', run_id=uuid4())
        wrong_session = AgentProgressCallback(service, owner, 'different-session', 'progress-run')
        await wrong_session.on_tool_start({'name': 'lore_stage_world'}, '', run_id=uuid4())
        assert service.get_operation('progress-run', owner)['progress'] == second['progress']
        service.complete_run(owner, sid, 'progress-run', content='Finished')
        await observer.on_chat_model_start({}, [], run_id=uuid4())
        final = service.get_operation('progress-run', owner)
        assert final['status'] == 'completed' and final['progress'] == second['progress']
        assert 'sentinel' not in json.dumps(final)
    asyncio.run(exercise())


def test_progress_failures_do_not_break_or_log_model_work(service, owner, monkeypatch, capsys, caplog):
    from app.agent.progress import AgentProgressCallback
    observer = AgentProgressCallback(service, owner, 'session', 'request')
    calls = []
    def denied():
        calls.append(True)
        raise RuntimeError('private-storage-sentinel')
    monkeypatch.setattr(service, '_write', denied)
    async def exercise():
        await observer.on_chat_model_start({}, [], run_id=uuid4())
        await observer.on_tool_start({'name': 'lore_search'}, 'private-input', run_id=uuid4())
    asyncio.run(exercise())
    assert calls == [True]
    captured = capsys.readouterr()
    assert 'sentinel' not in captured.out + captured.err + caplog.text


class ProgressFixtureModel(BaseChatModel):
    _calls: int = PrivateAttr(default=0)
    _inspect: Any = PrivateAttr()
    _observed: list = PrivateAttr(default_factory=list)
    @property
    def _llm_type(self): return 'offline-progress-fixture'
    def bind_tools(self, tools, **kwargs): return self
    def _generate(self, *args, **kwargs): raise AssertionError('async only')
    async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
        self._calls += 1
        self._observed.append(self._inspect())
        if isinstance(messages[-1], HumanMessage):
            message = AIMessage(content='', tool_calls=[{'name': 'lore_stage_character', 'args': {'fields': {'name': 'Progress fixture', 'persona': 'Synthetic navigator'}}, 'id': 'progress-tool', 'type': 'tool_call'}])
        else:
            assert isinstance(messages[-1], ToolMessage)
            message = AIMessage(content='The draft is ready for review.')
        return ChatResult(generations=[ChatGeneration(message=message)])


def test_real_graph_publishes_actual_stages_and_replay_is_readonly(service, owner, monkeypatch):
    from app.agent import runtime
    sid = service.create_session(owner, 'graph-progress-session')['id']
    fixture = ProgressFixtureModel()
    fixture._inspect = lambda: service.get_operation('graph-progress-run', owner).get('progress')
    monkeypatch.setattr(runtime, 'LoreAgentChatModel', lambda **kwargs: fixture)
    tool_stages = []
    original_stage = service.create_draft
    def stage(*args, **kwargs):
        tool_stages.append(service.get_operation('graph-progress-run', owner).get('progress'))
        return original_stage(*args, **kwargs)
    monkeypatch.setattr(service, 'create_draft', stage)
    result = asyncio.run(runtime.run_agent_turn(service, owner, sid, 'graph-progress-run', 'Create a navigator', []))
    assert result['last_error'] is None and result['busy'] is False
    assert fixture._calls == 2
    assert [item['stage'] for item in fixture._observed] == ['model', 'model']
    assert [item['round'] for item in fixture._observed] == [1, 2]
    assert [item['stage'] for item in tool_stages] == ['draft']
    sequences = [fixture._observed[0]['sequence'], tool_stages[0]['sequence'], fixture._observed[1]['sequence']]
    assert sequences == sorted(set(sequences))
    final = service.get_operation('graph-progress-run', owner)
    assert final['status'] == 'completed' and final['progress']['stage'] == 'model'
    replay = asyncio.run(runtime.run_agent_turn(service, owner, sid, 'graph-progress-run', 'Create a navigator', []))
    assert replay == result and fixture._calls == 2
    assert service.get_operation('graph-progress-run', owner) == final
    with service.engine.connect() as connection:
        assert connection.execute(text('select count(*) from characters')).scalar_one() == 0
