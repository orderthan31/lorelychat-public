"""Response admission happens before decoding and owned buffer growth."""
import asyncio
import gzip
import json

import httpx
import pytest

from app.services import decision_transport as dt


QUESTION = dt.AssetDecisionQuestion('q', {'asset': 'Synthetic image', 'none': 'No image'})
BODY = json.dumps({'answers': {'q': {'type': 'choice', 'choice': 'asset', 'confidence': .9,
    'probabilities': {'asset': .9, 'none': .1}}}}).encode()


class Stream(httpx.AsyncByteStream):
    def __init__(self, chunks):
        self.chunks = chunks
        self.pulls = 0
        self.closed = False
    async def __aiter__(self):
        for chunk in self.chunks:
            self.pulls += 1
            yield chunk
    async def aclose(self):
        self.closed = True


def invoke(stream, headers=None, buffered=None):
    requests = []
    def handler(request):
        requests.append(request)
        if buffered is not None:
            return httpx.Response(200, content=buffered, headers=headers)
        return httpx.Response(200, stream=stream, headers=headers)
    def run():
        return asyncio.run(dt.decide_assets(dt.DecisionRoute('google', 'synthetic', 'synthetic'),
            context={}, questions=[QUESTION], _transport=httpx.MockTransport(handler)))
    return run, requests


@pytest.mark.parametrize('encoding', ['gzip', 'deflate', 'br', 'zstd', 'unknown', 'identity, gzip'])
def test_encoded_response_rejected_before_body_or_decoder(monkeypatch, encoding):
    # Reproduce the old 16MiB expansion with the real HTTPX decoding seam.
    from httpx._decoders import GZipDecoder
    decoded = []
    original = GZipDecoder.decode
    def observe(self, data):
        decoded.append(len(data))
        return original(self, data)
    monkeypatch.setattr(GZipDecoder, 'decode', observe)
    stream = Stream([gzip.compress(b' ' * (16 * 1024 * 1024))])
    run, requests = invoke(stream, {'content-encoding': encoding})
    with pytest.raises(dt.DecisionUnavailable):
        run()
    assert len(requests) == 1 and requests[0].headers['accept-encoding'] == 'identity'
    assert stream.pulls == 0 and stream.closed and decoded == []


@pytest.mark.parametrize('chunks,expected_copies', [([b'x' * 33], []),
    ([b'x' * 24, b'x' * 16], [24]), ([b'x' * 16, b'x' * 16, b'x'], [16, 16])])
def test_raw_cap_checked_before_each_buffer_extend(monkeypatch, chunks, expected_copies):
    monkeypatch.setattr(dt, '_MAX_RESPONSE_BYTES', 32)
    copies = []
    class Buffer(bytearray):
        def extend(self, chunk):
            copies.append(len(chunk))
            return super().extend(chunk)
    monkeypatch.setattr(dt, 'bytearray', Buffer, raising=False)
    stream = Stream(chunks)
    run, requests = invoke(stream)
    with pytest.raises(dt.DecisionUnavailable):
        run()
    assert copies == expected_copies and stream.closed and len(requests) == 1


@pytest.mark.parametrize('headers', [None, {'content-encoding': 'identity'}, {'content-encoding': ' IDENTITY '}])
def test_small_identity_stream_and_exact_cap_still_select(monkeypatch, headers):
    monkeypatch.setattr(dt, '_MAX_RESPONSE_BYTES', len(BODY))
    stream = Stream([BODY[:7], BODY[7:]])
    run, requests = invoke(stream, headers)
    assert run() == {'q': 'asset'}
    assert stream.pulls == 2 and stream.closed and len(requests) == 1


def test_buffered_identity_response_uses_same_cap_before_copy(monkeypatch):
    monkeypatch.setattr(dt, '_MAX_RESPONSE_BYTES', len(BODY) - 1)
    copies = []
    class Buffer(bytearray):
        def extend(self, chunk):
            copies.append(len(chunk))
            return super().extend(chunk)
    monkeypatch.setattr(dt, 'bytearray', Buffer, raising=False)
    run, requests = invoke(None, buffered=BODY)
    with pytest.raises(dt.DecisionUnavailable):
        run()
    assert copies == [] and len(requests) == 1
