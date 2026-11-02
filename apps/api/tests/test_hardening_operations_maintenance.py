from datetime import datetime,timezone,timedelta
import json
from pathlib import Path
import sqlite3
import pytest
from app.ops.maintenance import storage_report, prune_snapshots, media_inventory


def test_snapshot_maintenance_bounded_and_domain_preserved(tmp_path):
    database=tmp_path/'db';now=datetime(2026,11,2,tzinfo=timezone.utc)
    with sqlite3.connect(database) as db:
        db.execute('CREATE TABLE llm_prompt_snapshots(id TEXT, created_at TEXT)')
        db.execute('CREATE TABLE messages(id TEXT, content TEXT)')
        db.execute("INSERT INTO messages VALUES ('m','authored history')")
        old=(now-timedelta(days=8)).replace(tzinfo=None).isoformat(' ')
        recent=now.replace(tzinfo=None).isoformat(' ')
        db.executemany('INSERT INTO llm_prompt_snapshots VALUES (?,?)',[(str(i),old) for i in range(12)]+[('new',recent)])
    before=database.read_bytes()
    assert prune_snapshots(database,now=now,batch_size=5)=={'dry_run':True,'eligible_in_batch':5,'deleted':0}
    assert database.read_bytes()==before
    assert prune_snapshots(database,now=now,batch_size=5,apply=True)['deleted']==5
    with sqlite3.connect(database) as db:
        assert db.execute('SELECT count(*) FROM llm_prompt_snapshots').fetchone()[0]==8
        assert db.execute('SELECT content FROM messages').fetchone()[0]=='authored history'
    with pytest.raises(ValueError):prune_snapshots(database,days=1,apply=True)
    with pytest.raises(ValueError):prune_snapshots(database,batch_size=1001,apply=True)


def test_media_inventory_is_readonly_and_reference_safe(tmp_path):
    database=tmp_path/'db';uploads=tmp_path/'uploads';shared=tmp_path/'shared'
    uploads.mkdir();shared.mkdir()
    for name in ['avatar.png','message.wav','orphan.png']:(uploads/name).write_bytes(b'private')
    (shared/'original.png').write_bytes(b'original')
    (uploads/'linked.png').symlink_to(shared/'original.png')
    with sqlite3.connect(database) as db:
        db.execute('CREATE TABLE characters(avatar_url TEXT)')
        db.execute('CREATE TABLE messages(metadata JSON)')
        db.execute('INSERT INTO characters VALUES (?)',('https://trusted.test/uploads/avatar.png',))
        db.execute('INSERT INTO messages VALUES (?)',(json.dumps({'tts_audio_url':'/uploads/message.wav','assets':[{'url':'/uploads/original.png'}]}),))
    before={p.name:p.read_bytes() for p in uploads.iterdir()}
    report=media_inventory(database,uploads,shared_root=shared)
    assert report['dry_run'] and not report['deletion_performed']
    assert report['missing_references']==0
    assert {item['path'] for item in report['unreferenced_candidates']}=={'orphan.png'}
    assert {p.name:p.read_bytes() for p in uploads.iterdir()}==before
    assert (shared/'original.png').read_bytes()==b'original'
    with pytest.raises(ValueError):media_inventory(database,shared,shared_root=shared)


def test_complete_bundle_detects_unresolved_media_and_secrets(tmp_path):
    from app.ops.backup_bundle import create_bundle,verify_bundle
    from app.ops.sqlite_ops import REQUIRED_RUNTIME_TABLES
    database=tmp_path/'db'
    with sqlite3.connect(database) as db:
        for table in REQUIRED_RUNTIME_TABLES:db.execute(f'CREATE TABLE {table}(id TEXT)')
        db.execute('CREATE TABLE characters(avatar_url TEXT)')
        db.execute("INSERT INTO characters VALUES ('/uploads/missing.png')")
        db.execute('CREATE TABLE model_provider_accounts(api_key_secret_ref TEXT)')
        db.execute("INSERT INTO model_provider_accounts VALUES ('synthetic-ref')")
    uploads=tmp_path/'u';uploads.mkdir();secrets=tmp_path/'s';secrets.mkdir()
    (secrets/'provider_secrets.json').write_text('{"synthetic-ref":"synthetic-secret"}')
    config=tmp_path/'env';config.write_text('LLM_MOCK=true')
    bundle=create_bundle(database,uploads,secrets,config,tmp_path/'bundle',quiesced=True)
    with pytest.raises(ValueError,match='media'):verify_bundle(bundle)
    (uploads/'missing.png').write_bytes(b'synthetic referenced bytes')
    healthy=create_bundle(database,uploads,secrets,config,tmp_path/'healthy',quiesced=True)
    accepted=verify_bundle(healthy)
    assert accepted['media_references_resolved'] and accepted['provider_secrets_resolved']
    (secrets/'provider_secrets.json').write_text('{}')
    broken=create_bundle(database,uploads,secrets,config,tmp_path/'broken-secrets',quiesced=True)
    with pytest.raises(ValueError,match='secret'):verify_bundle(broken)


def test_shared_settings_required_positive():
    from app.core.config import Settings
    from pydantic import ValidationError
    for field,default in [('llm_task_deadline_seconds',300),('dispatcher_shutdown_timeout_seconds',30),('tts_timeout_seconds',120)]:
        assert getattr(Settings(_env_file=None),field)==default
        for invalid in (None, 0, -1):
            with pytest.raises(ValidationError):
                Settings(_env_file=None, **{field: invalid})


def test_context_sources_new_intent_and_legacy_compatibility():
    from app.schemas.chat_commands import ChatCommandCreate,ChatCommandUpdate
    assert ChatCommandCreate(name='x',prompt='x').context_sources is None
    assert ChatCommandCreate(name='x',prompt='x',context_sources=None).context_sources is None
    assert ChatCommandCreate(name='x',prompt='x',context_sources=[]).context_sources==[]
    assert 'context_sources' not in ChatCommandUpdate().model_dump(exclude_unset=True)
    assert ChatCommandUpdate(context_sources=None).model_dump(exclude_unset=True)=={'context_sources':None}
    assert ChatCommandUpdate(context_sources=[]).model_dump(exclude_unset=True)=={'context_sources':[]}


def test_maintenance_cli_entrypoint_parses_and_reports(tmp_path,monkeypatch,capsys):
    from app.ops import maintenance
    database=tmp_path/'db';uploads=tmp_path/'uploads';uploads.mkdir()
    with sqlite3.connect(database) as db:db.execute('CREATE TABLE messages(id TEXT)')
    monkeypatch.setattr('sys.argv',['maintenance','capacity','--database',str(database),'--uploads',str(uploads)])
    maintenance.main()
    report=json.loads(capsys.readouterr().out)
    assert report['read_only'] and report['row_counts']['messages']==0


def test_bundle_cli_entrypoint_is_verified_not_just_importable(tmp_path,monkeypatch,capsys):
    from app.ops import backup_bundle
    from app.ops.sqlite_ops import REQUIRED_RUNTIME_TABLES
    database=tmp_path/'db'
    with sqlite3.connect(database) as db:
        for table in REQUIRED_RUNTIME_TABLES:db.execute(f'CREATE TABLE {table}(id TEXT)')
    uploads=tmp_path/'uploads';uploads.mkdir();secrets=tmp_path/'secrets';secrets.mkdir()
    config=tmp_path/'config.env';config.write_text('LLM_MOCK=true')
    destination=tmp_path/'cli-bundle'
    monkeypatch.setattr('sys.argv',['backup_bundle','create','--database',str(database),'--uploads',str(uploads),'--secrets',str(secrets),'--config',str(config),'--destination',str(destination),'--quiesced'])
    backup_bundle.main()
    assert json.loads(capsys.readouterr().out)['bundle']==str(destination)
    monkeypatch.setattr('sys.argv',['backup_bundle','verify',str(destination)])
    backup_bundle.main()
    assert json.loads(capsys.readouterr().out)['integrity']=='ok'
