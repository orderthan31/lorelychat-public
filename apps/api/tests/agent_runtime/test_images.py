import asyncio
import base64
from io import BytesIO
from datetime import datetime, timezone
import json

import httpx
from PIL import Image
import pytest

from app.agent.image_generation import (ImagePlanError, execute_image_plan,
    image_completion, list_image_models, validate_plan, verified_outputs)
from app.engine import litellm_transport as transport


@pytest.fixture(autouse=True)
def admission_date(monkeypatch):
    # Exercise deprecated routes as of their admitted date, not after shutdown.
    # The reference SDK suite separately verifies the real UTC cutoff behavior.
    class AdmissionDate(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 11, 15, tzinfo=tz or timezone.utc)
    monkeypatch.setattr('app.agent.image_generation.datetime', AdmissionDate)


def png():
    buffer = BytesIO()
    Image.new('RGB', (8, 8), 'blue').save(buffer, format='PNG')
    return buffer.getvalue()


def plan(provider='openai'):
    return {'provider_account_id': 'fixture', 'model_id': ('gpt-image-2.5-sunburst' if provider == 'openai' else 'grok-imagine-image'),
            'prompt': 'Synthetic image fixture', 'count': 1, 'options': {}, 'reference_images': []}


def route(provider='openai'):
    return transport.Route(provider, provider, plan(provider)['model_id'],
        'https://api.openai.com/v1' if provider == 'openai' else 'https://api.x.ai/v1', 'synthetic-image-key')


@pytest.mark.parametrize('provider,model', [('openai', 'gpt-image-2.5-sunburst'), ('openai', 'gpt-image-1-mini'),
    ('openai', 'gpt-image-1.5'), ('xai', 'grok-imagine-image')])
def test_actual_sdk_image_wire_and_verified_bytes(monkeypatch, provider, model):
    calls = []
    data = png()
    def wire(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200, json={'created': 1, 'data': [{'b64_json': base64.b64encode(data).decode()}]})
    monkeypatch.setattr(transport, 'new_http_transport', lambda: httpx.MockTransport(wire))
    from dataclasses import replace
    selected_plan = {**plan(provider), 'model_id': model}
    selected_route = replace(route(provider), model=model)
    outputs = asyncio.run(image_completion(selected_route, selected_plan, before_send=lambda: None))
    assert len(calls) == 1
    assert calls[0]['model'] == model
    assert outputs[0]['data'] == data
    assert outputs[0]['mime_type'] == 'image/png'
    assert (outputs[0]['width'], outputs[0]['height']) == (8, 8)


def test_image_sdk_failure_single_send_and_no_diagnostics(monkeypatch, capsys, caplog):
    calls = []
    def wire(request):
        calls.append(1)
        return httpx.Response(503, json={'error': {'message': 'synthetic-image-key sensitive-prompt'}})
    monkeypatch.setattr(transport, 'new_http_transport', lambda: httpx.MockTransport(wire))
    with pytest.raises(ImagePlanError) as caught:
        asyncio.run(image_completion(route(), plan(), before_send=lambda: None))
    assert calls == [1]
    captured = capsys.readouterr()
    observed = captured.out + captured.err + caplog.text + str(caught.value)
    assert 'synthetic-image-key' not in observed
    assert 'sensitive-prompt' not in observed


def test_image_registry_is_output_not_input_capability():
    assert list_image_models('openai')
    assert list_image_models('anthropic') == []
    assert list_image_models('openai_compatible') == []
    assert all(m['supports_reference'] for m in list_image_models('openai'))
    assert not any(m['supports_reference'] for m in list_image_models('xai'))
    assert 'gpt-image-1' not in {m['id'] for m in list_image_models('openai')}


@pytest.mark.parametrize('change', [{'reference_images': ['private.png']}, {'model_id': 'gpt-4o'},
    {'count': True}, {'count': 5}, {'options': {'api_key': 'injection'}}, {'options': {'size': '0x0'}}])
def test_unsupported_image_plan_rejected(change):
    value = plan()
    value.update(change)
    with pytest.raises(ImagePlanError): validate_plan(value, route())


@pytest.mark.parametrize('rows', [[{'url': 'http://169.254.169.254/secret'}], [{'b64_json': 'malformed'}],
                                 [{'b64_json': base64.b64encode(b'<svg/>').decode()}]])
def test_never_fetch_provider_urls_or_trust_mime(rows):
    with pytest.raises(ImagePlanError): verified_outputs({'data': rows}, 1)


def test_durable_claim_and_retention_no_repeat(monkeypatch):
    class Service:
        claimed = False
        operation = {'status': 'running'}
        def claim_image_generation(self, *args):
            if self.claimed: return {'claimed': False, 'operation': self.operation}
            self.claimed = True
            return {'claimed': True, 'plan': plan(), 'job_id': 'fixture-job'}
        def get_operation(self, *args): return self.operation
        def require_enabled(self, *args): pass
        def resolve_image_route(self, *args): return route()
        def load_image_reference(self, *args): return None
        def complete_image_job(self, principal, job_id, outputs, status='completed', error=None):
            self.outputs = outputs
            self.operation = {'status': status, 'error': error}
            return self.operation
    count = []
    async def generate(*args, **kwargs):
        assert service.claimed
        count.append(1)
        return verified_outputs({'data': [{'b64_json': base64.b64encode(png()).decode()}]}, 1)
    monkeypatch.setattr('app.agent.image_generation.image_completion', generate)
    service = Service()
    async def exercise():
        first = await execute_image_plan(service, object(), 'draft', 'request', 1, 'hash')
        repeat = await execute_image_plan(service, object(), 'draft', 'request', 1, 'hash')
        assert first == repeat == {'status': 'completed', 'error': None}
    asyncio.run(exercise())
    assert count == [1]
    assert service.outputs[0]['data'] == png()


def test_ambiguous_image_outcome_is_unknown(monkeypatch):
    class Service:
        def claim_image_generation(self, *args): return {'claimed': True, 'plan': plan(), 'job_id': 'fixture-job'}
        def require_enabled(self, *args): pass
        def resolve_image_route(self, *args): return route()
        def load_image_reference(self, *args): return None
        def complete_image_job(self, *args, **kwargs): return kwargs
    async def fail(*args, **kwargs): raise TimeoutError('synthetic key never exposed')
    monkeypatch.setattr('app.agent.image_generation.image_completion', fail)
    result = asyncio.run(execute_image_plan(Service(), object(), 'draft', 'request', 1, 'hash'))
    assert result['status'] == 'unknown'
    assert 'synthetic' not in result['error']
