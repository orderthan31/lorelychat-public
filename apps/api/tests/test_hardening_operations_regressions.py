import asyncio
import io
import json
import logging
import sqlite3
import stat
from pathlib import Path
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlmodel import Session,SQLModel,create_engine
from sqlalchemy.pool import StaticPool


def test_warning_path_and_exception_arguments_do_not_leak():
    from app.core.logging_config import SensitiveDataFormatter
    formatter=SensitiveDataFormatter('%(message)s')
    record=logging.LogRecord('py.warnings',logging.WARNING,'/private/source.py',1,'/private/a.py: warning SECRET_PROSE',(),None)
    assert 'SECRET_PROSE' not in formatter.format(record)
    record=logging.LogRecord('task',logging.WARNING,__file__,1,'task failed: %s',(ValueError('SECRET_EXCEPTION_ARGUMENT'),),None)
    assert 'SECRET_EXCEPTION_ARGUMENT' not in formatter.format(record)


def test_backup_artifact_permissions(tmp_path):
    from app.ops.sqlite_ops import online_backup,REQUIRED_RUNTIME_TABLES
    database=tmp_path/'db'
    with sqlite3.connect(database) as db:
        for table in REQUIRED_RUNTIME_TABLES:db.execute(f'CREATE TABLE {table}(id TEXT)')
    result=online_backup(database,tmp_path/'b',keep=100)
    assert stat.S_IMODE(Path(result.backup).stat().st_mode)==0o600
    assert stat.S_IMODE(Path(result.manifest).stat().st_mode)==0o600


def test_actual_patch_invalid_request_does_not_change_database(tmp_path):
    from app.api import characters,world_settings,presets
    from app.db.session import get_session
    engine=create_engine('sqlite://',connect_args={'check_same_thread':False},poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        app=FastAPI()
        for module in [characters,world_settings,presets]:app.include_router(module.router)
        app.dependency_overrides[get_session]=lambda:session
        with TestClient(app,raise_server_exceptions=False) as client:
            for endpoint,payload,fields in [('/characters',{'name':'original','persona':'original'},['name','persona','emotional_rules','forbidden_rules','trait_scores']),('/world-settings',{'title':'original'},['title','genre_mode','enabled','tags']),('/presets',{'preset_type':'room_seed','title':'original','content':'original'},['title','content','enabled'])]:
                created=client.post(endpoint,json=payload)
                assert created.status_code==200,created.text
                target=endpoint+'/'+created.json()['id'];before=client.get(target).json()
                for field in fields:
                    assert client.patch(target,json={field:None}).status_code==422
                    assert client.get(target).json()==before
                assert client.patch(target,json={}).status_code==200
    engine.dispose()


def test_capacity_reports_low_disk_readonly(tmp_path,monkeypatch):
    from collections import namedtuple
    from datetime import datetime,timezone,timedelta
    from app.ops import maintenance
    database=tmp_path/'db';uploads=tmp_path/'uploads';uploads.mkdir()
    with sqlite3.connect(database) as db:
        db.execute('CREATE TABLE post_commit_tasks(status TEXT,created_at TEXT)')
        db.execute("INSERT INTO post_commit_tasks VALUES ('failed','2026-11-01 00:00:00')")
    Usage=namedtuple('usage','total used free')
    monkeypatch.setattr(maintenance.shutil,'disk_usage',lambda p:Usage(1000,990,10))
    before=database.read_bytes()
    report=maintenance.storage_report(database,uploads,now=datetime(2026,11,2,tzinfo=timezone.utc))
    assert report['read_only'] and report['disk']['warning']
    assert report['queues']['post_commit_tasks'][0]['oldest_age_seconds']==86400
    assert database.read_bytes()==before


def test_webp_gif_structural_formats_are_kept():
    import base64
    from app.ops.image_validation import verify_image
    gif=base64.b64decode('R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7')
    assert verify_image(gif,'image/gif')=='.gif'
    # Preserve the malformed sample as a negative; a corrected container's
    # acceptance is structural evidence only, not full WebP decode acceptance.
    malformed=base64.b64decode('UklGRiIAAABXRUJQVlA4TBUAAAAvAAAAAAfQ//73v/+BiOh/AAA=')
    with pytest.raises(ValueError): verify_image(malformed,'image/webp')
    payload=malformed[20:]+b'\0\0'
    import struct
    chunk=b'VP8L'+struct.pack('<I',len(payload))+payload+(b'\0' if len(payload)%2 else b'')
    webp=b'RIFF'+struct.pack('<I',len(chunk)+4)+b'WEBP'+chunk
    assert verify_image(webp,'image/webp')=='.webp'


def test_upload_target_symlink_never_writes_shared_root(tmp_path):
    from app.core.upload_files import ensure_private_upload_directory
    from fastapi import HTTPException
    private=tmp_path/'private';private.mkdir()
    shared=tmp_path/'shared';shared.mkdir()
    target=private/'character-assets';target.symlink_to(shared,target_is_directory=True)
    with pytest.raises(HTTPException):ensure_private_upload_directory(target,private,shared)
    assert list(shared.iterdir())==[]


def test_chunked_multipart_abort_closes_spool(monkeypatch):
    from fastapi import File,UploadFile
    from app.ops.request_limits import RequestBodyLimitMiddleware
    import starlette.formparsers as parsers
    opened=[];original=parsers.SpooledTemporaryFile
    def track(*args,**kwargs):
        value=original(*args,**kwargs);opened.append(value);return value
    monkeypatch.setattr(parsers,'SpooledTemporaryFile',track)
    app=FastAPI()
    @app.post('/')
    async def upload(file: UploadFile = File(...)):return {'ok':True}
    app.add_middleware(RequestBodyLimitMiddleware,max_bytes=1024)
    chunks=[b'--b\r\nContent-Disposition: form-data; name="file"; filename="a.png"\r\nContent-Type: image/png\r\n\r\n'+b'x'*100,b'x'*1100+b'\r\n--b--\r\n']
    events=[]
    async def receive():
        if chunks:return {'type':'http.request','body':chunks.pop(0),'more_body':bool(chunks)}
        return {'type':'http.disconnect'}
    async def send(event):events.append(event)
    scope={'type':'http','method':'POST','path':'/','raw_path':b'/','query_string':b'','root_path':'','scheme':'http','server':('synthetic',80),'client':('synthetic',1),'http_version':'1.1','headers':[(b'content-type',b'multipart/form-data; boundary=b')]}
    asyncio.run(app(scope,receive,send))
    assert events[0]['status']==413
    assert opened and all(file.closed for file in opened)


@pytest.mark.parametrize('endpoint,directory,response_key',[('/avatars','avatars','avatar_url'),('/world-thumbnails','world-thumbnails','thumbnail_url'),('/conversation-thumbnails','conversation-thumbnails','thumbnail_url')])
def test_verified_upload_routes_return_real_static_bytes(tmp_path,monkeypatch,endpoint,directory,response_key):
    from app.api import uploads
    from app.core.upload_files import UploadStaticFiles
    from test_hardening_operations_uploads_logging import png
    monkeypatch.setattr(uploads,'UPLOAD_ROOT',tmp_path)
    for attr,name in [('AVATAR_DIR','avatars'),('WORLD_THUMBNAIL_DIR','world-thumbnails'),('CONVERSATION_THUMBNAIL_DIR','conversation-thumbnails')]:monkeypatch.setattr(uploads,attr,tmp_path/name)
    app=FastAPI();app.include_router(uploads.router);app.mount('/uploads',UploadStaticFiles(tmp_path),name='uploads')
    with TestClient(app) as client:
        response=client.post('/uploads'+endpoint,files={'file':('fixture.png',png(),'image/png')})
        assert response.status_code==200,response.text
        assert client.get(response.json()[response_key]).content==png()
        assert len(list((tmp_path/directory).iterdir()))==1


def test_two_real_file_handlers_and_warning_channel_are_sanitized(monkeypatch):
    from logging.handlers import RotatingFileHandler
    from app.core.logging_config import configure_file_logging
    import warnings
    configure_file_logging()
    logger=logging.getLogger('operations.synthetic')
    try:
        try:raise ValueError('PRIVATE_CHAIN_PROSE https://provider.test?key=FAKE_CREDENTIAL')
        except ValueError as cause:raise RuntimeError('PRIVATE_OUTER_PROSE') from cause
    except RuntimeError:
        logger.exception('synthetic diagnostic receipt')
    with warnings.catch_warnings():
        warnings.simplefilter('always')
        monkeypatch.setattr(warnings,'showwarning',logging._showwarning)
        warnings.warn('PRIVATE_WARNING_PROSE /private/input.txt',UserWarning)
    handlers=[handler for handler in logging.getLogger().handlers if isinstance(handler,RotatingFileHandler) and getattr(handler,'_lorechat_file_handler',False)]
    assert len(handlers)==2
    for handler in handlers:
        handler.flush();text=Path(handler.baseFilename).read_text()
        assert 'synthetic diagnostic receipt' in text and 'ValueError' in text and 'RuntimeError' in text
        for sentinel in ['PRIVATE_CHAIN_PROSE','PRIVATE_OUTER_PROSE','FAKE_CREDENTIAL','PRIVATE_WARNING_PROSE','/private/input.txt']:
            assert sentinel not in text
