"""Shared deadline, cancellation, circuit ownership and attempt ledger proofs."""
import asyncio
import time
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from types import SimpleNamespace
import httpx
import pytest
from sqlmodel import Session, select
from app.engine import llm_client as llm
from app.engine import litellm_transport as sdk
from app.engine.llm_transport_deadline import TASK_DEADLINE
from app.engine.llm_error_types import LLMTaskDeadlineError, LLMPolicyBlockedError
from app.db.models import LLMUsageEvent
from test_litellm_transport import make, native, wire

_REAL_FAILURE = llm.LLMClient._record_transport_failure
_REAL_USAGE = llm.LLMClient._record_usage

async def test_slow_chunk_deadline_one_actual_send_no_circuit(wire,monkeypatch):
    sdk.load_sdk()  # Import/bootstrap is synchronous and outside this I/O timing proof.
    wire[1].append(httpx.Response(200, json=native('openai')))
    await make().chat([])  # Warm the pinned SDK's lazy route initialization.
    class Slow(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'{'
            await asyncio.sleep(2)
            yield b'}'
    sends=[]
    async def send(request):
        sends.append(request)
        return httpx.Response(200,stream=Slow())
    monkeypatch.setattr(sdk,'new_http_transport',lambda:httpx.MockTransport(send))
    obj=make(circuit_breaker_enabled=True)
    obj.reset_circuit_breakers()
    monkeypatch.setattr(llm,'get_settings',lambda:SimpleNamespace(llm_task_deadline_seconds=0.05))
    class Task:
        settings=SimpleNamespace(llm_task_deadline_seconds=0.05)
        @llm.bounded_llm_task
        async def run(self):return await obj.chat([])
    started=time.monotonic()
    with pytest.raises(LLMTaskDeadlineError) as caught:await Task().run()
    assert time.monotonic()-started < 0.5
    assert len(sends)==1 and caught.value.transport_attempts==1
    assert llm._CIRCUIT_STATES[obj.circuit_key()]['failures']==0

async def test_cleanup_stall_is_bounded():
    finalized=asyncio.Event()
    async def close():
        try:await asyncio.sleep(10)
        finally:finalized.set()
    token=TASK_DEADLINE.set(time.monotonic()+0.01)
    try:
        started=time.monotonic()
        await sdk.close_owned(close)
        assert time.monotonic()-started<0.3
        await asyncio.wait_for(finalized.wait(),0.2)
    finally:TASK_DEADLINE.reset(token)

async def test_cancelled_half_open_owner_releases_probe(wire,monkeypatch):
    obj=make(circuit_breaker_enabled=True,circuit_failure_threshold=1)
    obj.reset_circuit_breakers();obj.record_circuit_failure()
    state=llm._CIRCUIT_STATES[obj.circuit_key()];state['open_until']=0.00001
    entered=asyncio.Event()
    async def stall(*a,**kw):entered.set();await asyncio.sleep(10)
    monkeypatch.setattr(obj,'_chat_litellm',stall)
    task=asyncio.create_task(obj.chat([]));await entered.wait();task.cancel()
    with pytest.raises(asyncio.CancelledError):await task
    assert state['probe'] is None and state['failures']==1
    state['open_until']=0.00001
    assert obj.ensure_circuit_available()[2] is not None

def test_barrier_half_open_one_admission():
    obj=make(circuit_breaker_enabled=True,circuit_failure_threshold=1)
    obj.reset_circuit_breakers();obj.record_circuit_failure()
    llm._CIRCUIT_STATES[obj.circuit_key()]['open_until']=0.00001
    barrier=Barrier(8)
    def enter(_):
        barrier.wait()
        try:return obj.ensure_circuit_available()
        except llm.CircuitOpenError:return None
    with ThreadPoolExecutor(8) as pool:tokens=list(pool.map(enter,range(8)))
    assert sum(token is not None for token in tokens)==1

def test_stale_success_cannot_clear_newer_closed_failure():
    obj=make(circuit_breaker_enabled=True,circuit_failure_threshold=2)
    obj.reset_circuit_breakers();token=obj.ensure_circuit_available();obj.record_circuit_failure(token)
    obj.record_circuit_success(token)
    assert llm._CIRCUIT_STATES[obj.circuit_key()]['failures']==1

@pytest.mark.parametrize('status',[408,429,500,502,503,504])
async def test_transient_matrix_two_sends_then_success(wire,status):
    wire[1].extend([httpx.Response(status,json={'error':{'message':'sensitive'}},headers={'Retry-After':'0'}),httpx.Response(200,json=native('openai'))])
    result=await make().chat([])
    assert len(wire[0])==result.generation_attempts==2

@pytest.mark.parametrize('status',[400,403,429])
async def test_explicit_http_policy_code_terminal(wire,status):
    wire[1].append(httpx.Response(status,json={'error':{'code':'content_policy_violation','message':'sensitive'}}))
    with pytest.raises(LLMPolicyBlockedError):await make().chat([])
    assert len(wire[0])==1

@pytest.mark.parametrize('payload',[{'choices':[]},None,'duplicate','nonjson'])
async def test_malformed_paid_attempt_ledger_readback(wire,monkeypatch,engine,payload):
    monkeypatch.setattr(llm,'engine',engine)
    monkeypatch.setattr(llm.LLMClient,'_record_transport_failure',_REAL_FAILURE)
    body=b'{"choices":[],"choices":[]}' if payload=='duplicate' else b'not json'
    wire[1].append(httpx.Response(200,json=payload) if payload is None or isinstance(payload,dict) else httpx.Response(200,content=body))
    with pytest.raises(llm.LLMResponseEvidenceError):await make(model_option_key='option-a').chat([])
    with Session(engine) as session:
        rows=session.exec(select(LLMUsageEvent)).all()
        assert len(rows)==1
        row=rows[0]
        assert row.prompt_tokens is row.total_tokens is row.completion_tokens is None
        meta=row.metadata_
        assert meta['transport_attempts']==1 and meta['http_status']==200
        assert meta['error_type']=='native_evidence_invalid'
        assert meta['effective_route']['model_option_key']=='option-a'
        assert len(meta['attempt_trace'])==1
        assert meta['attempt_trace'][0]['dispatched']==1

async def test_retry_success_ledger_and_requested_effective_route(wire,monkeypatch,engine):
    monkeypatch.setattr(llm,'engine',engine)
    monkeypatch.setattr(llm.LLMClient,'_record_usage',_REAL_USAGE)
    monkeypatch.setattr(llm.LLMClient,'_record_transport_failure',_REAL_FAILURE)
    wire[1].extend([httpx.Response(503,json={'error':{'message':'private'}},headers={'retry-after':'0'}),httpx.Response(503,json={'error':{}}),httpx.Response(200,json=native('xai'))])
    @llm.bounded_llm_task
    async def run():
        try:await make(model_option_key='primary').chat([])
        except llm.LLMUnavailableError:return await make('xai','grok-4.3',model_option_key='fallback',provider_account_id='other').chat([])
    await run()
    with Session(engine) as session:
        rows=session.exec(select(LLMUsageEvent)).all()
        assert len(rows)==2
        primary=next(row for row in rows if row.provider=='openai')
        fallback=next(row for row in rows if row.provider=='xai')
        assert len(primary.metadata_['attempt_trace'])==2
        assert all(t['transport_elapsed_seconds']>=0 and t['backoff_elapsed_seconds']>=0 for t in primary.metadata_['attempt_trace'])
        assert fallback.metadata_['requested_route']['model_option_key']=='primary'
        assert fallback.metadata_['effective_route']['model_option_key']=='fallback'
        assert fallback.metadata_['network_attempts']==3

async def test_telemetry_failure_counter_not_raw_diagnostic(wire,monkeypatch,caplog):
    def broken(*a,**k):raise RuntimeError('PRIVATE_TELEMETRY_SENTINEL')
    monkeypatch.setattr(llm,'Session',broken)
    monkeypatch.setattr(llm.LLMClient,'_record_usage',_REAL_USAGE)
    before=llm.telemetry_write_failure_count()
    wire[1].append(httpx.Response(200,json=native('openai')))
    assert (await make().chat([])).content==' untouched '
    assert llm.telemetry_write_failure_count()==before+1
    assert 'PRIVATE_TELEMETRY_SENTINEL' not in caplog.text

async def test_retry_after_cannot_outlive_task(wire,monkeypatch):
    from app.engine import llm_transport_deadline as deadline

    # Bootstrap is not retry/backoff: a cold native adapter can consume a 40ms
    # allowance before any request. Warm it, then consume the application clock
    # at the actual first response, leaving only 20ms for a real 10-second wait.
    wire[1].append(httpx.Response(200,json=native('openai')))
    await make().chat([])
    wire[0].clear()
    clock=[time.monotonic()]
    application_clock=SimpleNamespace(monotonic=lambda:clock[0])
    monkeypatch.setattr(llm,'time',application_clock)
    monkeypatch.setattr(deadline,'time',application_clock)
    transport=sdk.new_http_transport()
    remainders=[]
    async def send(request):
        response=await transport.handle_async_request(request)
        clock[0]+=9.98
        remainders.append(deadline.remaining_seconds())
        return response
    monkeypatch.setattr(sdk,'new_http_transport',lambda:httpx.MockTransport(send))
    class Task:
        settings=SimpleNamespace(llm_task_deadline_seconds=10.0)
        @llm.bounded_llm_task
        async def run(self):
            obj=make()
            monkeypatch.setattr(obj,'retry_delay_seconds',lambda *a,**k:10)
            return await obj.chat([])
    wire[1].append(httpx.Response(503,json={'error':{}}))
    previous=TASK_DEADLINE.get()
    started=time.monotonic()
    with pytest.raises(LLMTaskDeadlineError) as caught:await Task().run()
    assert len(wire[0])==1 and time.monotonic()-started<0.5
    assert remainders==pytest.approx([0.02])
    assert caught.value.transport_attempts==1
    assert TASK_DEADLINE.get()==previous

async def test_fallback_receives_remaining_not_fresh_deadline(wire,monkeypatch):
    from app.engine import llm_transport_deadline as deadline

    # Bootstrap/lazy SDK route work is not the invariant under test. Warm both
    # real native adapters, then control only application clock consumption.
    wire[1].extend([httpx.Response(200,json=native('openai')),
                    httpx.Response(200,json=native('xai'))])
    await make().chat([])
    await make('xai','grok-4.3').chat([])
    clock=[time.monotonic()]
    application_clock=SimpleNamespace(monotonic=lambda:clock[0])
    monkeypatch.setattr(llm,'time',application_clock)
    monkeypatch.setattr(deadline,'time',application_clock)
    deadlines=[]
    remaining=[]
    async def send(request):
        deadlines.append(TASK_DEADLINE.get())
        remaining.append(deadline.remaining_seconds())
        if len(deadlines)==1:
            clock[0]+=9.0
            return httpx.Response(503,json={'error':{}})
        # The fallback has only the shared remainder, not a new full budget.
        await asyncio.sleep(2)
        return httpx.Response(200,json=native('xai'))
    monkeypatch.setattr(sdk,'new_http_transport',lambda:httpx.MockTransport(send))
    class Task:
        settings=SimpleNamespace(llm_task_deadline_seconds=10.0)
        @llm.bounded_llm_task
        async def run(self):
            try:await make(retry_attempts=1).chat([])
            except llm.LLMUnavailableError:await make('xai','grok-4.3').chat([])
    previous=TASK_DEADLINE.get()
    with pytest.raises(LLMTaskDeadlineError) as caught:await Task().run()
    assert len(deadlines)==2 and deadlines[0]==deadlines[1]
    assert remaining==pytest.approx([10.0,1.0])
    assert caught.value.transport_attempts==2
    assert TASK_DEADLINE.get()==previous

def test_deadline_remaining_monotonic_clock(monkeypatch):
    from app.engine import llm_transport_deadline as deadline
    clock=[100.0]
    monkeypatch.setattr(deadline,'time',SimpleNamespace(monotonic=lambda:clock[0]))
    token=TASK_DEADLINE.set(103.0)
    try:
        assert deadline.remaining_seconds()==3.0
        clock[0]=102.5
        assert deadline.remaining_seconds()==0.5
        clock[0]=103.0
        with pytest.raises(LLMTaskDeadlineError):deadline.remaining_seconds()
    finally:TASK_DEADLINE.reset(token)

@pytest.mark.parametrize('value',[0,-1,float('inf'),float('nan'),None])
def test_invalid_deadline_is_typed_terminal(value):
    from app.engine.llm_transport_deadline import deadline_seconds
    with pytest.raises(llm.LLMConfigurationError):deadline_seconds(SimpleNamespace(llm_task_deadline_seconds=value))
