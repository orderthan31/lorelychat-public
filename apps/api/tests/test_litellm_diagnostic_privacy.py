"""Real-SDK diagnostic privacy; no SDK/serializer/warning mocks or global filters."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import warnings

import httpx
import pytest

from app.engine import litellm_transport as sdk
from test_litellm_transport import make, native, wire


SENTINEL = 'SYNTHETIC_PRIVATE_USAGE_VALUE_92ad'


def malformed_usage(kind):
    usage = deepcopy(native('openai')['usage'])
    if kind == 'text':
        return SENTINEL, (None, None, None, None, None)
    if kind == 'list':
        return [SENTINEL], (None, None, None, None, None)
    if kind == 'prompt':
        usage['prompt_tokens'] = SENTINEL
        return usage, (None, 12, 30, 0, 7)
    if kind == 'cache':
        usage['prompt_tokens_details']['cached_tokens'] = SENTINEL
        return usage, (11, 12, 30, None, 7)
    usage['completion_tokens_details']['reasoning_tokens'] = SENTINEL
    return usage, (11, 12, 30, 0, None)


@pytest.mark.parametrize('provider', ['openai', 'openai_compatible'])
@pytest.mark.parametrize('kind', ['text', 'list', 'prompt', 'cache', 'reasoning'])
async def test_malformed_native_usage_stays_private_and_preserves_prose(wire, provider, kind, capsys, caplog):
    usage, expected = malformed_usage(kind)
    data = native(provider, text='Unchanged original native prose.')
    data['usage'] = usage
    wire[1].append(httpx.Response(200, json=data))
    with warnings.catch_warnings(record=True) as emitted:
        warnings.simplefilter('always')
        result = await make(provider, api_key=SENTINEL).chat([
            {'role': 'user', 'content': 'Synthetic prompt, no private user data.'}])
    captured = capsys.readouterr()
    assert result.content == 'Unchanged original native prose.'
    assert (result.prompt_tokens, result.completion_tokens, result.total_tokens,
            result.cached_tokens, result.reasoning_tokens) == expected
    assert result.finish_reason == 'stop'
    assert result.requested_model == 'gpt-4o'
    assert result.reported_model_version == 'reported'
    assert result.generation_attempts == 1
    assert len(wire[0]) == 1
    assert wire[0][0].headers['authorization'] == 'Bearer ' + SENTINEL
    assert SENTINEL not in '\n'.join(str(item.message) for item in emitted)
    assert SENTINEL not in captured.out + captured.err + caplog.text
    assert result.transport_diagnostics['sdk_response_status'] == 'native_validated_after_sdk_error'


@pytest.mark.parametrize('provider', ['openai', 'openai_compatible'])
@pytest.mark.parametrize('kind', ['normal', 'missing', 'zero'])
async def test_ordinary_native_usage_contract_is_preserved(wire, provider, kind):
    data = native(provider, text='Ordinary valid response.')
    if kind == 'missing':
        del data['usage']
        expected = (None, None, None, None, None)
    elif kind == 'zero':
        data['usage'] = {'prompt_tokens': 0, 'completion_tokens': 0, 'total_tokens': 0,
                         'prompt_tokens_details': {'cached_tokens': 0},
                         'completion_tokens_details': {'reasoning_tokens': 0}}
        expected = (0, 0, 0, 0, 0)
    else:
        expected = (11, 12, 30, 0, 7)
    wire[1].append(httpx.Response(200, json=data))
    result = await make(provider).chat([{'role': 'user', 'content': 'Synthetic prompt.'}])
    assert result.content == 'Ordinary valid response.'
    assert (result.prompt_tokens, result.completion_tokens, result.total_tokens,
            result.cached_tokens, result.reasoning_tokens) == expected
    assert len(wire[0]) == 1


async def test_other_python_warnings_remain_visible(wire):
    sdk.load_sdk()
    previous_filters = list(warnings.filters)
    previous_showwarning = warnings.showwarning
    wire[1].append(httpx.Response(200, json=native('openai')))
    await make('openai').chat([{'role': 'user', 'content': 'Synthetic prompt.'}])
    assert warnings.filters == previous_filters
    assert warnings.showwarning is previous_showwarning
    with warnings.catch_warnings(record=True) as emitted:
        warnings.simplefilter('always')
        warnings.warn('SYNTHETIC_UNRELATED_WARNING', UserWarning)
    assert any('SYNTHETIC_UNRELATED_WARNING' in str(item.message) for item in emitted)


def test_malformed_usage_is_private_across_independent_thread_event_loops(wire, monkeypatch, capsys, caplog):
    sdk.load_sdk()
    requests = []

    async def send(request):
        requests.append(request)
        account = request.url.host.split('.')[0]
        assert request.headers['authorization'] == 'Bearer SYNTHETIC_ACCOUNT_' + account
        data = native('openai_compatible', text='Valid original ' + account)
        data['usage'] = SENTINEL + '_' + account
        return httpx.Response(200, json=data)

    monkeypatch.setattr(sdk, 'new_http_transport', lambda: httpx.MockTransport(send))

    def run(account):
        return asyncio.run(make('openai_compatible',
            provider_account_id=account,
            base_url='https://' + account + '.invalid/v1',
            api_key='SYNTHETIC_ACCOUNT_' + account).chat([
                {'role': 'user', 'content': 'Synthetic threaded prompt.'}]))

    with warnings.catch_warnings(record=True) as emitted:
        warnings.simplefilter('always')
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(run, ['alpha', 'beta']))
    captured = capsys.readouterr()
    assert [r.content for r in results] == ['Valid original alpha', 'Valid original beta']
    assert all(r.prompt_tokens is r.completion_tokens is r.total_tokens is None for r in results)
    assert all(r.generation_attempts == 1 for r in results)
    assert len(requests) == 2
    assert {r.url.host for r in requests} == {'alpha.invalid', 'beta.invalid'}
    assert SENTINEL not in '\n'.join(str(item.message) for item in emitted)
    assert SENTINEL not in captured.out + captured.err + caplog.text
