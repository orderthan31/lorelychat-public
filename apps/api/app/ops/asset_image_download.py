"""Bounded direct HTTP import. No environment proxies, cookies or URL logging."""
import asyncio
from contextvars import ContextVar
import ipaddress
import logging
import socket
from urllib.parse import urlsplit, urlunsplit, urljoin

import httpcore

MAX_BYTES = 8 * 1024 * 1024
DEADLINE = 10.0
_IMPORT_ACTIVE = ContextVar('asset_image_import_active', default=False)


class _PrivateImportLogs(logging.Filter):
    def filter(self, record):
        # httpcore DEBUG response traces can echo signed redirect URLs. Suppress
        # only this task's import traces, never unrelated provider diagnostics.
        return not _IMPORT_ACTIVE.get()


for _logger in ('httpcore.connection', 'httpcore.http11', 'httpcore.http2', 'httpcore.proxy', 'httpcore.socks'):
    logging.getLogger(_logger).addFilter(_PrivateImportLogs())


def _global(value):
    address = ipaddress.ip_address(value)
    return address.is_global and not address.is_multicast and not address.is_reserved


async def _pinned_url(url):
    if len(url) > 8192 or any(ord(c) < 33 or ord(c) == 127 for c in url) or '\\' in url:
        raise ValueError()
    parsed = urlsplit(url)
    if parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username is not None or parsed.password is not None:
        raise ValueError()
    host = parsed.hostname.encode('idna').decode('ascii')
    if '%' in host:
        raise ValueError()
    port = parsed.port or (443 if parsed.scheme == 'https' else 80)
    addresses = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    ips = list(dict.fromkeys(entry[4][0] for entry in addresses))
    if not ips or not all(_global(ip) for ip in ips):
        raise ValueError()
    ip = ips[0]
    authority = f'[{ip}]' if ':' in ip else ip
    original = f'[{host}]' if ':' in host else host
    if parsed.port is not None:
        original += f':{port}'
    return urlunsplit((parsed.scheme, f'{authority}:{port}', parsed.path or '/', parsed.query, '')), original, host


async def download_image(url: str) -> tuple[bytes, str]:
    token = _IMPORT_ACTIVE.set(True)
    try:
        async with asyncio.timeout(DEADLINE):
            # httpcore has no environment configuration or automatic redirects.
            async with httpcore.AsyncConnectionPool(max_connections=1, max_keepalive_connections=0, retries=0) as client:
                for hop in range(4):
                    pinned, host, sni = await _pinned_url(url)
                    async with client.stream('GET', pinned, headers={
                        'Host': host, 'Accept': 'image/*', 'Accept-Encoding': 'identity',
                    }, extensions={'sni_hostname': sni, 'timeout': {
                        'connect': 5.0, 'read': 5.0, 'write': 5.0, 'pool': 5.0,
                    }}) as response:
                        headers = {key.lower(): value for key, value in response.headers}
                        if response.status in {301, 302, 303, 307, 308}:
                            if hop == 3 or b'location' not in headers:
                                raise ValueError()
                            url = urljoin(url, headers[b'location'].decode('ascii'))
                            continue
                        if response.status != 200 or headers.get(b'content-encoding', b'identity').lower() != b'identity':
                            raise ValueError()
                        if int(headers.get(b'content-length', b'0')) > MAX_BYTES:
                            raise ValueError()
                        data = bytearray()
                        async for chunk in response.aiter_stream():
                            if len(data) + len(chunk) > MAX_BYTES:
                                raise ValueError()
                            data.extend(chunk)
                        if not data:
                            raise ValueError()
                        return bytes(data), headers.get(b'content-type', b'').decode('ascii').split(';')[0].strip().lower()
    except Exception:
        raise ValueError('Image URL import failed or exceeds safety budget') from None
    finally:
        _IMPORT_ACTIVE.reset(token)
    raise ValueError('Image URL import failed or exceeds safety budget')
