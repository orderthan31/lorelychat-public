import concurrent.futures
import json
import multiprocessing
import os
import pytest
from app.services import provider_secret_service as secrets

@pytest.fixture
def store(tmp_path,monkeypatch):
    monkeypatch.setattr(secrets,'SECRET_ROOT',tmp_path)
    monkeypatch.setattr(secrets,'SECRET_FILE',tmp_path/'provider_secrets.json')
    return tmp_path

def _save(index):
    return secrets.save_secret('synthetic-'+str(index))[0]

def test_concurrent_threads_no_lost_updates(store):
    with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
        refs=list(pool.map(_save,range(80)))
    data=json.loads(secrets.SECRET_FILE.read_text())
    assert len(data)==len(refs)==80
    assert all(ref in data for ref in refs)
    assert secrets.SECRET_FILE.stat().st_mode & 0o777 == 0o600

def _configure_store(path):
    from pathlib import Path
    secrets.SECRET_ROOT = Path(path)
    secrets.SECRET_FILE = Path(path) / 'provider_secrets.json'

def test_concurrent_processes_no_lost_updates(store):
    with multiprocessing.get_context('spawn').Pool(6, initializer=_configure_store, initargs=(str(store),)) as pool:
        refs=pool.map(_save,range(40))
    assert len(json.loads(secrets.SECRET_FILE.read_text()))==len(refs)==40

@pytest.mark.parametrize('data',['{broken','[]','{"x":42}','{"x":"one","x":"two"}'])
def test_corruption_fails_closed_without_destroying(store,data):
    secrets.SECRET_FILE.write_text(data)
    with pytest.raises(RuntimeError,match='Secret store'):
        secrets.save_secret('synthetic')
    assert secrets.SECRET_FILE.read_text()==data

def test_failed_replace_keeps_previous_file(store,monkeypatch):
    ref,_=secrets.save_secret('old-synthetic')
    def fail(*a,**kw):raise OSError('synthetic replace failure')
    monkeypatch.setattr(os,'replace',fail)
    with pytest.raises(RuntimeError):secrets.save_secret('new-synthetic')
    assert secrets.get_secret(ref)=='old-synthetic'
    assert len(json.loads(secrets.SECRET_FILE.read_text()))==1

def test_rotation_sql_failure_preserves_old_secret(store,monkeypatch):
    from app.services import model_provider_service as service
    from app.db.models import ModelProviderAccount
    from app.schemas.model_providers import ProviderAccountUpdate
    old,_=secrets.save_secret('old-synthetic')
    account=ModelProviderAccount(id='synthetic',provider_type='openai',alias='test',api_key_secret_ref=old)
    class BrokenSession:
        def get(self,*a):return account
        def add(self,*a):pass
        def commit(self):raise RuntimeError('synthetic SQL failure')
        def rollback(self):pass
    with pytest.raises(RuntimeError,match='synthetic SQL failure'):
        service.update_account(BrokenSession(),account.id,ProviderAccountUpdate(api_key='new-synthetic'))
    assert secrets.get_secret(old)=='old-synthetic'
    assert len(json.loads(secrets.SECRET_FILE.read_text()))==1


def test_mixed_update_delete_preserves_unrelated_records(store):
    refs=[secrets.save_secret('old-synthetic')[0] for _ in range(60)]
    def mutate(index):
        if index % 2:
            secrets.delete_secret(refs[index])
        else:
            secrets.update_secret(refs[index],'new-synthetic')
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
        list(pool.map(mutate,range(60)))
    data=json.loads(secrets.SECRET_FILE.read_text())
    assert len(data)==30
    assert all(refs[i] in data for i in range(0,60,2))
    assert all(value=='new-synthetic' for value in data.values())


def test_create_sql_failure_compensates_new_secret(store,session,monkeypatch):
    from app.services import model_provider_service as service
    from app.schemas.model_providers import ProviderAccountCreate
    def fail():raise RuntimeError('synthetic SQL failure')
    monkeypatch.setattr(session,'commit',fail)
    with pytest.raises(RuntimeError,match='synthetic SQL failure'):
        service.create_account(session,ProviderAccountCreate(provider_type='openai',alias='synthetic',api_key='synthetic-new'))
    assert json.loads(secrets.SECRET_FILE.read_text())=={}


def test_rotation_and_clear_commit_readback(store,session,monkeypatch):
    from app.services import model_provider_service as service
    from app.schemas.model_providers import ProviderAccountCreate, ProviderAccountUpdate
    from app.db.models import ModelProviderAccount
    monkeypatch.setattr(service,'sync_models',lambda *a:[])
    account=service.create_account(session,ProviderAccountCreate(provider_type='openai',alias='synthetic',api_key='synthetic-old'))
    old=account.api_key_secret_ref
    service.update_account(session,account.id,ProviderAccountUpdate(api_key='synthetic-new'))
    session.expire_all()
    account=session.get(ModelProviderAccount,account.id)
    current=account.api_key_secret_ref
    assert current!=old and secrets.get_secret(current)=='synthetic-new' and secrets.get_secret(old) is None
    service.update_account(session,account.id,ProviderAccountUpdate(clear_api_key=True))
    session.expire_all()
    assert session.get(ModelProviderAccount,account.id).api_key_secret_ref is None
    assert secrets.get_secret(current) is None


def test_clear_sql_failure_preserves_old_secret(store,session,monkeypatch):
    from app.services import model_provider_service as service
    from app.schemas.model_providers import ProviderAccountUpdate
    from app.db.models import ModelProviderAccount
    old,_=secrets.save_secret('synthetic-old')
    account=ModelProviderAccount(id='synthetic',provider_type='openai',alias='synthetic',api_key_secret_ref=old)
    session.add(account);session.commit()
    def fail():raise RuntimeError('synthetic SQL failure')
    monkeypatch.setattr(session,'commit',fail)
    with pytest.raises(RuntimeError,match='synthetic SQL failure'):
        service.update_account(session,account.id,ProviderAccountUpdate(clear_api_key=True))
    assert session.get(ModelProviderAccount,'synthetic').api_key_secret_ref==old
    assert secrets.get_secret(old)=='synthetic-old'
