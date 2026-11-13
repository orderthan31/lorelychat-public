"""Offline Vertex account and installed-SDK wire acceptance (no paid calls)."""
import json
import pytest
from app.schemas.model_providers import ProviderAccountCreate, ProviderAccountUpdate, ModelOptionManualCreate
from app.services import model_provider_service as service


def adc_payload(**kwargs):
    return ProviderAccountCreate(provider_type='vertex_ai', alias='Vertex',
        vertex_project='test-project', vertex_location='us-central1', vertex_auth_mode='adc', **kwargs)


def test_adc_account_crud_and_manual_catalog(session):
    account = service.create_account(session, adc_payload())
    read = service.serialize_account(account)
    assert read.vertex_project == 'test-project'
    assert read.vertex_location == 'us-central1'
    assert read.vertex_auth_mode == 'adc'
    assert read.configured and read.api_key_hint is None
    assert service.list_options(session, provider_account_id=account.id) == []
    option = service.create_manual_option(session, ModelOptionManualCreate(
        provider_account_id=account.id, model='gemini-2.5-flash'))
    assert option.supports_chat and option.supports_compression
    assert not option.supports_decision and not option.supports_tts
    service.update_account(session, account.id, ProviderAccountUpdate(vertex_location='global'))
    assert service.serialize_account(account).vertex_location == 'global'
    tested = service.test_account(session, account.id)
    assert '인증' in tested.message and '확인하지' in tested.message


@pytest.mark.parametrize('field,value', [('vertex_project','../bad'), ('vertex_project','x'),
    ('vertex_location','https://evil.invalid'), ('vertex_location','us-central1/../'),
    ('vertex_location','not-a-region')])
def test_vertex_invalid_configuration(session, field, value):
    values = dict(provider_type='vertex_ai', alias='Vertex', vertex_project='test-project',
        vertex_location='us-central1', vertex_auth_mode='adc')
    values[field] = value
    with pytest.raises(ValueError):
        service.create_account(session, ProviderAccountCreate(**values))


@pytest.mark.parametrize('model', ['claude-sonnet-4', 'publishers/google/models/gemini-2.5-flash',
    'gemini-2.5-flash-preview-tts','gemini-2.5-flash-image','gemini-decision-1'])
def test_vertex_only_text_gemini_catalog(session, model):
    account = service.create_account(session, adc_payload())
    with pytest.raises(ValueError):
        service.create_manual_option(session, ModelOptionManualCreate(provider_account_id=account.id, model=model))


def test_vertex_credentials_never_accept_paths_or_external_accounts(session):
    for value in ['/private/account.json', 'short-token', '{"type":"external_account","token_url":"https://evil.invalid"}']:
        with pytest.raises(ValueError) as caught:
            service.create_account(session, ProviderAccountCreate(provider_type='vertex_ai', alias='Vertex',
                vertex_project='test-project', vertex_location='global', api_key=value))
        assert value not in str(caught.value)


def test_adc_rejects_uploaded_secret(session):
    with pytest.raises(ValueError):
        service.create_account(session, adc_payload(api_key='secret-sentinel'))


def test_additive_migration_preserves_legacy_rows(tmp_path):
    from sqlalchemy import text
    from sqlmodel import create_engine
    from app.db.session import migrate_sqlite_columns
    engine = create_engine('sqlite:///' + str(tmp_path / 'legacy.db'))
    with engine.begin() as conn:
        conn.execute(text('CREATE TABLE model_provider_accounts (id VARCHAR PRIMARY KEY, provider_type VARCHAR, alias VARCHAR, api_key_secret_ref VARCHAR)'))
        conn.execute(text("INSERT INTO model_provider_accounts VALUES ('legacy','google','Untouched','existing-ref')"))
    migrate_sqlite_columns(engine)
    migrate_sqlite_columns(engine)
    with engine.connect() as conn:
        row = conn.execute(text('SELECT * FROM model_provider_accounts')).one()
        assert tuple(row) == ('legacy','google','Untouched','existing-ref',None,None,None)
    engine.dispose()


def test_runtime_overrides_adc_and_no_primary_changes(session):
    from app.db.models import RuntimeSetting
    from app.services.runtime_settings_service import _dynamic_llm_overrides
    from sqlmodel import select
    before = list(session.exec(select(RuntimeSetting)).all())
    account = service.create_account(session, adc_payload())
    option = service.create_manual_option(session, ModelOptionManualCreate(provider_account_id=account.id,model='gemini-2.5-flash'))
    values = _dynamic_llm_overrides(session, option.key, 'chat')
    assert values['provider']=='vertex_ai' and values['vertex_project']=='test-project'
    assert values['vertex_auth_mode']=='adc' and 'api_key' not in values
    assert list(session.exec(select(RuntimeSetting)).all()) == before
    assert _dynamic_llm_overrides(session, option.key, 'compression')['provider']=='vertex_ai'
    assert service.active_options_for_runtime(session, 'tts')==[]
    assert service.active_options_for_runtime(session, 'decision')==[]
    with pytest.raises(ValueError,match='manual'):
        service.sync_models(session,account.id)
