"""Google image-output admission and request-owned maintained SDK drivers.

Official IDs/protocol: ai.google.dev/gemini-api/docs/image-generation and
cloud.google.com/gemini-enterprise-agent-platform/models/capabilities/image-generation.
No chat capability flag, ADC, credential cache, URL image fetch or retry is used.
"""
from __future__ import annotations

import asyncio
import base64
from datetime import datetime, timezone
import json
from types import SimpleNamespace

import httpx

from app.agent.diagnostics import Failure, exception_name, failure
from app.engine import litellm_transport as transport, vertex_auth

PROVIDERS = frozenset({'google', 'gemini', 'vertex_ai'})
MODELS = (
    ('gemini-nano-banana-2.1', 'Nano Banana 2.1'),
    ('gemini-3.1-flash-lite-image', 'Nano Banana 2 Lite'),
    ('gemini-3.1-flash-image', 'Nano Banana 2'),
    ('gemini-3-pro-image', 'Nano Banana Pro'),
    ('gemini-2.5-flash-image', 'Nano Banana'),
)
# Provider-specific lifecycle. Future "or later" retirement dates are treated
# conservatively as admission cutoffs, not an assertion of an exact shutdown.
VERTEX_CUTOFFS = {
    'gemini-3.1-flash-lite-image': '2027-06-28',
    'gemini-3.1-flash-image': '2027-05-28',
    'gemini-3-pro-image': '2027-05-28',
    'gemini-2.5-flash-image': '2027-03-15',
}
VERTEX_LOCATIONS = {
    'gemini-nano-banana-2.1': ('global',),
    'gemini-3.1-flash-lite-image': ('global',),
    'gemini-3.1-flash-image': ('global', 'us', 'eu'),
    'gemini-3-pro-image': ('global',),
    'gemini-2.5-flash-image': (
        'global', 'us-central1', 'us-east1', 'us-east4', 'us-east5', 'us-south1',
        'us-west1', 'us-west4', 'europe-central2', 'europe-north1',
        'europe-southwest1', 'europe-west1', 'europe-west4', 'europe-west8',
    ),
}
GEMINI_BASE = 'https://generativelanguage.googleapis.com/v1beta'


def catalog(provider, registry):
    """Explicit output admission, including native SDK metadata namespaces."""
    sdk_provider = 'vertex_ai-language-models' if provider == 'vertex_ai' else 'gemini'
    prefix = 'vertex_ai' if provider == 'vertex_ai' else 'gemini'
    today = datetime.now(timezone.utc).date().isoformat()
    choices = []
    for model, label in MODELS:
        meta = registry.get(f'{prefix}/{model}', {})
        # Some Vertex prefixed records omit modalities; its canonical record
        # must independently confirm output IMAGE, never merely supports_vision.
        canonical = registry.get(model, {}) if provider == 'vertex_ai' else meta
        if (meta.get('mode') != 'image_generation' or meta.get('litellm_provider') != sdk_provider
                or canonical.get('mode') != 'image_generation'
                or canonical.get('litellm_provider') != sdk_provider
                or 'image' not in canonical.get('supported_output_modalities', [])
                or not {'/v1/images/generations', '/v1/images/edits'} <= set(meta.get('supported_endpoints', []))):
            continue
        cutoff = (VERTEX_CUTOFFS.get(model) if provider == 'vertex_ai'
                  else '2027-03-15' if model == 'gemini-2.5-flash-image' else None)
        if cutoff and today >= cutoff:
            continue
        deprecated = model == 'gemini-2.5-flash-image'
        choice = {'id': model, 'label': f'{label} ({model})', 'provider_type': provider,
                  'supports_reference': True, 'max_count': 1, 'deprecated': deprecated,
                  'shutdown_date': cutoff if deprecated else None}
        if cutoff:
            choice['admission_cutoff_date'] = cutoff
        if deprecated:
            choice['label'] += f' — deprecated; shuts down {cutoff}'
        if provider == 'vertex_ai':
            choice['vertex_locations'] = list(VERTEX_LOCATIONS[model])
        choices.append(choice)
    return choices


def account_models(account, choices):
    if account.provider_type != 'vertex_ai':
        return choices
    try:
        mode = getattr(account, 'vertex_auth_mode', None) or 'service_account'
        vertex_auth.validate_config(account.vertex_project, account.vertex_location, mode, account.base_url)
        if mode != 'service_account':
            return []
        return [m for m in choices if account.vertex_location in m['vertex_locations']]
    except (AttributeError, ValueError):
        return []


def validate_route(route):
    from app.agent.image_generation import ImagePlanError, list_image_models
    if route.model not in {m['id'] for m in list_image_models(route.provider)}:
        raise ImagePlanError('Selected Google image model is not admitted')
    if not isinstance(route.api_key, str) or not route.api_key.strip():
        raise ImagePlanError('Selected image account credential is required')
    if route.provider in {'google', 'gemini'}:
        if route.sdk_provider != 'gemini' or route.base_url != GEMINI_BASE:
            raise ImagePlanError('Invalid Gemini image account route')
    elif route.provider == 'vertex_ai':
        try:
            vertex_auth.validate_config(route.vertex_project, route.vertex_location, route.vertex_auth_mode)
            if route.vertex_auth_mode != 'service_account':
                raise ValueError()
            if route.vertex_location not in VERTEX_LOCATIONS[route.model]:
                raise ValueError()
            if route.sdk_provider != 'vertex_ai' or route.base_url != f'https://{vertex_auth.endpoint_host(route.vertex_location)}/v1':
                raise ValueError()
            vertex_auth.service_account_info(route.api_key)
        except (ValueError, KeyError):
            raise ImagePlanError('Invalid Vertex image account or unsupported location') from None
    else:
        raise ImagePlanError('Unsupported Google image provider')


def _native_outputs(response, count):
    """Verify native bytes/MIME before SDK conversion discards that evidence.

    Only verified non-thought inline image parts reach SDK response conversion.
    Usage/debug/unknown metadata is not serialized by SDK models or retained.
    """
    from app.agent.image_generation import ImagePlanError, verified_outputs
    try:
        body = response.json()
        if not isinstance(body, dict):
            raise ValueError()
        if body.get('promptFeedback', {}).get('blockReason'):
            raise Failure('IMAGE_POLICY_BLOCKED', 'ImagePlanError', 200).wrap(ImagePlanError)
        candidates = body.get('candidates')
        if not isinstance(candidates, list) or len(candidates) != 1:
            raise ValueError()
        candidate = candidates[0]
        if not isinstance(candidate, dict):
            raise ValueError()
        finish = candidate.get('finishReason')
        if finish in ('SAFETY', 'IMAGE_SAFETY', 'PROHIBITED_CONTENT', 'IMAGE_PROHIBITED_CONTENT'):
            raise Failure('IMAGE_POLICY_BLOCKED', 'ImagePlanError', 200).wrap(ImagePlanError)
        if finish == 'NO_IMAGE':
            raise Failure('IMAGE_NO_OUTPUT', 'ImagePlanError', 200).wrap(ImagePlanError)
        if finish != 'STOP':
            raise ValueError()
        parts = candidate['content']['parts']
        if not isinstance(parts, list) or not 1 <= len(parts) <= 32:
            raise ValueError()
        safe = []
        for part in parts:
            if not isinstance(part, dict) or 'fileData' in part or 'file_data' in part:
                raise ValueError()
            if 'thought' in part and type(part['thought']) is not bool:
                raise ValueError()
            if 'inlineData' in part:
                inline = part['inlineData']
                if not isinstance(inline, dict) or inline.get('mimeType') not in {'image/png', 'image/jpeg', 'image/webp'}:
                    raise ValueError()
                output = verified_outputs({'data': [{'b64_json': inline.get('data')}]}, 1)[0]
                if output['mime_type'] != inline['mimeType']:
                    raise ValueError()
                if not part.get('thought', False):
                    safe.append({'inlineData': {'mimeType': output['mime_type'], 'data': inline['data']}})
        if not safe:
            raise Failure('IMAGE_NO_OUTPUT', 'ImagePlanError', 200).wrap(ImagePlanError)
        if len(safe) != count:
            raise ValueError()
        return {'candidates': [{'content': {'role': 'model', 'parts': safe}, 'finishReason': 'STOP'}]}
    except Exception as exc:
        if isinstance(getattr(exc, 'agent_failure', None), Failure):
            raise
        raise Failure('IMAGE_INVALID_RESPONSE', exception_name(exc), 200).wrap(ImagePlanError) from None


class GoogleImageSendTransport(transport.SingleSendTransport):
    def __init__(self, inner, reserve, *, endpoint, plan, reference):
        super().__init__(inner, reserve)
        from app.agent.image_generation import MAX_IMAGE_BYTES
        self.endpoint = endpoint
        self.plan = plan
        self.reference = reference
        self.max_request_bytes = MAX_IMAGE_BYTES * 4 // 3 + 65536
        self.max_response_bytes = MAX_IMAGE_BYTES * 4 // 3 + 65536

    async def handle_async_request(self, request):
        from app.agent.image_generation import ImagePlanError
        # Fail closed if an SDK changes endpoint, adds tools or drops the ref.
        if request.method != 'POST' or str(request.url) != self.endpoint:
            raise ImagePlanError('Unexpected Google image endpoint')
        raw = await request.aread()
        if len(raw) > self.max_request_bytes:
            raise ImagePlanError('Google image request exceeds capture limit')
        body = json.loads(raw)
        contents = body.get('contents')
        if not isinstance(contents, list) or len(contents) != 1 or set(body) != {'contents', 'generationConfig'}:
            raise ImagePlanError('Unexpected Google image request')
        expected = [{'text': self.plan['prompt']}]
        if self.reference is not None:
            expected.insert(0, {'inlineData': {'mimeType': self.reference['mime_type'],
                'data': base64.b64encode(self.reference['data']).decode('ascii')}})
        config = body['generationConfig']
        if (contents[0].get('parts') != expected
                or config != {'response_modalities': ['IMAGE', 'TEXT'], 'candidateCount': 1}):
            raise ImagePlanError('Google image request evidence mismatch')
        try:
            response = await super().handle_async_request(request)
        except (httpx.ConnectError, httpx.RemoteProtocolError) as exc:
            # The pinned AsyncHTTPHandler has a connection-error retry path
            # independent of num_retries. Withhold that signal; never create
            # another client or dispatch even after an ambiguous network error.
            raise failure('IMAGE_REQUEST', exc, self).wrap(ImagePlanError) from None
        if response.status_code != 200:
            self.error = Failure('IMAGE_REQUEST_HTTP_ERROR', 'ImagePlanError', response.status_code).wrap(ImagePlanError)
            raise self.error
        try:
            safe = _native_outputs(response, self.plan['count'])
        except ImagePlanError as exc:
            self.error = exc  # Preserve safe evidence even if the SDK wraps the exception.
            raise
        return httpx.Response(200, json=safe, request=request)


async def completion(route, plan, *, before_send, reference):
    """Real lower LiteLLM image handler, no global routing/callback/retry layer.

    Gemini generateContent image serializers are shared by Google's two APIs.
    The installed Vertex edit serializer instead emits a JSON string/uppercase
    USER and inherits multipart defaults: use Gemini's maintained JSON edit
    serializer with Vertex's maintained URL builder, not that incompatible path.
    """
    from app.agent.image_generation import ImagePlanError, verified_outputs
    validate_route(route)
    transport.load_sdk()
    from litellm.llms.gemini.image_generation.transformation import GoogleImageGenConfig
    from litellm.llms.gemini.image_edit.transformation import GeminiImageEditConfig
    from litellm.llms.vertex_ai.image_generation.vertex_gemini_transformation import VertexAIGeminiImageGenerationConfig
    from litellm.llms.custom_httpx.llm_http_handler import BaseLLMHTTPHandler
    from litellm.llms.custom_httpx.http_handler import AsyncHTTPHandler
    from litellm.types.router import GenericLiteLLMParams

    guard = raw = None
    stage = 'IMAGE_DISPATCH'
    try:
        async with asyncio.timeout(90):
            headers = {'Content-Type': 'application/json', 'Accept-Encoding': 'identity'}
            if route.provider == 'vertex_ai':
                # Existing request-owned OAuth implementation: fresh uploaded
                # service-account credentials, fixed bounded token POST. No ADC.
                before_send()
                stage = 'IMAGE_AUTH'
                token = await vertex_auth.acquire_token(route, 20)
                stage = 'IMAGE_DISPATCH'
                headers['Authorization'] = f'Bearer {token}'
                params = {'vertex_project': route.vertex_project, 'vertex_location': route.vertex_location}
                endpoint = VertexAIGeminiImageGenerationConfig().get_complete_url(
                    api_base=None, api_key=None, model=route.model, optional_params={}, litellm_params=params)
                expected = (f'{route.base_url}/projects/{route.vertex_project}/locations/'
                            f'{route.vertex_location}/publishers/google/models/{route.model}:generateContent')
                if endpoint != expected:
                    raise ImagePlanError('SDK changed the selected Vertex endpoint')
            else:
                headers['x-goog-api-key'] = route.api_key
                endpoint = GoogleImageGenConfig().get_complete_url(
                    api_base=route.base_url, api_key=route.api_key, model=route.model,
                    optional_params={}, litellm_params={})

            # These overrides never consult SDK/environment credential helpers.
            class OwnedGeneration(GoogleImageGenConfig):
                def validate_environment(self, **kwargs): return dict(headers)
                def get_complete_url(self, **kwargs): return endpoint

            class OwnedEdit(GeminiImageEditConfig):
                def validate_environment(self, **kwargs): return dict(headers)
                def get_complete_url(self, **kwargs): return endpoint

            guard = GoogleImageSendTransport(transport.new_http_transport(), before_send,
                endpoint=endpoint, plan=plan, reference=reference)
            # The pinned AsyncHTTPHandler accepts transport (not client=); its
            # injected-transport branch sets trust_env=False and owns no pool cache.
            client = AsyncHTTPHandler(transport=guard, timeout=65, follow_redirects=False)
            raw = client.client
            log = SimpleNamespace(pre_call=lambda **kw: None, post_call=lambda **kw: None, optional_params={})
            handler = BaseLLMHTTPHandler()
            if reference is not None:
                result = await handler.async_image_edit_handler(
                    model=route.model, image=reference['data'], prompt=plan['prompt'],
                    image_edit_provider_config=OwnedEdit(), image_edit_optional_request_params={'n': 1},
                    custom_llm_provider=route.sdk_provider,
                    litellm_params=GenericLiteLLMParams(api_key=None, api_base=None, num_retries=0, max_retries=0),
                    logging_obj=log, timeout=65, client=client)
            else:
                result = await handler.async_image_generation_handler(
                    model=route.model, prompt=plan['prompt'], image_generation_provider_config=OwnedGeneration(),
                    image_generation_optional_request_params={'n': 1}, custom_llm_provider=route.sdk_provider,
                    litellm_params={'num_retries': 0, 'max_retries': 0}, api_key=None,
                    logging_obj=log, timeout=65, client=client)
            if not guard.sent or guard.denied or guard.error is not None:
                raise ImagePlanError('Google image dispatch did not complete')
            stage = 'IMAGE_RESPONSE'
            return verified_outputs(result, plan['count'])
    except Exception as exc:
        if stage == 'IMAGE_DISPATCH' and guard is not None and guard.sent:
            stage = 'IMAGE_REQUEST'
        raise failure(stage, exc, guard).wrap(ImagePlanError) from None
    finally:
        if raw is not None:
            await transport.close_owned(raw.aclose)
        if guard is not None:
            await transport.close_owned(guard.aclose)
