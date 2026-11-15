"""Approved, single-dispatch image plans using maintained LiteLLM image APIs.

Output models are an explicit admission registry checked against the installed
SDK image-output metadata, never inferred from input-image support. Provider
URLs are NOT fetched: bounded inline bytes are the only admitted output format.
"""
from __future__ import annotations

import asyncio
import base64
from datetime import datetime, timezone
import hashlib
import warnings
from io import BytesIO
import json
import logging
from pathlib import Path

import httpx
from PIL import Image

from app.engine import litellm_transport as transport

# Explicit admission: official image API + pinned SDK metadata + synthetic wire.
# Do not restore retired IDs or silently replace saved model selections.
_IMAGE_MODELS = {
    'openai': ('gpt-image-2.5-sunburst', 'gpt-image-1.5', 'gpt-image-1-mini'),
    'xai': ('grok-imagine-image',),
}
_SHUTDOWN_DATES = {'gpt-image-1.5': '2026-12-01', 'gpt-image-1-mini': '2026-12-01'}
MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_PIXELS = 16_777_216


class ImagePlanError(ValueError):
    pass


def list_image_models(provider_type):
    # Read SDK's shipped metadata without remote model-cost refresh or importing
    # a provider client. Models absent from the installed registry fail closed.
    import importlib.util
    spec = importlib.util.find_spec('litellm')
    if spec is None or not spec.origin:
        return []
    path = Path(spec.origin).parent / 'model_prices_and_context_window_backup.json'
    try:
        registry = json.loads(path.read_text())
    except (OSError, ValueError):
        return []
    from app.agent import image_google
    if provider_type in image_google.PROVIDERS:
        return image_google.catalog(provider_type, registry)
    choices = []
    for model in _IMAGE_MODELS.get(provider_type, ()):
        key = model if provider_type == 'openai' else f'{provider_type}/{model}'
        metadata = registry.get(key, {})
        shutdown = _SHUTDOWN_DATES.get(model)
        if shutdown and datetime.now(timezone.utc).date().isoformat() >= shutdown:
            continue
        if metadata.get('mode') == 'image_generation' and metadata.get('litellm_provider') == provider_type:
            choice = {'id': model, 'label': model if not shutdown else f'{model} (deprecated; shuts down {shutdown})',
                      'provider_type': provider_type, 'supports_reference': provider_type == 'openai',
                      'deprecated': bool(shutdown), 'shutdown_date': shutdown}
            if provider_type == 'xai':
                choice['reference_unavailable_reason'] = (
                    'Reference editing requires xAI JSON image edits; the installed SDK route '
                    'has not been admitted. Choose a supported model explicitly; no text-only fallback.')
            choices.append(choice)
    return choices


def list_image_models_for_account(account):
    """Filter image execution, not chat/media-input flags or saved selections."""
    from app.agent.image_google import account_models
    return account_models(account, list_image_models(account.provider_type))


def build_image_overrides(account, secrets):
    """Reuse registered account secrets and metadata; never create owner keys."""
    from app.engine import vertex_auth
    values = {'provider_account_id': account.id, 'provider': account.provider_type,
              'api_key': secrets.get_secret(account.api_key_secret_ref), 'base_url': account.base_url}
    if account.provider_type == 'vertex_ai':
        values.update({key: getattr(account, key, None) for key in vertex_auth.FIELDS})
        values['vertex_auth_mode'] = values['vertex_auth_mode'] or 'service_account'
    return values


def resolve_image_route_overrides(values):
    """Image-only route; do not widen the chat/compression Vertex model guard."""
    from types import SimpleNamespace
    from app.agent import image_google
    from app.engine import vertex_auth
    provider, model = values.get('provider'), values.get('model')
    if not values.get('provider_account_id') or model not in {m['id'] for m in list_image_models(provider)}:
        raise ImagePlanError('Selected image account/model is unavailable')
    if provider == 'vertex_ai':
        try:
            vertex_auth.validate_config(values.get('vertex_project'), values.get('vertex_location'),
                values.get('vertex_auth_mode'), values.get('base_url'))
            route = transport.Route(provider, provider, model,
                f"https://{vertex_auth.endpoint_host(values['vertex_location'])}/v1",
                values.get('api_key'), values.get('vertex_project'), values.get('vertex_location'),
                values.get('vertex_auth_mode'))
            image_google.validate_route(route)
            return route
        except ValueError:
            raise ImagePlanError('Invalid Vertex image account or unsupported location') from None
    route = transport.resolve_route(SimpleNamespace(overrides=values, provider=lambda: provider, model=lambda: model))
    if provider in image_google.PROVIDERS:
        image_google.validate_route(route)
    return route


def validate_plan(plan, route):
    allowed = {item['id']: item for item in list_image_models(route.provider)}
    if (plan.get('model_id') not in allowed or route.model != plan['model_id']
            or route.sdk_provider != ('gemini' if route.provider == 'google' else route.provider)):
        raise ImagePlanError('Selected image model is not admitted')
    mode = plan.get('generation_mode', 'new')
    if mode not in {'new', 'reference'}:
        raise ImagePlanError('An explicit image generation choice is required')
    if mode == 'reference':
        if not allowed[route.model]['supports_reference']:
            raise ImagePlanError('Reference images are not supported by this route')
        metadata = plan.get('reference')
        if (not isinstance(metadata, dict) or set(metadata) != {'snapshot_id', 'source_id', 'sha256', 'mime_type'}
                or not all(isinstance(value, str) and value for value in metadata.values())):
            raise ImagePlanError('A selected reference snapshot is required')
    elif plan.get('reference') is not None:
        raise ImagePlanError('New generation cannot consume a reference')
    if plan.get('reference_images') or plan.get('reference_output_id'):
        raise ImagePlanError('Reference images are not supported by this route')
    if not isinstance(plan.get('prompt'), str) or not 1 <= len(plan['prompt'].strip()) <= 8000:
        raise ImagePlanError('Invalid image prompt')
    if type(plan.get('count')) is not int or not 1 <= plan['count'] <= 4:
        raise ImagePlanError('Invalid image count')
    if plan['count'] > allowed[route.model].get('max_count', 4):
        raise ImagePlanError('Selected image route supports one image per approved dispatch')
    options = plan.get('options') or {}
    if not isinstance(options, dict) or set(options) - {'size', 'quality'}:
        raise ImagePlanError('Unsupported image options')
    if route.provider == 'openai':
        if options.get('size', '1024x1024') not in {'1024x1024', '1536x1024', '1024x1536', 'auto'}:
            raise ImagePlanError('Unsupported image size')
        if options.get('quality', 'auto') not in {'auto', 'low', 'medium', 'high'}:
            raise ImagePlanError('Unsupported image quality')
    elif options:
        raise ImagePlanError('Image options are not admitted for this route')
    return dict(options)


def verified_outputs(response, count):
    data = response.model_dump() if hasattr(response, 'model_dump') else response
    rows = data.get('data')
    if not isinstance(rows, list) or len(rows) != count:
        raise ImagePlanError('Image output count mismatch')
    outputs = []
    for row in rows:
        encoded = row.get('b64_json')
        if not isinstance(encoded, str) or len(encoded) > (MAX_IMAGE_BYTES * 4 // 3 + 8):
            raise ImagePlanError('Provider did not return bounded inline image bytes')
        try:
            raw = base64.b64decode(encoded, validate=True)
            if not raw or len(raw) > MAX_IMAGE_BYTES:
                raise ValueError()
            with Image.open(BytesIO(raw)) as image:
                fmt, width, height = image.format, image.width, image.height
                if fmt not in {'PNG', 'JPEG', 'WEBP'} or width * height > MAX_PIXELS:
                    raise ValueError()
                image.verify()
            with Image.open(BytesIO(raw)) as image:
                image.load()
            outputs.append({'data': raw, 'mime_type': Image.MIME[fmt],
                            'width': width, 'height': height,
                            'sha256': hashlib.sha256(raw).hexdigest()})
        except Exception:
            raise ImagePlanError('Invalid image bytes') from None
    return outputs


def verified_reference(plan, reference):
    """Read-only bounded decode: never re-encode or fetch a reference URL/path."""
    if plan.get('generation_mode', 'new') == 'new':
        if reference is not None or plan.get('reference') is not None:
            raise ImagePlanError('New generation cannot consume a reference')
        return None
    metadata = plan.get('reference')
    if plan.get('generation_mode') != 'reference' or not isinstance(metadata, dict) or not isinstance(reference, dict):
        raise ImagePlanError('A selected reference snapshot is required')
    raw = reference.get('data')
    try:
        if not isinstance(raw, bytes) or not raw or len(raw) > MAX_IMAGE_BYTES:
            raise ValueError()
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(BytesIO(raw)) as image:
                fmt = image.format
                if fmt not in {'PNG', 'JPEG', 'WEBP'} or image.width * image.height > MAX_PIXELS:
                    raise ValueError()
                image.verify()
            with Image.open(BytesIO(raw)) as image:
                image.load()
        mime, digest = Image.MIME[fmt], hashlib.sha256(raw).hexdigest()
        if (reference.get('mime_type') != mime or metadata.get('mime_type') != mime
                or reference.get('sha256') != digest or metadata.get('sha256') != digest):
            raise ValueError()
    except Exception:
        raise ImagePlanError('Invalid reference image bytes or snapshot digest') from None
    return {'data': raw, 'mime_type': mime, 'sha256': digest}


class _ImageSendTransport(transport.SingleSendTransport):
    async def handle_async_request(self, request):
        # Native image edits stream multipart. Buffer only our already bounded
        # binary+prompt so the shared guard can inspect request.content safely.
        body = bytearray()
        async for chunk in request.stream:
            if len(body) + len(chunk) > MAX_IMAGE_BYTES + 65536:
                raise ImagePlanError('Image request exceeds capture limit')
            body.extend(chunk)
        buffered = httpx.Request(request.method, request.url, headers=request.headers,
                                 content=bytes(body), extensions=request.extensions)
        return await super().handle_async_request(buffered)


async def image_completion(route, plan, *, before_send, reference=None):
    options = validate_plan(plan, route)
    reference = verified_reference(plan, reference)
    from app.agent import image_google
    if route.provider in image_google.PROVIDERS:
        return await image_google.completion(route, plan, before_send=before_send, reference=reference)
    from openai import AsyncOpenAI
    if not route.api_key:
        raise ImagePlanError('Selected image account credential is required')
    sdk = transport.load_sdk()
    # The native SDK debug logger serializes request options including binary
    # files/prompts. Like LiteLLM diagnostic suppression, never enable it here.
    logging.getLogger('openai._base_client').disabled = True
    guard = _ImageSendTransport(transport.new_http_transport(), before_send)
    # One base64 image can be larger than the text response cap, but remains bounded.
    guard.max_response_bytes = MAX_IMAGE_BYTES * 4 // 3 * plan['count'] + 65536
    client = None
    try:
        async with asyncio.timeout(90):
            # Both admitted image drivers use the maintained OpenAI SDK client;
            # xAI's image driver does not accept LiteLLM AsyncHTTPHandler.
            raw = httpx.AsyncClient(transport=guard, trust_env=False, follow_redirects=False, timeout=85)
            client = AsyncOpenAI(api_key=route.api_key, base_url=route.base_url,
                                 http_client=raw, max_retries=0, _strict_response_validation=True)
            if reference is not None:
                extension = {'image/png': 'png', 'image/jpeg': 'jpg', 'image/webp': 'webp'}[reference['mime_type']]
                result = await client.images.edit(
                    model=route.model, prompt=plan['prompt'], n=plan['count'],
                    image=(f'reference.{extension}', reference['data'], reference['mime_type']),
                    timeout=85, **options,
                )
            else:
                if route.provider == 'xai':
                    options['response_format'] = 'b64_json'
                result = await sdk.aimage_generation(
                    model=(route.model if route.provider == 'openai' else f'{route.sdk_provider}/{route.model}'),
                    custom_llm_provider=route.sdk_provider, api_key=route.api_key,
                    api_base=route.base_url, client=client, prompt=plan['prompt'], n=plan['count'],
                    timeout=85, num_retries=0, max_retries=0, caching=False, **options,
                )
            if not guard.sent or guard.denied or guard.error is not None:
                raise ImagePlanError('Image dispatch did not complete')
            return verified_outputs(result, plan['count'])
    except asyncio.CancelledError:
        raise
    except Exception:
        raise ImagePlanError('Image dispatch outcome unknown; do not regenerate automatically') from None
    finally:
        if client is not None:
            await transport.close_owned(client.close)
        await transport.close_owned(guard.aclose)


async def execute_image_plan(service, principal, draft_id, request_id, expected_version, preview_hash):
    # Claim synchronously before any SDK await; duplicate request/draft returns
    # the durable receipt even if the caller lost the previous HTTP response.
    claim = service.claim_image_generation(principal, draft_id, request_id, expected_version, preview_hash)
    if not claim['claimed']:
        return claim['operation']
    plan, job_id = claim['plan'], claim['job_id']
    try:
        service.require_enabled(principal, 'lore:image:generate')
        route = service.resolve_image_route(principal, plan['provider_account_id'], plan['model_id'])
        validate_plan(plan, route)
        reference = service.load_image_reference(principal, plan)
        verified_reference(plan, reference)
    except Exception:
        return service.complete_image_job(principal, job_id, [], status='failed',
                                              error='Image plan rejected before dispatch')
    try:
        outputs = await image_completion(route, plan, reference=reference, before_send=lambda:
            service.require_enabled(principal, 'lore:image:generate'))
        # The core owns retained output paths/ownership and atomic image-job
        # completion. This does not register assets or change any avatar.
        return service.complete_image_job(principal, job_id, outputs)
    except asyncio.CancelledError:
        service.complete_image_job(principal, job_id, [], status='unknown',
                                      error='Image dispatch interrupted; do not regenerate automatically')
        raise
    except Exception:
        return service.complete_image_job(principal, job_id, [], status='unknown',
                                              error='Image outcome unknown; do not regenerate automatically')
