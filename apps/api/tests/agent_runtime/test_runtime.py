import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import StructuredTool
from pydantic import BaseModel

from app.agent.chat_model import (AgentModelError, LoreAgentChatModel,
    native_tool_completion, response_message, wire_messages)
from app.engine import litellm_transport as transport

TOOL = {'type': 'function', 'function': {'name': 'lookup', 'description': 'Read selected summary',
        'parameters': {'type': 'object', 'properties': {'query': {'type': 'string'}}, 'required': ['query']}}}


def answer(tool=False):
    message = {'role': 'assistant', 'content': None if tool else 'Finished'}
    if tool:
        message['tool_calls'] = [{'id': 'native_123', 'type': 'function',
            'function': {'name': 'lookup', 'arguments': '{"query":"fixture"}'}}]
    return {'id': 'resp-fixture', 'object': 'chat.completion', 'created': 1, 'model': 'gpt-4o',
            'choices': [{'index': 0, 'finish_reason': 'tool_calls' if tool else 'stop', 'message': message}],
            'usage': {'prompt_tokens': 2, 'completion_tokens': 3, 'total_tokens': 5}}


def test_native_ids_and_continuation_roundtrip():
    data = answer(True)
    data['choices'][0]['message']['tool_calls'][0]['provider_specific_fields'] = {'thought_signature': 'opaque-synthetic'}
    data['choices'][0]['message']['thinking_blocks'] = [{'type': 'thinking', 'thinking': 'private', 'signature': 'synthetic'}]
    message = response_message(data, {'lookup'})
    wire = wire_messages([HumanMessage(content='test'), message,
                          ToolMessage(content='summary', tool_call_id='native_123')])
    assert wire[1]['tool_calls'][0]['provider_specific_fields']['thought_signature'] == 'opaque-synthetic'
    assert wire[1]['thinking_blocks'][0]['signature'] == 'synthetic'
    assert wire[2]['tool_call_id'] == 'native_123'
    with pytest.raises(AgentModelError):
        wire_messages([message, ToolMessage(content='x', tool_call_id='wrong')])


@pytest.mark.parametrize('mutation', ['badargs', 'duplicate', 'unbound', 'empty', 'truncated'])
def test_malformed_tools_rejected(mutation):
    data = answer(True)
    choice = data['choices'][0]
    call = choice['message']['tool_calls'][0]
    if mutation == 'badargs': call['function']['arguments'] = '[]'
    if mutation == 'duplicate': choice['message']['tool_calls'].append(call)
    if mutation == 'unbound': call['function']['name'] = 'approve'
    if mutation == 'empty': call['id'] = ''
    if mutation == 'truncated': choice['finish_reason'] = 'length'
    with pytest.raises(AgentModelError): response_message(data, {'lookup'})


def test_real_openai_sdk_tool_roundtrip(monkeypatch):
    calls = []
    def wire(request):
        calls.append(json.loads(request.content))
        assert request.headers['authorization'] == 'Bearer synthetic-key'
        return httpx.Response(200, json=answer(len(calls) == 1))
    monkeypatch.setattr(transport, 'new_http_transport', lambda: httpx.MockTransport(wire))
    route = transport.Route('openai', 'openai', 'gpt-4o', 'https://api.openai.com/v1', 'synthetic-key')
    async def exercise():
        model = LoreAgentChatModel(resolve_route=lambda: route, before_dispatch=lambda: None)
        bound = model.bind_tools([TOOL])
        first = await bound.ainvoke([HumanMessage(content='test')])
        assert first.tool_calls[0]['id'] == 'native_123'
        final = await bound.ainvoke([HumanMessage(content='test'), first,
                                   ToolMessage(content='fixture', tool_call_id='native_123')])
        assert final.content == 'Finished'
        assert 'synthetic-key' not in str(model) + json.dumps(model.to_json(), default=str)
    asyncio.run(exercise())
    assert len(calls) == 2
    assert calls[1]['messages'][-1]['tool_call_id'] == 'native_123'


def test_sdk_errors_sanitized_and_not_retried(monkeypatch, capsys, caplog):
    calls = []
    def wire(request):
        calls.append(request)
        return httpx.Response(503, json={'error': {'message': 'synthetic-secret prompt contents'}})
    monkeypatch.setattr(transport, 'new_http_transport', lambda: httpx.MockTransport(wire))
    route = transport.Route('openai', 'openai', 'gpt-4o', 'https://api.openai.com/v1', 'synthetic-key')
    async def exercise():
        with pytest.raises(AgentModelError) as caught:
            await native_tool_completion(route, messages=[{'role': 'user', 'content': 'test'}], tools=[TOOL])
        assert 'synthetic' not in str(caught.value)
        assert caught.value.__suppress_context__
    asyncio.run(exercise())
    assert len(calls) == 1
    captured = capsys.readouterr()
    assert 'synthetic-secret' not in captured.out + captured.err + caplog.text


@pytest.mark.parametrize('provider,sdk_provider,base,model', [
    ('google', 'gemini', 'https://generativelanguage.googleapis.com/v1beta', 'gemini-2.5-flash'),
    ('gemini', 'gemini', 'https://generativelanguage.googleapis.com/v1beta', 'gemini-2.5-flash'),
    ('anthropic', 'anthropic', 'https://api.anthropic.com', 'claude-sonnet-4-20250514'),
    ('xai', 'xai', 'https://api.x.ai/v1', 'grok-3'),
    ('openrouter', 'openrouter', 'https://openrouter.ai/api/v1', 'openai/gpt-4o'),
    ('openai_compatible', 'openai', 'http://localhost:9999/v1', 'local-fixture'),
    ('vertex_ai', 'vertex_ai', 'https://us-central1-aiplatform.googleapis.com/v1', 'gemini-2.5-flash'),
])
def test_provider_native_tool_wires(monkeypatch, provider, sdk_provider, base, model):
    calls = []
    def wire(request):
        calls.append(json.loads(request.content))
        first = len(calls) == 1
        if sdk_provider in {'gemini', 'vertex_ai'}:
            part = {'functionCall': {'name': 'lookup', 'args': {'query': 'fixture'}}, 'thoughtSignature': 'opaque-synthetic'} if first else {'text': 'Finished'}
            data = {'candidates': [{'index': 0, 'content': {'role': 'model', 'parts': [part]}, 'finishReason': 'STOP'}],
                    'usageMetadata': {'promptTokenCount': 2, 'candidatesTokenCount': 3, 'totalTokenCount': 5}}
        elif sdk_provider == 'anthropic':
            data = {'id': 'msg-fixture', 'type': 'message', 'role': 'assistant', 'model': model,
                    'stop_reason': 'tool_use' if first else 'end_turn', 'stop_sequence': None,
                    'content': [{'type': 'thinking', 'thinking': 'private synthetic reasoning', 'signature': 'opaque-anthropic'},
                                {'type': 'tool_use', 'id': 'native_123', 'name': 'lookup', 'input': {'query': 'fixture'}}] if first else [{'type': 'text', 'text': 'Finished'}],
                    'usage': {'input_tokens': 2, 'output_tokens': 3}}
        else:
            data = answer(first)
        return httpx.Response(200, json=data)
    monkeypatch.setattr(transport, 'new_http_transport', lambda: httpx.MockTransport(wire))
    if provider == 'vertex_ai':
        from app.engine import vertex_auth
        async def token(*args): return 'synthetic-oauth'
        monkeypatch.setattr(vertex_auth, 'acquire_token', token)
    route = transport.Route(provider, sdk_provider, model, base, 'synthetic-key', 'synthetic-project', 'us-central1', 'service_account')
    async def exercise():
        bound = LoreAgentChatModel(resolve_route=lambda: route, before_dispatch=lambda: None).bind_tools([TOOL])
        first = await bound.ainvoke([HumanMessage(content='test')])
        assert first.tool_calls[0]['name'] == 'lookup'
        final = await bound.ainvoke([HumanMessage(content='test'), first,
            ToolMessage(content='fixture', tool_call_id=first.tool_calls[0]['id'])])
        assert final.content == 'Finished'
    asyncio.run(exercise())
    assert len(calls) == 2
    if sdk_provider == 'anthropic':
        assert 'opaque-anthropic' in json.dumps(calls[1])
        assert 'tool_result' in json.dumps(calls[1])
    if sdk_provider in {'gemini', 'vertex_ai'}:
        assert 'opaque-synthetic' in json.dumps(calls[1])
        assert 'function_response' in json.dumps(calls[1])


def test_standard_call_limit_and_master_off():
    from app.agent.runtime import build_agent
    from langgraph.checkpoint.memory import InMemorySaver
    class Query(BaseModel):
        query: str
    calls = []
    async def lookup(query): return 'fixture'
    async def dispatch(route, *, messages, tools, before_send):
        before_send()
        calls.append(1)
        data = answer(True)
        data['choices'][0]['message']['tool_calls'][0]['id'] = f'call-{len(calls)}'
        return data
    model = LoreAgentChatModel(resolve_route=lambda: object(), before_dispatch=lambda: None, dispatch=dispatch)
    tool = StructuredTool.from_function(coroutine=lookup, name='lookup', description='Search', args_schema=Query)
    async def exercise():
        graph = build_agent(model, [tool], InMemorySaver())
        with pytest.raises(Exception):
            await graph.ainvoke({'messages': [HumanMessage(content='test')]},
                {'configurable': {'thread_id': 'limited'}, 'recursion_limit': 40})
        assert len(calls) == 6
        def disabled(): raise ValueError('master off')
        off = LoreAgentChatModel(resolve_route=lambda: object(), before_dispatch=disabled, dispatch=dispatch)
        with pytest.raises(AgentModelError):
            await off.bind_tools([TOOL]).ainvoke([HumanMessage(content='test')])
        assert len(calls) == 6
    asyncio.run(exercise())


def test_standard_create_agent_persistent_tool_roundtrip(tmp_path):
    from app.agent.runtime import build_agent
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
    class Query(BaseModel):
        query: str
    seen = []
    async def lookup(query):
        seen.append(query)
        return 'fixture'
    async def dispatch(route, *, messages, tools, before_send):
        before_send()
        return answer(messages[-1]['role'] != 'tool')
    model = LoreAgentChatModel(resolve_route=lambda: object(), before_dispatch=lambda: None, dispatch=dispatch)
    tool = StructuredTool.from_function(coroutine=lookup, name='lookup', description='Search', args_schema=Query)
    config = {'configurable': {'thread_id': 'stable-thread'}, 'callbacks': []}
    async def exercise():
        path = str(tmp_path / 'checkpoints.sqlite')
        async with AsyncSqliteSaver.from_conn_string(path) as saver:
            graph = build_agent(model, [tool], saver)
            result = await graph.ainvoke({'messages': [HumanMessage(content='test')]}, config)
            assert result['messages'][-1].content == 'Finished'
        async with AsyncSqliteSaver.from_conn_string(path) as saver:
            graph = build_agent(model, [tool], saver)
            state = await graph.aget_state(config)
            assert state.values['messages'][-1].content == 'Finished'
            assert not state.next
            tool_result = next(m for m in state.values['messages'] if isinstance(m, ToolMessage))
            assert tool_result.tool_call_id == 'native_123'
    asyncio.run(exercise())
    assert seen == ['fixture']
