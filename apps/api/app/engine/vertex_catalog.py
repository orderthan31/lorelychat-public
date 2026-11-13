"""Authenticated Google publisher inventory, not project/region entitlement.

Protocol: publishers.models.list (v1beta1), publisherModels / nextPageToken.
Model Garden's versionId is a catalog revision, NOT an inference model suffix.
No generated fallback inventory or returned URL is ever followed.
"""
from __future__ import annotations

import json
import re
import time
from types import SimpleNamespace

import httpx

from app.engine import vertex_auth

CATALOG_URL = 'https://aiplatform.googleapis.com/v1beta1/publishers/google/models'
MAX_PAGES = 20
MAX_MODELS = 2000
MAX_RESPONSE_BYTES = 2_000_000
MAX_TOTAL_BYTES = 8_000_000
TOTAL_TIMEOUT = 60.0
MODEL_NAME = re.compile(r'publishers/google/models/([a-zA-Z0-9][a-zA-Z0-9._-]{0,199})')


def new_catalog_transport():
    return httpx.HTTPTransport(retries=0, trust_env=False)


def fetch_models(account, credential: str | None) -> list[dict]:
    """All-or-nothing bounded fetch; safe exceptions do not retain HTTP bodies."""
    vertex_auth.validate_config(account.vertex_project, account.vertex_location,
        account.vertex_auth_mode or 'service_account', account.base_url)
    route = SimpleNamespace(vertex_project=account.vertex_project, vertex_location=account.vertex_location,
        vertex_auth_mode=account.vertex_auth_mode or 'service_account',
        api_key=credential if account.vertex_auth_mode != 'adc' else None)
    deadline = time.monotonic() + TOTAL_TIMEOUT
    def remaining():
        value = deadline - time.monotonic()
        if value <= 0:
            raise ValueError('Vertex model catalog deadline exceeded')
        return value
    try:
        token = vertex_auth._acquire_token(route, min(20.0, remaining()))
        models, seen_tokens = {}, set()
        page_token = None
        total_bytes = 0
        with httpx.Client(transport=new_catalog_transport(), trust_env=False, follow_redirects=False) as client:
            for _ in range(MAX_PAGES):
                params = {'pageSize': 100, 'listAllVersions': 'false'}
                if page_token:
                    params['pageToken'] = page_token
                with client.stream('GET', CATALOG_URL, params=params,
                    headers={'Authorization': f'Bearer {token}', 'x-goog-user-project': account.vertex_project,
                             'Accept': 'application/json', 'Accept-Encoding': 'identity'},
                    timeout=min(10.0, remaining())) as response:
                    if response.status_code != 200:
                        raise ValueError('Vertex model catalog request rejected')
                    if response.headers.get('content-encoding', 'identity').lower() != 'identity':
                        raise ValueError('Compressed Vertex model catalog response denied')
                    data = bytearray()
                    # Check the deadline on every network chunk, not a buffered
                    # application chunk (which could hide a slow-drip response).
                    for chunk in response.iter_bytes():
                        remaining()
                        total_bytes += len(chunk)
                        if len(data) + len(chunk) > MAX_RESPONSE_BYTES or total_bytes > MAX_TOTAL_BYTES:
                            raise ValueError('Vertex model catalog response limit exceeded')
                        data.extend(chunk)
                remaining()
                payload = json.loads(data)
                if not isinstance(payload, dict) or not isinstance(payload.get('publisherModels', []), list):
                    raise ValueError('Invalid Vertex model catalog response')
                for item in payload.get('publisherModels', []):
                    if not isinstance(item, dict) or not isinstance(item.get('name'), str):
                        raise ValueError('Invalid Vertex model catalog entry')
                    match = MODEL_NAME.fullmatch(item['name'])
                    if not match:
                        raise ValueError('Invalid Vertex model catalog identifier')
                    models[match[1]] = dict(item, model_id=match[1])
                    if len(models) > MAX_MODELS:
                        raise ValueError('Vertex model catalog entry limit exceeded')
                page_token = payload.get('nextPageToken')
                if page_token in (None, ''):
                    return list(models.values())
                if not isinstance(page_token, str) or len(page_token) > 4096 or page_token in seen_tokens:
                    raise ValueError('Invalid Vertex model catalog page boundary')
                seen_tokens.add(page_token)
            raise ValueError('Vertex model catalog pagination limit exceeded')
    except Exception:
        # Includes google-auth errors, transport errors and malformed upstream JSON.
        # No exception chain/body/token/credential enters the public error boundary.
        raise ValueError('Vertex model catalog refresh failed; check authentication, Cloud API access and retry') from None
