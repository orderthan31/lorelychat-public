"""Provider-free regression contracts; run through the guarded snapshot runner."""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
from app.schemas.characters import CharacterCreate, CharacterUpdate
from app.schemas.assets import CharacterAssetCreate, CharacterAssetUpdate
from app.schemas.world_settings import WorldSettingCreate, WorldSettingUpdate
from app.schemas.presets import PresetUpdate
from app.schemas.conversations import ConversationCreate, MessageCreate
from app.schemas.chat_commands import ChatCommandCreate, ChatCommandUpdate

@pytest.mark.parametrize('value', [[], True, 1, 'bad'])
def test_trait_wrong_container_http(value):
    app = FastAPI()
    @app.post('/')
    def create(payload: CharacterCreate): return payload
    with TestClient(app, raise_server_exceptions=False) as client:
        assert client.post('/', json={'name':'n','persona':'p','trait_scores':value}).status_code == 422

@pytest.mark.parametrize('value', [True, 1, {}, ['ok', {}]])
def test_tags_wrong_container_http(value):
    app = FastAPI()
    @app.post('/')
    def create(payload: CharacterAssetCreate): return payload
    with TestClient(app, raise_server_exceptions=False) as client:
        assert client.post('/', json={'label':'n','image_url':'/uploads/a.png','tags':value}).status_code == 422

def test_tag_compatibility_and_trait_defaults():
    assert CharacterAssetCreate(label='x',image_url='x',tags='a,b\nc').tags == ['a','b','c']
    assert CharacterCreate(name='x',persona='x',trait_scores=None).trait_scores['confidence'] == 3

@pytest.mark.parametrize('model,fields', [
    (CharacterUpdate,['name','persona','tts_provider','tts_model','tts_voice_style']),
    (CharacterAssetUpdate,['asset_type','label','image_url','priority','enabled','is_default']),
    (WorldSettingUpdate,['title','genre_mode','enabled']),
    (PresetUpdate,['title','content','preset_type','enabled']),
    (ChatCommandUpdate,['name','description','prompt','generation_prompt','postprocess_prompt','postprocess_target','postprocess_probability','enabled','priority']),
])
def test_patch_required_null_but_omission_preserved(model,fields):
    assert model().model_dump(exclude_unset=True) == {}
    for field in fields:
        with pytest.raises(ValidationError): model(**{field:None})

@pytest.mark.parametrize('model,kwargs',[(CharacterCreate,{'name':'  ','persona':'p'}),(WorldSettingCreate,{'title':'  '}),(WorldSettingUpdate,{'title':'  '})])
def test_blank_required(model,kwargs):
    with pytest.raises(ValidationError): model(**kwargs)

def test_large_valid_authored_input_is_lossless():
    text='한글 authored story. '*3000
    assert CharacterCreate(name='n',persona=text).persona == text
    assert MessageCreate(speaker_type='user',speaker_id='u',content=text).content == text

@pytest.mark.parametrize('kind',['prose','participants','blocks','depth','nodes'])
def test_resource_rejects(kind):
    with pytest.raises(ValidationError):
        if kind=='prose': CharacterCreate(name='n',persona='x'*262145)
        elif kind=='participants': ConversationCreate(mode='group',participants=[{'type':'character','id':'c'}]*65)
        elif kind=='blocks': ChatCommandCreate(name='x',prompt='x',postprocess_block_types=['text']*65)
        elif kind=='depth':
            value={}
            for _ in range(18): value={'v':value}
            MessageCreate(speaker_type='user',speaker_id='u',metadata=value)
        else: MessageCreate(speaker_type='user',speaker_id='u',metadata={'v':list(range(8193))})

def test_context_sources_empty_is_distinct():
    # Existing field name is retained; [] is an intentional context opt-out.
    assert ChatCommandCreate(name='x',prompt='x',postprocess_context_options=[]).postprocess_context_options == []
    assert ChatCommandCreate(name='x',prompt='x').postprocess_context_options
