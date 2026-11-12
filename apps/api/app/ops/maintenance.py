"""Read-only capacity/queue/media reports; explicit bounded snapshot maintenance.

No lifespan hooks, recurring jobs, retry mutations or media/domain deletion.
"""
from contextlib import closing
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import shutil
import sqlite3
from urllib.parse import unquote, urlsplit


def _readonly(database):
    path=Path(database).resolve()
    return closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=5))


def storage_report(database, uploads, *, now=None):
    now=now or datetime.now(timezone.utc)
    with _readonly(database) as db:
        tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        counts={}
        for table in ['messages','llm_prompt_snapshots','llm_usage_events','generation_job_events']:
            if table in tables:counts[table]=db.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0]
        queues={}
        for table in ['message_generation_jobs','post_commit_tasks']:
            if table not in tables:continue
            groups=[]
            for status,count,oldest in db.execute(f'SELECT status,count(*),min(created_at) FROM "{table}" GROUP BY status'):
                age=None
                if oldest:
                    stamp=datetime.fromisoformat(oldest)
                    if stamp.tzinfo is None:stamp=stamp.replace(tzinfo=timezone.utc)
                    age=max(0,(now-stamp).total_seconds())
                groups.append({'status':status,'count':count,'oldest_age_seconds':age})
            queues[table]=groups
        expired=0
        if 'llm_prompt_snapshots' in tables:
            cutoff=(now-timedelta(days=7)).replace(tzinfo=None).isoformat(' ')
            expired=db.execute('SELECT count(*) FROM llm_prompt_snapshots WHERE created_at < ?', (cutoff,)).fetchone()[0]
    root=Path(uploads).resolve();files=bytes_used=0
    if root.is_dir():
        for directory,dirs,names in os.walk(root,followlinks=False):
            dirs[:]=[name for name in dirs if not (Path(directory)/name).is_symlink()]
            for name in names:
                p=Path(directory)/name
                if p.is_file() and not p.is_symlink():files+=1;bytes_used+=p.stat().st_size
    disk=shutil.disk_usage(root if root.exists() else root.parent)
    database_bytes=sum(p.stat().st_size for p in [Path(database),Path(str(database)+'-wal'),Path(str(database)+'-shm')] if p.is_file())
    return {'read_only':True,'database_bytes_including_wal':database_bytes,'row_counts':counts,'queues':queues,'expired_snapshots':expired,'uploads':{'files':files,'bytes':bytes_used},'disk':{'free_bytes':disk.free,'total_bytes':disk.total,'warning':disk.free<1024**3 or disk.free/disk.total<0.1}}


def prune_snapshots(database, *, days=7, batch_size=500, apply=False, now=None):
    if days<7 or not 1<=batch_size<=1000:raise ValueError('Retention minimum 7 days; batch size 1..1000')
    cutoff=((now or datetime.now(timezone.utc))-timedelta(days=days)).replace(tzinfo=None).isoformat(' ')
    if not apply:
        with _readonly(database) as db:
            count=db.execute('SELECT count(*) FROM (SELECT id FROM llm_prompt_snapshots WHERE created_at < ? ORDER BY created_at,id LIMIT ?)',(cutoff,batch_size)).fetchone()[0]
        return {'dry_run':True,'eligible_in_batch':count,'deleted':0}
    with sqlite3.connect(Path(database),timeout=5) as db:
        db.execute('BEGIN IMMEDIATE')
        cursor=db.execute('DELETE FROM llm_prompt_snapshots WHERE id IN (SELECT id FROM llm_prompt_snapshots WHERE created_at < ? ORDER BY created_at,id LIMIT ?)',(cutoff,batch_size))
        return {'dry_run':False,'deleted':cursor.rowcount}


def referenced_uploads(database):
    """Conservative inventory of URL references in ALL text/JSON columns.

    Do not return private stored content. Unknown metadata stays conservative:
    a raw matching URL is recognized even when its enclosing JSON is invalid.
    """
    import re
    refs=set()
    def add(value):
        if isinstance(value,str):
            for match in re.finditer(r'(?:https?://[^\s"\'<>]+)?/uploads/[^\s"\'<>\\]+',value):
                path=unquote(urlsplit(match.group()).path)
                relative=path.split('/uploads/',1)[1]
                if relative and '..' not in Path(relative).parts:refs.add(relative)
            try:
                decoded=json.loads(value)
                if decoded!=value:add(decoded)
            except (ValueError,TypeError):pass
        elif isinstance(value,dict):
            for child in value.values():add(child)
        elif isinstance(value,list):
            for child in value:add(child)
    with _readonly(database) as db:
        for (table,) in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall():
            quote=lambda name:'"'+name.replace('"','""')+'"'
            for _,column,kind,*_ in db.execute(f'PRAGMA table_info({quote(table)})').fetchall():
                if not any(token in kind.upper() for token in ['TEXT','CHAR','JSON']):continue
                for (value,) in db.execute(f'SELECT {quote(column)} FROM {quote(table)} WHERE instr({quote(column)}, ?) > 0',('/uploads/',)):
                    add(value)
    return refs


def media_inventory(database, upload_root, *, shared_root=None):
    root=Path(upload_root).resolve()
    if shared_root:
        shared=Path(shared_root).resolve()
        if root==shared or root.is_relative_to(shared) or shared.is_relative_to(root):raise ValueError('Private/shared media roots must be disjoint')
    refs=referenced_uploads(database);orphans=[];files=set()
    for directory,dirs,names in os.walk(root,followlinks=False):
        # Internal derivatives/retry intents are not domain-media orphans and
        # their private filenames must not enter the operator's source report.
        dirs[:]=[name for name in dirs if name not in {'.image-cache', '.image-cleanup'}
                 and not (Path(directory)/name).is_symlink()]
        for name in names:
            path=Path(directory)/name
            if path.is_symlink() or not path.is_file():continue
            relative=str(path.relative_to(root));files.add(relative)
            if relative not in refs:orphans.append({'path':relative,'bytes':path.stat().st_size})
    missing=sum(not (root/name).is_file() and not (shared_root and (Path(shared_root)/name).is_file()) for name in refs)
    return {'dry_run':True,'files':len(files),'referenced_paths':len(refs),'missing_references':missing,'unreferenced_candidates':orphans,'deletion_performed':False}


def main():
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='command',required=True)
    report=sub.add_parser('capacity');report.add_argument('--database',required=True);report.add_argument('--uploads',required=True)
    media=sub.add_parser('media');media.add_argument('--database',required=True);media.add_argument('--upload-root',required=True);media.add_argument('--shared-root')
    prune=sub.add_parser('snapshots');prune.add_argument('--database',required=True);prune.add_argument('--days',type=int,default=7);prune.add_argument('--batch-size',type=int,default=500);prune.add_argument('--apply',action='store_true',default=False)
    args=vars(parser.parse_args());command=args.pop('command')
    result={'capacity':storage_report,'media':media_inventory,'snapshots':prune_snapshots}[command](**args)
    print(json.dumps(result,indent=2))

if __name__=='__main__':main()
