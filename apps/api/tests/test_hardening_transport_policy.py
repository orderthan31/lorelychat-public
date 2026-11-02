"""Hardening acceptance: actual pinned SDK; synthetic HTTP only."""
import asyncio
import json
import pytest
import httpx
from app.engine.llm_client import LLMClient, LLMUnavailableError, bounded_llm_task, task_attempt_metadata
from app.engine import llm_error_types as errors
from app.engine import litellm_transport as sdk
from test_litellm_transport import make, native, wire

@pytest.mark.parametrize('status,kind', [(400,'LLMRequestError'),(401,'LLMAuthenticationError'),(403,'LLMAuthenticationError'),(404,'LLMConfigurationError'),(422,'LLMRequestError')])
async def test_terminal_http_no_retry_no_circuit(wire,status,kind):
    obj=make(circuit_breaker_enabled=True)
    obj.reset_circuit_breakers()
    wire[1].append(httpx.Response(status,json={'error':{'message':'PRIVATE_SENTINEL'}}))
    with pytest.raises(getattr(errors,kind)) as caught:
        await obj.chat([{'role':'user','content':'synthetic'}])
    assert not isinstance(caught.value,(ValueError,LLMUnavailableError))
    assert caught.value.transport_attempts==1
    assert len(wire[0])==1
    assert 'PRIVATE_SENTINEL' not in str(caught.value)
    from app.engine.llm_client import _CIRCUIT_STATES
    state = _CIRCUIT_STATES.get(obj.circuit_key(), {})
    assert state.get('failures', 0) == 0 and state.get('open_until', 0) == 0

@pytest.mark.parametrize('provider,payload', [('google',{'promptFeedback':{'blockReason':'SAFETY'}}),('openai',{'choices':[{'message':{'role':'assistant','content':None,'refusal':'PRIVATE_SENTINEL'},'finish_reason':'stop'}]}),('openai',native('openai',finish='content_filter')),('anthropic',native('anthropic',finish='refusal'))])
async def test_policy_block_terminal(wire,provider,payload):
    wire[1].append(httpx.Response(200,json=payload))
    with pytest.raises(errors.LLMPolicyError) as caught:
        await make(provider,model={'google':'gemini-2.5-flash','anthropic':'claude-sonnet-4-5'}.get(provider,'gpt-4o')).chat([{'role':'user','content':'synthetic'}])
    assert caught.value.transport_attempts==1 and len(wire[0])==1
    assert 'PRIVATE_SENTINEL' not in str(caught.value)

async def test_native_failure_accounted(wire,monkeypatch):
    recorded=[]
    monkeypatch.setattr(LLMClient,'_record_transport_failure',lambda self,exc,**kw:recorded.append(exc))
    wire[1].append(httpx.Response(200,json={'choices':[]}))
    with pytest.raises(errors.LLMResponseEvidenceError):
        await make().chat([{'role':'user','content':'synthetic'}])
    assert len(recorded)==1 and recorded[0].transport_attempts==1

async def test_capability_preflight_zero_send(wire):
    with pytest.raises(errors.LLMCapabilityError):
        await make(supports_json=False).chat([],response_format={'type':'json_object'})
    assert not wire[0]

async def test_keyless_contract(wire):
    wire[1].append(httpx.Response(200,json=native('openai')))
    obj=make('openai_compatible',base_url='http://127.0.0.1:11434/v1',api_key=None,keyless_local=True)
    await obj.chat([])
    assert str(wire[0][0].url)=='http://127.0.0.1:11434/v1/chat/completions'
    with pytest.raises(errors.LLMConfigurationError):
        sdk.resolve_route(make('openai_compatible',api_key=None,keyless_local=True))

async def test_task_deadline_spans_repair_and_nested_tasks(wire):
    from app.engine.llm_client import _TASK_BUDGET
    @bounded_llm_task
    async def nested():
        return _TASK_BUDGET.get()
    @bounded_llm_task
    async def run():
        budget=_TASK_BUDGET.get()
        assert await nested() is budget
        budget.deadline=__import__('time').monotonic()+0.02
        await make()._post_with_retries(lambda:asyncio.sleep(1),endpoint_label='synthetic')
    with pytest.raises(errors.LLMTaskDeadlineError):
        await run()

async def test_half_open_single_owner_and_stale_success():
    obj=make(circuit_breaker_enabled=True,circuit_failure_threshold=1)
    obj.reset_circuit_breakers()
    old=obj.ensure_circuit_available()
    obj.record_circuit_failure()
    obj.record_circuit_success(old)
    from app.engine.llm_client import _CIRCUIT_STATES, CircuitOpenError
    state=_CIRCUIT_STATES[obj.circuit_key()]
    state['open_until']=0.00001
    token=obj.ensure_circuit_available()
    with pytest.raises(CircuitOpenError):obj.ensure_circuit_available()
    obj.record_circuit_success(token)
    obj.ensure_circuit_available()

def test_route_identity_isolation():
    a=make(model_option_key='a',provider_config_revision='one')
    assert a.circuit_key()!=make(model_option_key='b',provider_config_revision='one').circuit_key()
    assert a.circuit_key()!=make(model_option_key='a',provider_config_revision='two').circuit_key()
    assert a.circuit_key()!=make(model_option_key='a',provider_config_revision='one',base_url='https://api.openai.com/v1/').circuit_key()

@pytest.mark.parametrize('protocol',['legacy_json','free_body_json','tagged_text'])
@pytest.mark.parametrize('failure',[400,401,403,404,422,'block','native','billing'])
async def test_runtime_terminal_no_repair_no_secondary(wire,monkeypatch,protocol,failure):
    from app.core.config import get_settings
    from app.engine.character_runtime import CharacterRuntime
    from app.db.models import Character
    monkeypatch.setenv('CHAT_RESPONSE_PROTOCOL',protocol)
    get_settings.cache_clear()
    if failure=='block':
        payload=httpx.Response(200,json={'promptFeedback':{'blockReason':'SAFETY'}})
        primary=make('google','gemini-2.5-flash',circuit_breaker_enabled=True)
    elif failure=='native':
        payload=httpx.Response(200,json={'choices':[]})
        primary=make(circuit_breaker_enabled=True)
    elif failure=='billing':
        payload=httpx.Response(429,json={'error':{'code':'insufficient_quota','message':'PRIVATE_SENTINEL'}})
        primary=make(circuit_breaker_enabled=True)
    else:
        payload=httpx.Response(failure,json={'error':{'message':'PRIVATE_SENTINEL'}})
        primary=make(circuit_breaker_enabled=True)
    primary.reset_circuit_breakers()
    wire[1].append(payload)
    writes=[];snapshots=[]
    with pytest.raises(errors.LLMTerminalError):
        await CharacterRuntime(primary,make('xai','grok-4.3')).generate_replies(
            characters=[Character(id='char_1',name='Synthetic')],recent_messages=[],user_message='synthetic',
            persist_replies=writes.append,prompt_snapshot_callback=snapshots.append)
    assert not writes and len(wire[0])==1
    assert len(snapshots)==1 and snapshots[0]['metadata']['network_attempts']==1
    assert snapshots[0]['metadata']['fallback_used'] is False
    from app.engine.llm_client import _CIRCUIT_STATES
    assert _CIRCUIT_STATES[primary.circuit_key()]['failures']==0
