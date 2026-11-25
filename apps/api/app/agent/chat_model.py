"""Native-tool LangChain adapter. Credentials exist only inside a dispatch.

No roleplay parser, action parser, retry loop, ambient credential routing or
provider HTTP serialization lives here. LiteLLM owns provider translations.
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
import json
from types import SimpleNamespace
from typing import Any, Callable

import httpx
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.messages.utils import convert_to_openai_messages
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.utils.function_calling import convert_to_openai_tool
from pydantic import PrivateAttr

from app.agent.diagnostics import failure
from app.engine import litellm_transport as transport


class AgentModelError(RuntimeError):
    """Deliberately content-free error safe for callbacks and session views."""


def wire_messages(messages: list[BaseMessage]) -> list[dict]:
    result = []
    for message in messages:
        if isinstance(message, AIMessage) and '_lore_native' in message.additional_kwargs:
            native = deepcopy(message.additional_kwargs['_lore_native'])
            native['role'] = 'assistant'
            result.append(native)
        else:
            result.append(convert_to_openai_messages(message))
    # Correlation errors are rejected locally, never repaired with invented IDs.
    pending: set[str] = set()
    for message in result:
        if message['role'] == 'tool':
            call_id = message.get('tool_call_id')
            if call_id not in pending:
                raise AgentModelError('Invalid tool result correlation')
            pending.remove(call_id)
        else:
            if pending:
                raise AgentModelError('Missing tool results')
            pending = {call['id'] for call in message.get('tool_calls', [])}
    if pending:
        raise AgentModelError('Missing tool results')
    return result


def response_message(response: Any, tool_names: set[str]) -> AIMessage:
    """Validate the SDK's native tool envelope, retaining continuation fields."""
    try:
        data = response.model_dump() if hasattr(response, 'model_dump') else response
        if len(data['choices']) != 1:
            raise ValueError()
        choice = data['choices'][0]
        if choice.get('finish_reason') not in {'stop', 'tool_calls', 'function_call'}:
            raise ValueError()
        native = choice['message']
        content = native.get('content') or ''
        if not isinstance(content, str) or len(content) > 100_000:
            raise ValueError()
        calls = []
        ids = set()
        native_calls = native.get('tool_calls') or []
        if not isinstance(native_calls, list) or len(native_calls) > 12:
            raise ValueError()
        for call in native_calls:
            function = call['function']
            call_id, name = call['id'], function['name']
            if (call.get('type') != 'function' or not isinstance(call_id, str)
                    or not call_id or call_id in ids or name not in tool_names):
                raise ValueError()
            raw_args = function['arguments']
            if not isinstance(raw_args, str) or len(raw_args) > 32_000:
                raise ValueError()
            args = json.loads(raw_args)
            if not isinstance(args, dict):
                raise ValueError()
            ids.add(call_id)
            calls.append({'name': name, 'args': args, 'id': call_id, 'type': 'tool_call'})
        if not calls and not content.strip():
            raise ValueError()
        # Do not retain SDK hidden params, request headers, URLs or arbitrary
        # response metadata. These are the SDK's provider continuation fields.
        retained = {key: deepcopy(native[key]) for key in (
            'content', 'tool_calls', 'provider_specific_fields', 'thinking_blocks',
            'reasoning_content',
        ) if native.get(key) is not None}
        return AIMessage(content=content, tool_calls=calls,
                         additional_kwargs={'_lore_native': retained})
    except (KeyError, TypeError, ValueError, AttributeError):
        raise AgentModelError('Invalid provider tool response') from None


async def vertex_tool_completion(route, *, messages, parameters, client, timeout):
    """Request-owned sibling of the text-only Vertex driver; no shared auth cache."""
    from app.engine import vertex_auth
    from litellm.llms.vertex_ai.gemini.vertex_and_google_ai_studio_gemini import VertexLLM, VertexGeminiConfig
    if route.model in vertex_auth.TTS_MODELS or route.vertex_location in {'us', 'eu'}:
        raise AgentModelError('Selected Vertex route is not tool-capable')
    sdk = transport.load_sdk()
    optional = VertexGeminiConfig().map_openai_params(
        deepcopy(parameters), {}, route.model, drop_params=False)
    try:
        token = await vertex_auth.acquire_token(route, timeout)
    except Exception as exc:
        raise failure('MODEL_AUTH', exc).wrap(AgentModelError) from None

    class OwnedVertex(VertexLLM):
        async def _ensure_access_token_async(self, **kwargs):
            return token, route.vertex_project

        @staticmethod
        def get_vertex_region(vertex_region, model):
            return vertex_region

    log = SimpleNamespace(pre_call=lambda **kw: None, post_call=lambda **kw: None, optional_params={})
    return await OwnedVertex().completion(
        model=route.model, messages=deepcopy(messages), model_response=sdk.ModelResponse(),
        print_verbose=lambda *a, **kw: None, custom_llm_provider='vertex_ai',
        encoding=sdk.encoding, logging_obj=log, optional_params=optional,
        acompletion=True, timeout=timeout, vertex_project=route.vertex_project,
        vertex_location=route.vertex_location, vertex_credentials=None, gemini_api_key=None,
        litellm_params={}, client=client, api_base=None,
    )


async def native_tool_completion(route, *, messages, tools, timeout=45.0,
                                 before_send: Callable[[], None] = lambda: None):
    """One physical send; request-owned SDK client; no SDK fallback/retry."""
    from openai import AsyncOpenAI
    from litellm.llms.custom_httpx.http_handler import AsyncHTTPHandler
    sdk = transport.load_sdk()
    if not route.api_key and route.sdk_provider != 'vertex_ai':
        raise AgentModelError('Selected account credential is required')
    guard = transport.SingleSendTransport(transport.new_http_transport(), before_send)
    client = None
    try:
        async with asyncio.timeout(timeout):
            if route.sdk_provider == 'openai':
                raw = httpx.AsyncClient(transport=guard, trust_env=False,
                                       follow_redirects=False, timeout=timeout)
                client = AsyncOpenAI(api_key=route.api_key, base_url=route.base_url,
                                     http_client=raw, max_retries=0, _strict_response_validation=True)
            else:
                client = AsyncHTTPHandler(transport=guard, timeout=timeout, follow_redirects=False)
            params: dict[str, Any] = {'max_tokens': 4096}
            if tools:
                params.update(tools=deepcopy(tools), tool_choice='auto')
            before_send()
            if route.sdk_provider == 'vertex_ai':
                response = await vertex_tool_completion(route, messages=messages,
                    parameters=params, client=client, timeout=timeout)
            else:
                response = await sdk.acompletion(
                    model=route.model, custom_llm_provider=route.sdk_provider,
                    api_base=route.base_url, api_key=route.api_key, client=client,
                    messages=deepcopy(messages), stream=False, timeout=timeout,
                    num_retries=0, max_retries=0, drop_params=False, caching=False,
                    **params,
                )
            if not guard.sent or guard.denied or guard.error is not None:
                raise AgentModelError('Provider dispatch did not complete')
            return response
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        raise failure('MODEL_REQUEST' if guard.sent else 'MODEL_DISPATCH', exc, guard).wrap(AgentModelError) from None
    finally:
        if client is not None:
            await transport.close_owned(client.close)
        await transport.close_owned(guard.aclose)


class LoreAgentChatModel(BaseChatModel):
    """Minimal BaseChatModel contract; resolver/guard are never serialized."""
    _resolve_route: Callable = PrivateAttr()
    _before_dispatch: Callable = PrivateAttr()
    _dispatch: Callable = PrivateAttr()

    def __init__(self, *, resolve_route, before_dispatch, dispatch=native_tool_completion, **kwargs):
        super().__init__(cache=False, callbacks=None, **kwargs)
        self._resolve_route = resolve_route
        self._before_dispatch = before_dispatch
        self._dispatch = dispatch

    @property
    def _llm_type(self) -> str:
        return 'lore-agent-byok'

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        raise NotImplementedError('Lore Agent requires asynchronous dispatch')

    def bind_tools(self, tools, *, tool_choice=None, **kwargs):
        if tool_choice not in (None, 'auto') or kwargs:
            raise AgentModelError('Unsupported tool binding option')
        return self.bind(tools=[convert_to_openai_tool(tool) for tool in tools])

    async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
        if stop or set(kwargs) - {'tools'}:
            raise AgentModelError('Unsupported model request option')
        stage = 'MODEL_SETUP'
        try:
            self._before_dispatch()
            route = self._resolve_route()
            tools = kwargs.get('tools') or []
            wire = wire_messages(messages)
            stage = 'MODEL_REQUEST'
            response = await self._dispatch(route, messages=wire, tools=tools,
                                            before_send=self._before_dispatch)
            stage = 'MODEL_RESPONSE'
            message = response_message(response, {tool['function']['name'] for tool in tools})
            return ChatResult(generations=[ChatGeneration(message=message)])
        except Exception as exc:
            raise failure(stage, exc).wrap(AgentModelError) from None
