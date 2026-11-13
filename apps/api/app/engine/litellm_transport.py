"""SDK-only request translation with a single-send, original-response boundary.

No routing policy, credentials, clients or response caches are shared between calls.
The application owns retries; the SDK owns provider request serialization.
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import dataclass, field
import logging
import json
import math
import os
import threading
from urllib.parse import urlsplit

import httpx

from app.engine.provider_capabilities import safe_count, safe_model_version


from app.engine.llm_error_types import LLMConfigurationError, LLMResponseEvidenceError, LLMPolicyError
from app.engine.llm_transport_deadline import bounded_close, remaining_seconds


class TransportConfigurationError(LLMConfigurationError):
    """Pre-dispatch configuration failure; never an availability/fallback signal."""


class ResponseEvidenceError(LLMResponseEvidenceError):
    """A response is not a supported, bounded native text reply."""


class AdditionalSendDenied(Exception):
    pass


# Compatibility identifiers, not model-family dispatch. Native custom endpoints
# need an explicit compatible account rather than an implicit protocol guess.
_ROUTES = {
    'google': ('gemini', 'https://generativelanguage.googleapis.com/v1beta'),
    'gemini': ('gemini', 'https://generativelanguage.googleapis.com/v1beta'),
    'openai': ('openai', 'https://api.openai.com/v1'),
    'xai': ('xai', 'https://api.x.ai/v1'),
    'openrouter': ('openrouter', 'https://openrouter.ai/api/v1'),
    'anthropic': ('anthropic', 'https://api.anthropic.com'),
    'openai_compatible': ('openai', None),
}


@dataclass(frozen=True)
class Route:
    provider: str
    sdk_provider: str
    model: str
    base_url: str
    api_key: str = field(repr=False)
    vertex_project: str | None = None
    vertex_location: str | None = None
    vertex_auth_mode: str | None = None


def resolve_route(client) -> Route:
    provider = client.provider()
    if provider == 'vertex_ai':
        from app.engine import vertex_auth
        values = client.overrides
        project, location = values.get('vertex_project'), values.get('vertex_location')
        mode = values.get('vertex_auth_mode') or 'service_account'
        try:
            vertex_auth.validate_config(project, location, mode, values.get('base_url'))
            vertex_auth.validate_model(client.model())
            if not values.get('provider_account_id'):
                raise ValueError()
            key = values.get('api_key')
            if mode == 'service_account':
                vertex_auth.service_account_info(key)
            elif key:
                raise ValueError()
        except ValueError:
            raise TransportConfigurationError('Invalid Vertex account configuration') from None
        host = vertex_auth.endpoint_host(location)
        return Route(provider, 'vertex_ai', client.model(), f'https://{host}/v1', key or '', project, location, mode)
    record = _ROUTES.get(provider)
    if record is None:
        raise TransportConfigurationError('Unsupported LLM provider')
    sdk_provider, default = record
    dynamic = client.overrides.get('provider_account_id') is not None
    base = client.overrides.get('base_url')
    if base is None and not dynamic and default is None:
        base = client.base_url()
    base = default if base is None else base
    if not isinstance(base, str) or not base.strip():
        raise TransportConfigurationError('Explicit compatible endpoint is required')
    base = base.rstrip('/')
    try:
        parsed = urlsplit(base)
        valid = (parsed.scheme in ('https', 'http') and parsed.hostname and parsed.port != 0
                 and parsed.username is None and parsed.password is None
                 and '?' not in base and '#' not in base)
    except ValueError:
        valid = False
    if not valid or (default is not None and base != default):
        raise TransportConfigurationError('Unsupported or ambiguous LLM endpoint')
    model = client.model()
    if not isinstance(model, str) or not model.strip():
        raise TransportConfigurationError('Explicit model is required')
    if dynamic:
        key = client.overrides.get('api_key')
    else:
        try:
            key = client.api_key()
        except RuntimeError:
            key = None
    # Dynamic keyless routing requires both an explicit capability and a local
    # endpoint. Never inherit ambient cloud credentials or use a remote placeholder.
    from app.engine.provider_capabilities import is_local_endpoint
    if not key and provider == 'openai_compatible' and is_local_endpoint(base) and (
        not dynamic or client.overrides.get('keyless_local') is True
    ):
        key = 'not-needed-for-local'
    if not isinstance(key, str) or not key.strip():
        raise TransportConfigurationError('Selected account credential is required')
    return Route(provider, sdk_provider, model, base, key)


_SDK = None
_INIT_LOCK = threading.Lock()


def load_sdk():
    global _SDK
    with _INIT_LOCK:
        if _SDK is None:
            os.environ['LITELLM_LOCAL_MODEL_COST_MAP'] = 'True'
            # SDK exceptions can include full prompts/keys. Never emit SDK logs.
            for name in ('LiteLLM', 'LiteLLM Router', 'LiteLLM Proxy'):
                logging.getLogger(name).disabled = True
            import litellm
            litellm.telemetry = False
            litellm.callbacks = []
            litellm.input_callback = []
            litellm.success_callback = []
            litellm.failure_callback = []
            litellm._async_success_callback = []
            litellm._async_failure_callback = []
            litellm.model_fallbacks = None
            litellm.num_retries = 0
            litellm.drop_params = False
            litellm.suppress_debug_info = True
            litellm.set_verbose = False
            litellm.log_raw_request_response = False
            _SDK = litellm
    return _SDK


def new_http_transport() -> httpx.AsyncBaseTransport:
    return httpx.AsyncHTTPTransport(retries=0, trust_env=False)


async def close_owned(close):
    await bounded_close(close)


class SingleSendTransport(httpx.AsyncBaseTransport):
    max_response_bytes = 8 * 1024 * 1024

    def __init__(self, inner, reserve):
        self.inner = inner
        self.reserve = reserve
        self.sent = False
        self.denied = False
        self.response = None
        self.error = None
        self.closed = False
        self.wire_controls = {}

    async def handle_async_request(self, request):
        if self.sent:
            self.denied = True
            raise AdditionalSendDenied('Additional SDK send denied')
        try:
            self.reserve()
        except Exception as exc:
            self.error = exc
            raise
        self.sent = True  # Reserve before the first await, including concurrent sends.
        # Only allowlisted scalar controls survive; never messages/schema/keys.
        try:
            self.wire_controls = control_fields(json.loads(request.content))
        except (ValueError, TypeError, UnicodeError):
            self.wire_controls = {}
        try:
            response = await self.inner.handle_async_request(request)
            try:
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    if len(body) + len(chunk) > self.max_response_bytes:
                        raise ResponseEvidenceError('Provider response exceeds capture limit', http_status=response.status_code)
                    body.extend(chunk)
                self.response = httpx.Response(
                    response.status_code, content=bytes(body), request=request,
                    headers=[(k, v) for k, v in response.headers.multi_items()
                             if k.lower() in ('warning', 'retry-after', 'content-type')],
                )
                return self.response
            finally:
                await close_owned(response.aclose)
        except Exception as exc:
            self.error = exc
            raise

    async def aclose(self):
        if not self.closed:
            self.closed = True
            await self.inner.aclose()


def response_format_copy(value, *, strict):
    value = deepcopy(value)
    if value and value.get('type') == 'json_object' and isinstance(value.get('schema'), dict):
        return {'type': 'json_schema', 'json_schema': {
            'name': value.get('name') or 'character_chat_replies',
            'strict': value.get('strict', strict), 'schema': value['schema'],
        }}
    return value


def metadata_object(value):
    return value if isinstance(value, dict) else {}


# Spelling aliases, not provider/model capability policy. Wire stays untouched.
_CONTROL_PATHS = {
    'temperature': ('temperature',),
    'top_p': ('top_p', 'topP'),
    'top_k': ('top_k', 'topK'),
    'max_tokens': ('max_tokens', 'max_completion_tokens', 'max_output_tokens', 'maxOutputTokens'),
    'reasoning_effort': ('reasoning_effort',),
    'thinkingConfig.thinkingBudget': ('thinkingConfig.thinkingBudget', 'thinking_budget'),
    'thinkingConfig.thinkingLevel': ('thinkingConfig.thinkingLevel', 'thinking_level'),
    'reasoning.enabled': ('reasoning.enabled',),
    'response_format.strict': ('response_format.json_schema.strict',),
}


def control_fields(payload):
    result = {}
    payload = metadata_object(payload)
    for root in (payload, metadata_object(payload.get('generationConfig')), metadata_object(payload.get('extra_body'))):
        for name, paths in _CONTROL_PATHS.items():
            for path in paths:
                value = root
                for part in path.split('.'):
                    value = metadata_object(value).get(part)
                if (type(value) in (bool, int) or
                    (type(value) is float and math.isfinite(value)) or
                    (type(value) is str and value in {'none', 'off', 'minimal', 'low', 'medium', 'high', 'xhigh'})):
                    result[name] = value
    return result


def control_diagnostics(response, parameters, requested, omitted):
    app = control_fields(parameters)
    wire = response.extensions['lorechat_wire_controls']
    aliases = {'topP': 'top_p', 'topK': 'top_k'}
    absent = [name for name in omitted if aliases.get(name, name) not in wire]
    return {
        'requested_controls': control_fields(requested),
        'application_controls': app,
        'application_omitted_parameters': list(omitted),
        'serialized_wire_controls': wire,
        'sdk_added_defaults': {key: value for key, value in wire.items() if key not in app},
        'omitted_parameters': absent,
        'sdk_response_status': response.extensions['lorechat_sdk_response_status'],
    }


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Ambiguous native object')
        result[key] = value
    return result


def _reject_non_json_constant(value):
    raise ValueError('Non-JSON native constant')


def native_observation(response, route):
    """Read native response fields only; never construct a provider request here."""
    try:
        if not response.is_success:
            raise ValueError
        data = json.loads(response.content, object_pairs_hook=_unique_object,
                          parse_constant=_reject_non_json_constant)
        if not isinstance(data, dict) or 'error' in data:
            raise ValueError
        # Only explicit native safety/refusal evidence, never prose heuristics.
        if route.sdk_provider in ('gemini', 'vertex_ai'):
            feedback = metadata_object(data.get('promptFeedback'))
            candidates_for_policy = data.get('candidates')
            if 'candidates' in data and not isinstance(candidates_for_policy, list):
                raise ValueError
            blocked = (isinstance(feedback.get('blockReason'), str) and bool(feedback['blockReason'])) or (
                isinstance(candidates_for_policy, list) and any(
                    isinstance(c, dict) and c.get('finishReason') in {
                        'SAFETY', 'RECITATION', 'BLOCKLIST', 'PROHIBITED_CONTENT',
                        'SPII', 'MODEL_ARMOR', 'IMAGE_SAFETY'} for c in candidates_for_policy))
        elif route.sdk_provider == 'anthropic':
            blocked = data.get('stop_reason') == 'refusal'
        else:
            choices_for_policy = data.get('choices')
            blocked = isinstance(choices_for_policy, list) and any(
                isinstance(c, dict) and (c.get('finish_reason') == 'content_filter' or
                    (isinstance(metadata_object(c.get('message')).get('refusal'), str) and
                     bool(metadata_object(c.get('message'))['refusal'])))
                for c in choices_for_policy)
        if blocked:
            raise LLMPolicyError()
        if route.sdk_provider in ('gemini', 'vertex_ai'):
            candidates = data.get('candidates')
            if 'candidates' not in data:
                feedback = metadata_object(data.get('promptFeedback'))
                if not isinstance(feedback.get('blockReason'), str) or not feedback['blockReason']:
                    raise ValueError
                candidates = []
            if not isinstance(candidates, list) or len(candidates) > 1:
                raise ValueError
            candidate = candidates[0] if candidates else {}
            if not isinstance(candidate, dict) or (candidates and not candidate):
                raise ValueError
            if 'content' in candidate:
                content = candidate['content']
                if not isinstance(content, dict) or content.get('role', 'model') != 'model':
                    raise ValueError
                parts = content.get('parts')
            else:
                if candidates and not isinstance(candidate.get('finishReason'), str):
                    raise ValueError
                parts = []
            if not isinstance(parts, list) or any(not isinstance(p, dict) or
                not isinstance(p.get('text'), str) or
                not set(p) <= {'text', 'thought', 'thoughtSignature'} or
                ('thought' in p and type(p['thought']) is not bool) for p in parts):
                raise ValueError
            text = ''.join(p.get('text', '') for p in parts if not p.get('thought')).strip()
            usage = metadata_object(data.get('usageMetadata'))
            result = dict(content=text, finish_reason=candidate.get('finishReason'),
                          reported_model_version=safe_model_version(data.get('modelVersion')),
                          prompt_tokens=safe_count(usage.get('promptTokenCount')),
                          completion_tokens=safe_count(usage.get('candidatesTokenCount')),
                          total_tokens=safe_count(usage.get('totalTokenCount')),
                          cached_tokens=safe_count(usage.get('cachedContentTokenCount')),
                          reasoning_tokens=safe_count(usage.get('thoughtsTokenCount')))
        elif route.sdk_provider == 'anthropic':
            if data.get('type', 'message') != 'message' or data.get('role', 'assistant') != 'assistant':
                raise ValueError
            parts = data['content']
            if not isinstance(parts, list) or any(not isinstance(p, dict) or p.get('type') not in
                ('text', 'thinking', 'redacted_thinking') or (p.get('type') == 'text' and not isinstance(p.get('text'), str)) for p in parts):
                raise ValueError
            usage = metadata_object(data.get('usage'))
            result = dict(content=''.join(p['text'] for p in parts if p.get('type') == 'text'),
                          finish_reason=data.get('stop_reason'), reported_model_version=safe_model_version(data.get('model')),
                          prompt_tokens=safe_count(usage.get('input_tokens')),
                          completion_tokens=safe_count(usage.get('output_tokens')), total_tokens=None,
                          cached_tokens=safe_count(usage.get('cache_read_input_tokens')), reasoning_tokens=None)
        else:
            if not isinstance(data.get('choices'), list) or len(data['choices']) != 1:
                raise ValueError
            choice = data['choices'][0]
            message = choice['message']
            if (message.get('role', 'assistant') != 'assistant' or not isinstance(message.get('content'), str)
                or message.get('tool_calls') or message.get('function_call') or message.get('audio')):
                raise ValueError
            usage = metadata_object(data.get('usage'))
            pd = usage.get('prompt_tokens_details') or {}
            cd = usage.get('completion_tokens_details') or {}
            result = dict(content=message['content'], finish_reason=choice.get('finish_reason'),
                          reported_model_version=safe_model_version(data.get('model')),
                          prompt_tokens=safe_count(usage.get('prompt_tokens')),
                          completion_tokens=safe_count(usage.get('completion_tokens')),
                          total_tokens=safe_count(usage.get('total_tokens')),
                          cached_tokens=safe_count(pd.get('cached_tokens')) if isinstance(pd, dict) else None,
                          reasoning_tokens=safe_count(cd.get('reasoning_tokens')) if isinstance(cd, dict) else None)
        if result['finish_reason'] is not None and not isinstance(result['finish_reason'], str):
            raise ValueError
        return result, data
    except (ValueError, TypeError, KeyError, IndexError, AttributeError, RecursionError):
        raise ResponseEvidenceError('Invalid native provider response') from None



async def completion(route, *, messages, parameters, timeout, reserve):
    sdk = load_sdk()
    from openai import AsyncOpenAI
    from litellm.llms.custom_httpx.http_handler import AsyncHTTPHandler

    guard = SingleSendTransport(new_http_transport(), reserve)
    client = None
    error = None
    try:
        if route.sdk_provider == 'openai':
            raw = httpx.AsyncClient(transport=guard, trust_env=False, follow_redirects=False, timeout=timeout)
            # Validate before SDK model_dump can echo malformed usage in warnings.
            # This internal OpenAI option is guarded by the pinned-version tests.
            client = AsyncOpenAI(
                api_key=route.api_key, base_url=route.base_url, http_client=raw,
                max_retries=0, _strict_response_validation=True,
            )
        else:
            client = AsyncHTTPHandler(transport=guard, timeout=timeout, follow_redirects=False)
        try:
            if route.sdk_provider == 'vertex_ai':
                from app.engine.vertex_transport import sdk_completion
                await sdk_completion(route, messages=messages, parameters=parameters, client=client, timeout=timeout)
            else:
                await sdk.acompletion(
                    model=route.model, custom_llm_provider=route.sdk_provider,
                    api_base=route.base_url, api_key=route.api_key, client=client,
                    messages=deepcopy(messages), stream=False, timeout=timeout,
                    num_retries=0, max_retries=0, drop_params=False,
                    enable_json_schema_validation=False, **deepcopy(parameters),
                )
        except Exception as exc:
            error = exc
    finally:
        if client is not None:
            await close_owned(client.close)
        await close_owned(guard.aclose)
    # SDK wrappers may replace exceptions. Original wire errors/status remain
    # authoritative for the application's retry classifier and Retry-After.
    if guard.error is not None:
        raise guard.error from None
    if guard.response is not None:
        guard.response.raise_for_status()
        # Prove original native grammar/text/finish independently of the SDK
        # normalization outcome. Application parsing/quality still follows.
        try:
            native_observation(guard.response, route)
        except (LLMResponseEvidenceError, LLMPolicyError) as exc:
            exc.http_status = guard.response.status_code
            raise
        guard.response.extensions['lorechat_wire_controls'] = guard.wire_controls
        guard.response.extensions['lorechat_sdk_response_status'] = (
            'native_validated_after_sdk_error' if error is not None else 'native_validated')
        return guard.response
    from app.engine.llm_error_types import LLMTerminalError
    if isinstance(error, LLMTerminalError) or (route.sdk_provider == 'vertex_ai' and isinstance(error, httpx.HTTPError)):
        raise error from None
    raise TransportConfigurationError('SDK rejected request before dispatch') from None
