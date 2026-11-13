"""Vertex account validation and request-owned Google authentication.

Uploaded credentials are JSON, never paths, tokens or external-account configs.
No account credential is installed in process environment or an SDK global cache.
"""
from __future__ import annotations

import asyncio
import json
import re
import time
from types import SimpleNamespace

import httpx

TOKEN_URI = 'https://oauth2.googleapis.com/token'
SCOPES = ['https://www.googleapis.com/auth/cloud-platform']
FIELDS = ('vertex_project', 'vertex_location', 'vertex_auth_mode')


def validate_config(project, location, mode, base_url=None):
    if not isinstance(project, str) or not re.fullmatch(r'[a-z][a-z0-9-]{4,28}[a-z0-9]', project):
        raise ValueError('Vertex project must be a Google Cloud project ID')
    if not isinstance(location, str) or not (location == 'global' or re.fullmatch(
        r'(?:us|europe|asia|australia|northamerica|southamerica|africa|me)-(?:central|east|west|north|south|northeast|southeast|southwest)\d{1,2}', location)):
        raise ValueError('Vertex location must be global or a region ID')
    if mode not in ('service_account', 'adc'):
        raise ValueError('Unsupported Vertex authentication mode')
    if base_url is not None:
        raise ValueError('Vertex uses Google-managed endpoints; base_url is not supported')


def validate_model(model):
    if not isinstance(model, str) or not re.fullmatch(r'gemini-[a-zA-Z0-9][a-zA-Z0-9.-]{0,119}', model) or any(
        word in model.lower() for word in ('image', 'tts', 'audio', 'embedding', 'robotics', 'decision', 'research', 'live')):
        raise ValueError('Vertex currently supports Gemini text chat and compression only')


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError()
        result[key] = value
    return result


def service_account_info(value):
    """Validate without network; exception messages never contain submitted data."""
    try:
        if not isinstance(value, str) or len(value) > 32768:
            raise ValueError()
        info = json.loads(value, object_pairs_hook=_unique)
        required = {'type', 'project_id', 'private_key_id', 'private_key', 'client_email', 'client_id', 'token_uri'}
        allowed = required | {'auth_uri', 'auth_provider_x509_cert_url', 'client_x509_cert_url', 'universe_domain'}
        if not isinstance(info, dict) or not required <= info.keys() or not info.keys() <= allowed:
            raise ValueError()
        if any(not isinstance(v, str) or not v for v in info.values()):
            raise ValueError()
        if info['type'] != 'service_account' or info['token_uri'] != TOKEN_URI or info.get('universe_domain', 'googleapis.com') != 'googleapis.com':
            raise ValueError()
        if not re.fullmatch(r'[a-z][a-z0-9-]{4,28}[a-z0-9]', info['project_id']):
            raise ValueError()
        if not re.fullmatch(r'[a-zA-Z0-9._-]+@(?:[a-z0-9-]+\.iam|developer)\.gserviceaccount\.com', info['client_email']):
            raise ValueError()
        if not re.fullmatch(r'[a-zA-Z0-9_-]{1,128}', info['private_key_id']) or not info['client_id'].isdigit():
            raise ValueError()
        # Keep only fields consumed by google-auth, not caller-supplied URLs.
        info = {k: info[k] for k in required}
        from cryptography.hazmat.primitives.serialization import load_pem_private_key
        from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey
        key = load_pem_private_key(info['private_key'].encode(), password=None)
        if not isinstance(key, RSAPrivateKey) or key.key_size < 2048:
            raise ValueError()
        from google.oauth2 import service_account
        service_account.Credentials.from_service_account_info(info, scopes=SCOPES)
        return info
    except ImportError:
        raise ValueError('Vertex Google authentication dependencies are unavailable') from None
    except Exception:
        raise ValueError('Invalid Vertex service-account JSON credential') from None


class GoogleAuthRequest:
    """Bounded google-auth transport. No redirects, proxy inheritance or token retries."""
    def __init__(self, timeout):
        self.deadline = time.monotonic() + timeout
        self.token_sent = False
        self.calls = 0
        self.client = httpx.Client(transport=new_auth_transport(), trust_env=False, follow_redirects=False)

    def __call__(self, url, method='GET', body=None, headers=None, timeout=None, **kwargs):
        parsed = httpx.URL(url)
        # ADC discovery may read the attached Google service account. Uploaded
        # credentials never enter this path. No external-account URL is admitted.
        metadata = (parsed.scheme == 'http' and parsed.host in {'metadata.google.internal', '169.254.169.254'}
                    and parsed.port in (None, 80) and not parsed.username and not parsed.password)
        if url != TOKEN_URI and not metadata:
            raise ValueError('Unsupported Google authentication endpoint')
        self.calls += 1
        if self.calls > 8 or (url == TOKEN_URI and self.token_sent):
            raise ValueError('Additional Google authentication request denied')
        if url == TOKEN_URI:
            self.token_sent = True
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError()
        with self.client.stream(method, url, content=body, headers=headers,
                                timeout=min(remaining, float(timeout or remaining))) as response:
            if response.status_code == 429 or response.status_code >= 500:
                # Preserve availability/retry semantics without retaining auth bodies.
                safe = httpx.Response(response.status_code, request=httpx.Request(method, TOKEN_URI),
                    headers={k: v for k, v in response.headers.items() if k.lower() == 'retry-after'})
                safe.raise_for_status()
            data = bytearray()
            for chunk in response.iter_bytes():
                if len(data) + len(chunk) > 65536:
                    raise ValueError('Google authentication response too large')
                data.extend(chunk)
            return SimpleNamespace(status=response.status_code, data=bytes(data), headers=response.headers)

    def close(self):
        self.client.close()


def new_auth_transport():
    return httpx.HTTPTransport(retries=0, trust_env=False)


def _acquire_token(route, timeout):
    request = None
    try:
        from google.oauth2 import service_account, credentials as user_credentials
        from google.auth import compute_engine
        import google.auth
        request = GoogleAuthRequest(timeout)
        if route.vertex_auth_mode == 'service_account':
            credentials = service_account.Credentials.from_service_account_info(
                service_account_info(route.api_key), scopes=SCOPES)
        else:
            # Explicit opt-in only. No SDK cache, account JSON, or global SDK key
            # participates in server ADC discovery.
            credentials, _ = google.auth.default(scopes=SCOPES, request=request)
            if not isinstance(credentials, (service_account.Credentials, user_credentials.Credentials, compute_engine.Credentials)):
                raise ValueError()
            if isinstance(credentials, compute_engine.Credentials):
                # The public universe_domain property creates its own HTTP
                # transport; use google-auth's metadata helper with our fence.
                from google.auth.compute_engine import _metadata
                universe = _metadata.get_universe_domain(request)
            else:
                universe = credentials.universe_domain
            if universe != 'googleapis.com':
                raise ValueError()
        credentials.refresh(request)
        if not isinstance(credentials.token, str) or not credentials.token or '\n' in credentials.token or '\r' in credentials.token:
            raise ValueError()
        return credentials.token
    except httpx.HTTPStatusError:
        raise
    except (httpx.TimeoutException, TimeoutError):
        raise httpx.TimeoutException('Vertex authentication transport timed out') from None
    except httpx.RequestError:
        raise httpx.ConnectError('Vertex authentication transport failed') from None
    except Exception:
        from app.engine.llm_error_types import LLMAuthenticationError
        raise LLMAuthenticationError('Vertex credential acquisition failed') from None
    finally:
        if request is not None:
            request.close()


async def acquire_token(route, timeout):
    # A fresh credential per application attempt intentionally avoids shared
    # refresh locks/caches across accounts, threads and event loops. Google-auth
    # acquires a fresh token, including after expiry; no pasted token is accepted.
    return await asyncio.to_thread(_acquire_token, route, timeout)
