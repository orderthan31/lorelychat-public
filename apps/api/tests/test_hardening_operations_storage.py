import asyncio
import io
import json
import sqlite3
from pathlib import Path
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlmodel import SQLModel, Session, create_engine, select
from sqlalchemy.pool import StaticPool
from app.db.models import Character, CharacterAsset, WorldSetting, ChatCommand, ConversationParticipant, Message

@pytest.fixture
def session():
    engine=create_engine('sqlite://',connect_args={'check_same_thread':False},poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as value: yield value
    engine.dispose()


def test_db_pagination_filters_count_order_and_limit(session):
    from app.api.characters import list_characters
    from app.services import character_service
    statements=[]
    for index in range(211):session.add(Character(id=f'c{index:03}',name=f'N{index:03}',persona='needle' if index%2 else 'other'))
    session.commit()
    event.listen(session.bind,'before_cursor_execute',lambda c,u,sql,p,ctx,m:statements.append(sql))
    result=list_characters(paginated=True,page=999,page_size=10,q='needle',session=session)
    assert result.total==105 and result.page==11 and len(result.items)==5
    selects=[s for s in statements if s.lstrip().upper().startswith('SELECT')]
    assert len(selects)==2 and 'count(' in selects[0].lower() and 'LIMIT' in selects[1].upper()
    assert result.items[-1].name=='N209'


def test_usage_uses_sql_aggregates(session):
    from app.services.character_service import list_characters_by_usage
    session.add(Character(id='c',name='C',persona='p'))
    for i in range(120):session.add(Message(id=f'm{i}',conversation_id='r',speaker_type='character',speaker_id='c',content='x'))
    session.add(ConversationParticipant(conversation_id='r',participant_type='character',participant_id='c'))
    session.commit();queries=[]
    event.listen(session.bind,'before_cursor_execute',lambda c,u,sql,p,ctx,m:queries.append(sql))
    result=list_characters_by_usage(session)
    assert result[0]['message_count']==120 and result[0]['room_count']==1
    assert any('GROUP BY' in sql for sql in queries)
    assert not any('SELECT messages.speaker_id \nFROM messages \nWHERE' in sql and 'GROUP BY' not in sql for sql in queries)


def test_asset_upload_lifecycle(session,tmp_path,monkeypatch):
    from app.api import assets
    from app.db.session import get_session
    from test_hardening_operations_uploads_logging import png
    monkeypatch.setattr(assets,'ASSET_DIR',tmp_path/'character-assets')
    monkeypatch.setattr(assets,'UPLOAD_ROOT',tmp_path)
    app=FastAPI();app.include_router(assets.router);app.dependency_overrides[get_session]=lambda: session
    with TestClient(app,raise_server_exceptions=False) as client:
        response=client.post('/characters/missing/assets/upload',files={'file':('a.png',png(),'image/png')})
        assert response.status_code==404 and not list(tmp_path.rglob('*.png'))
        session.add(Character(id='c',name='C',persona='p'));session.commit()
        original=assets.asset_service.create_asset
        def fail(*a,**kw):raise RuntimeError('synthetic DB registration failure')
        monkeypatch.setattr(assets.asset_service,'create_asset',fail)
        assert client.post('/characters/c/assets/upload',files={'file':('a.png',png(),'image/png')}).status_code==500
        assert not list(tmp_path.rglob('*.png'))
        monkeypatch.setattr(assets.asset_service,'create_asset',original)
        response=client.post('/characters/c/assets/upload',files={'file':('a.png',png(),'image/png')},data={'is_default':'true'})
        assert response.status_code==200,response.text
        assert len(list(tmp_path.rglob('*.png')))==1
        assert session.get(Character,'c').avatar_url==response.json()['image_url']


def test_restore_manifest_checksum_and_full_bundle(tmp_path):
    from app.ops.sqlite_ops import online_backup, verify_restore, REQUIRED_RUNTIME_TABLES
    from app.ops.backup_bundle import create_bundle, verify_bundle
    database=tmp_path/'source.db'
    with sqlite3.connect(database) as db:
        db.execute('PRAGMA journal_mode=WAL')
        for table in REQUIRED_RUNTIME_TABLES:db.execute(f'CREATE TABLE {table}(id TEXT)')
        db.execute("INSERT INTO messages VALUES ('WAL sentinel')")
        result=online_backup(database,tmp_path/'backups',keep=100)
        assert verify_restore(result.backup).sha256==result.sha256
    uploads=tmp_path/'uploads';uploads.mkdir();(uploads/'a.png').write_bytes(b'synthetic media')
    secrets=tmp_path/'secrets';secrets.mkdir();(secrets/'provider.json').write_text('{"synthetic":"only"}')
    config=tmp_path/'config.env';config.write_text('LLM_MOCK=true\n')
    bundle=create_bundle(database,uploads,secrets,config,tmp_path/'bundle',quiesced=True)
    report=verify_bundle(bundle)
    assert report['integrity']=='ok' and report['files_verified']>=4
    (Path(bundle)/'uploads/a.png').write_bytes(b'tampered')
    with pytest.raises(ValueError):verify_bundle(bundle)


def test_restore_rejects_modified_backup_manifest(tmp_path):
    from app.ops.sqlite_ops import online_backup,verify_restore,REQUIRED_RUNTIME_TABLES
    db=tmp_path/'db'
    with sqlite3.connect(db) as conn:
        for table in REQUIRED_RUNTIME_TABLES:conn.execute(f'CREATE TABLE {table}(id TEXT)')
    backup=online_backup(db,tmp_path/'b',keep=100)
    manifest=Path(backup.manifest);data=json.loads(manifest.read_text());data['sha256']='0'*64;manifest.write_text(json.dumps(data))
    with pytest.raises(ValueError): verify_restore(backup.backup)
