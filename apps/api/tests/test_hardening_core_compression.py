import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlmodel import SQLModel, Session
from app.db.models import Conversation, Message, SceneState
from app.db.session import make_engine
from app.services import conversation_service as svc
from app.services.context_management_service import ContextMaintenanceRequired, resolve_model_context_capacity
from app.engine.prompt_harness import approx_tokens
from app.engine.harness_comparison import rendered_input_tokens
import json


@pytest.mark.parametrize('field', ['content','action','raw_body'])
def test_fold_content_edit_during_provider_is_stale_and_prior_arc_survives(tmp_path, monkeypatch, field):
    engine=make_engine('sqlite:///'+str(tmp_path/'content.db'));SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        scene=SceneState(conversation_id='fold_room',summary='Prior Arc must survive')
        session.add(Conversation(id='fold_room',title='Synthetic',mode='user_character'));session.add(scene)
        now=datetime.now(timezone.utc)
        for n in range(14):
            session.add(Message(id=f'fold_{n:02}',conversation_id='fold_room',speaker_type='character',speaker_id='char',content=f'Original public fact {n}',created_at=now+timedelta(seconds=n), metadata_={'response_protocol':'free_body_json','raw_body':f'Original public fact {n}','render_parts':[{'type':'dialogue','text':f'Original public fact {n}'}]} if field=='raw_body' else {}))
        session.commit()
        rows=svc.list_messages(session,'fold_room')
        observed=[]
        async def fake(**kwargs):
            observed.append(session.in_transaction())
            with Session(engine) as edit:
                msg=edit.get(Message,'fold_00')
                if field=='raw_body':
                    msg.metadata_={**msg.metadata_,'raw_body':'Edited new canon','render_parts':[{'type':'dialogue','text':'Edited new canon'}]}
                else:setattr(msg,field,'Edited new canon')
                edit.add(msg);edit.commit()
            assert kwargs['recent_messages'][0].content=='Original public fact 0'
            await asyncio.sleep(0)
            return {'scene':{'summary':'Stale Arc based on the original text'}}
        monkeypatch.setattr(svc,'summarize_conversation_state_with_llm',fake)
        asyncio.run(svc.update_scene_orchestration_summary(session,'fold_room',rows,llm_client=object(),character_ids=['char']))
        session.expire_all();saved=session.get(SceneState,'fold_room')
        assert saved.summary=='Prior Arc must survive'
        assert saved.compression_revision==0 and saved.last_compression_source_message_id is None
        assert observed==[False], 'read transaction must end before the provider await'
        assert (session.get(Message,'fold_00').metadata_['raw_body'] if field=='raw_body' else getattr(session.get(Message,'fold_00'),field))=='Edited new canon'


@pytest.mark.parametrize('strategy', ['fast','quality'])
def test_selected_small_compressor_rejects_indivisible_large_turn_without_clipping(session, strategy):
    from app.db.models import ModelOption
    cid='small_compressor'
    session.add(Conversation(id=cid,title='Synthetic complete group',mode='user_character'))
    session.add(SceneState(conversation_id=cid,summary='Prior Arc'))
    session.add(ModelOption(id='compressor',key='small-compression',provider_account_id='synthetic',provider_type='synthetic',model='small',label='Small compressor',context_window_tokens=1024,max_output_tokens=128))
    now=datetime.now(timezone.utc)
    rows=[Message(id='complete_source',conversation_id=cid,speaker_type='user',speaker_id='u',content='Full source beginning',created_at=now)]
    for n in range(60):
        rows.append(Message(id=f'large_{n:02}',conversation_id=cid,speaker_type='character',speaker_id='c',content=f'Original indivisible evidence {n} '+('完整 evidence. '*40),generation_job_id='huge_job',reply_index=n,created_at=now+timedelta(seconds=n+1)))
    for n in range(12):rows.append(Message(id=f'tail_{n:02}',conversation_id=cid,speaker_type='character',speaker_id='c',content=f'Protected latest raw batch {n}',created_at=now+timedelta(seconds=n+100)))
    session.add_all(rows);session.commit()
    original={row.id:row.content for row in rows}
    class Compressor:
        overrides={'model_option_key':'small-compression'}
        def __init__(self):self.calls=[]
        def max_output_tokens(self):return 128
        async def chat(self,messages,**kw):self.calls.append(messages);return SimpleNamespace(content='This must not be requested')
    client=Compressor()
    selected,_=svc.select_incremental_compression_batch(session.get(SceneState,cid),rows,job_source_message_ids={'huge_job':'complete_source'})
    assert len(selected)>48 and selected[-1].id=='large_59'
    result=asyncio.run(svc.update_scene_orchestration_summary(session,cid,rows,llm_client=client,character_ids=['c'],compression_strategy=strategy,job_source_message_ids={'huge_job':'complete_source'}))
    assert client.compression_context_capacity.context_window_tokens==1024
    assert client.compression_context_capacity.completion_reserve_tokens==128
    assert client.calls==[]
    assert result.summary=='Prior Arc' and result.compression_revision==0 and result.last_compression_source_message_id is None
    assert 'context_maintenance_required' in result.last_compression_error
    assert {row.id:row.content for row in svc.list_messages(session,cid)}==original


@pytest.mark.parametrize('stage', ['extraction','critic','synthesis'])
def test_compression_actual_stage_exact_fit_and_one_over(stage):
    messages=[{'role':'system','content':'Contract for '+stage},{'role':'user','content':'Complete source evidence; not clipped.\n'+stage*17}]
    schema={'type':'json_object','schema':{'required':['events'],'properties':{'events':{'type':'array'}}}} if stage!='synthesis' else None
    cost=rendered_input_tokens(messages,history_serialization='native')+(approx_tokens(json.dumps(schema,ensure_ascii=False,sort_keys=True,separators=(',',':'))) if schema else 0)
    cap=resolve_model_context_capacity(None,mandatory_reserve_tokens=0)
    class Client:
        def __init__(self,budget):self.compression_context_capacity=replace(cap,working_input_tokens=budget);self.calls=[]
        async def chat(self, supplied, **kw):self.calls.append(supplied);return SimpleNamespace(content='synthetic')
    good=Client(cost)
    asyncio.run(svc._call_compression_stage(good,SceneState(conversation_id='cap'),messages,response_format=schema))
    assert good.calls==[messages]
    bad=Client(cost-1)
    with pytest.raises(ContextMaintenanceRequired):
        asyncio.run(svc._call_compression_stage(bad,SceneState(conversation_id='cap'),messages,response_format=schema))
    assert bad.calls==[]
