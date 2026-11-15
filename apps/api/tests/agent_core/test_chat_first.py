"""Natural-chat defaults retain bounded reads and explicit advanced restrictions."""
import asyncio
import pytest
from sqlmodel import Session
from app.agent.contracts import AgentError
from app.agent.operations import AgentOperations
from app.db.models import Character, ModelProviderAccount, ModelOption


def test_registered_account_aliases_match_available_chat_models(service):
    with Session(service.engine) as session:
        session.add(ModelProviderAccount(id='local-a',provider_type='openai_compatible',alias='My configured local account',base_url='http://127.0.0.1:9999/v1',preset='custom'))
        session.add(ModelOption(id='local-o',key='local-option',provider_account_id='local-a',provider_type='openai_compatible',model='local-model',label='Local model'))
        session.commit()
    view=service.settings_view()
    assert view['llm_accounts']==[{'id':'local-a','alias':'My configured local account','provider_type':'openai_compatible'}]
    assert view['llm_options'][0]['provider_account_id']==view['llm_accounts'][0]['id']
    assert 'api_key' not in view['llm_accounts'][0]


def test_plain_message_needs_no_manual_entity_or_field_selection(service,owner):
    with Session(service.engine) as session:
        session.add(Character(id='c',name='C',persona='Navigator',description='Old'))
        session.commit()
    sid=service.create_session(owner,'plain-chat')['id']
    service.accept_run(owner,sid,'plain-turn','Find C and update the description',[])
    ops=AgentOperations(service,owner,sid)
    result=asyncio.run(ops.execute('lore_get_character',{'id':'c','fields':['name','description']}))
    assert result['fields']=={'name':'C','description':'Old'}
    draft=asyncio.run(ops.execute('lore_stage_character',{'target_id':'c','fields':{'description':'New'}}))
    assert draft['status']=='draft' and draft['session_id']==sid
    with Session(service.engine) as session:assert session.get(Character,'c').description=='Old'
    with pytest.raises(AgentError):
        asyncio.run(ops.execute('lore_commit_draft',{'draft_id':draft['id'],'request_id':'unapproved','expected_version':1,'preview_hash':draft['preview_hash']}))


def test_automatic_context_targets_are_bounded(service,owner):
    with Session(service.engine) as session:
        for n in range(9):session.add(Character(id=f'c{n}',name=f'C{n}',persona='P'))
        session.commit()
    sid=service.create_session(owner,'bounded-chat')['id']
    service.accept_run(owner,sid,'bounded-turn','Inspect relevant characters',[])
    ops=AgentOperations(service,owner,sid)
    for n in range(8):asyncio.run(ops.execute('lore_get_character',{'id':f'c{n}','fields':['name']}))
    # Re-reading an admitted target does not consume another target slot.
    asyncio.run(ops.execute('lore_get_character',{'id':'c0','fields':['persona']}))
    with pytest.raises(AgentError):asyncio.run(ops.execute('lore_get_character',{'id':'c8','fields':['name']}))
