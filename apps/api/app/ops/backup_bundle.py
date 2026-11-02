"""Explicit offline/quiesced bundle creation and read-only acceptance.

Never invoked by lifespan. Keep bundles outside source/live state, encrypted by
operator storage. A hash manifest is integrity evidence, not an authenticity MAC.
"""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
from app.ops.sqlite_ops import online_backup, verify_restore, file_sha256


def _regular_files(root):
    for directory, dirs, files in os.walk(root, followlinks=False):
        for name in dirs+files:
            path=Path(directory)/name
            if path.is_symlink():raise ValueError('Bundle sources must not contain symlinks')
        for name in files:
            path=Path(directory)/name
            if not path.is_file():raise ValueError('Bundle sources must be regular files')
            yield path


def create_bundle(database, uploads, secrets, config, destination, *, quiesced=False):
    if not quiesced:
        raise ValueError('Quiesce all writers before coherent DB/media/secrets backup')
    raw_sources=[Path(value) for value in (database,uploads,secrets,config)]
    if any(path.is_symlink() for path in raw_sources):raise ValueError("Symlink source")
    sources=[path.resolve() for path in raw_sources]
    database,uploads,secrets,config=sources
    destination=Path(destination).absolute()
    if destination.exists():raise FileExistsError(destination)
    if not database.is_file() or not config.is_file() or not uploads.is_dir() or not secrets.is_dir():
        raise ValueError('Complete DB/uploads/secrets/config sources are required')
    for source in sources:
        if destination.resolve().is_relative_to(source):raise ValueError('Bundle destination overlaps a source')
    # Validate symlinks before creating the output. Explicit sources must be private copies.
    if Path(config).is_symlink() or Path(database).is_symlink():raise ValueError('Symlink source')
    for root in (uploads,secrets):
        for _ in _regular_files(root):pass
    destination.mkdir(parents=True,mode=0o700)
    result=online_backup(database,destination/'database',keep=1000000)
    for label,root in [('uploads',uploads),('secrets',secrets)]:
        target=destination/label;target.mkdir(mode=0o700)
        for source in _regular_files(root):
            copied=target/source.relative_to(root);copied.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
            shutil.copyfile(source,copied);copied.chmod(0o600)
    shutil.copyfile(config,destination/'config.env');(destination/'config.env').chmod(0o600)
    entries={str(p.relative_to(destination)):{'bytes':p.stat().st_size,'sha256':file_sha256(p)} for p in _regular_files(destination)}
    manifest={'version':1,'created_at':datetime.now(timezone.utc).isoformat(),'database':str(Path(result.backup).relative_to(destination)),'files':entries}
    path=destination/'bundle.json';path.write_text(json.dumps(manifest,indent=2)+'\n');path.chmod(0o600)
    return str(destination)


def verify_bundle(bundle):
    root=Path(bundle).resolve();manifest=json.loads((root/'bundle.json').read_text())
    entries=manifest['files']
    actual={str(p.relative_to(root)) for p in _regular_files(root)}-{'bundle.json'}
    if actual!=set(entries):raise ValueError('Bundle file inventory mismatch')
    for name,entry in entries.items():
        path=(root/name).resolve()
        if not path.is_relative_to(root) or file_sha256(path)!=entry['sha256'] or path.stat().st_size!=entry['bytes']:
            raise ValueError('Bundle checksum mismatch')
    database=(root/manifest['database']).resolve()
    if not database.is_relative_to(root):raise ValueError('Invalid bundle database path')
    check=verify_restore(database)
    from app.ops.maintenance import media_inventory, _readonly
    media = media_inventory(database, root/'uploads')
    if media['missing_references']:
        raise ValueError('Bundle media references are unresolved')
    secret_refs = []
    with _readonly(database) as db:
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if 'model_provider_accounts' in tables:
            secret_refs = [row[0] for row in db.execute('SELECT api_key_secret_ref FROM model_provider_accounts WHERE api_key_secret_ref IS NOT NULL')]
    if secret_refs:
        try:
            store = json.loads((root/'secrets/provider_secrets.json').read_text())
        except (ValueError, OSError):
            raise ValueError('Bundle secret store is unavailable or invalid') from None
        if not isinstance(store, dict) or any(not isinstance(store.get(ref), str) or not store[ref].strip() for ref in secret_refs):
            raise ValueError('Bundle provider secret references are unresolved')
    return {'integrity':check.integrity,'foreign_key_violations':check.foreign_key_violations,'files_verified':len(entries),'sha256':check.sha256,'media_references_resolved':True,'provider_secrets_resolved':True,'secrets_present':any(name.startswith('secrets/') for name in entries),'runtime_activation_performed':False}


def main():
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    commands=parser.add_subparsers(dest='command',required=True)
    create=commands.add_parser('create')
    for name in ['database','uploads','secrets','config','destination']:create.add_argument('--'+name,required=True)
    create.add_argument('--quiesced',action='store_true')
    verify=commands.add_parser('verify');verify.add_argument('bundle')
    args=vars(parser.parse_args());command=args.pop('command')
    if command=='create':print(json.dumps({'bundle':create_bundle(**args)}))
    else:print(json.dumps(verify_bundle(args['bundle'])))

if __name__=='__main__':main()
