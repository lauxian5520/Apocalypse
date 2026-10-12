"""Offline SQLite backup, verified restore and retention. Stop the backend first."""
import argparse
import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import sqlite3
import subprocess
import tarfile
import tempfile
from datetime import datetime, timezone


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1024*1024), b''):
            h.update(block)
    return h.hexdigest()


def safe_name(name):
    p = PurePosixPath(name)
    if p.is_absolute() or '..' in p.parts or str(p) != name or not p.parts:
        raise ValueError('unsafe archive path')
    return p


def database_check(path):
    with sqlite3.connect(f'file:{Path(path).resolve()}?mode=ro', uri=True) as db:
        if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise ValueError('database integrity check failed')
        if db.execute('PRAGMA foreign_key_check').fetchone():
            raise ValueError('database foreign-key check failed')
        return db.execute('PRAGMA user_version').fetchone()[0]


@contextlib.contextmanager
def stopped(root):
    with (root/'.app.lock').open('a+') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise ValueError('后端仍在运行；先停止后端，再备份')
        yield


def backup(root, target, relative_db):
    root, target = root.resolve(), target.resolve()
    safe_name(relative_db)
    if target.is_relative_to(root) or target.exists():
        raise ValueError('备份目标必须在运行目录之外且尚不存在')
    db_path = root/relative_db
    if not db_path.is_file():
        raise ValueError('SQLite 数据库不存在')
    with stopped(root), tempfile.TemporaryDirectory(prefix='apocalypse-backup-') as tmp:
        staging = Path(tmp)
        for source in root.rglob('*'):
            if source.is_symlink():
                raise ValueError('运行目录包含符号链接，请先明确迁移其目标')
            rel = source.relative_to(root).as_posix()
            if not source.is_file() or rel in ('.app.lock', 'backup-manifest.json', relative_db, relative_db+'-wal', relative_db+'-shm'):
                continue
            if source.name == '.env' or source.suffix in ('.key', '.pem'):
                raise ValueError('密钥文件不能进入普通备份包')
            dest = staging/rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, dest)
        snapshot = staging/relative_db
        snapshot.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(f'file:{db_path}?mode=ro', uri=True) as source, sqlite3.connect(snapshot) as dest:
            source.backup(dest)
        schema = database_check(snapshot)
        revision = subprocess.run(['git','rev-parse','HEAD'],capture_output=True,text=True).stdout.strip() or 'unknown'
        manifest = {'format':1,'created_at':datetime.now(timezone.utc).isoformat(),
                    'application_version':'1.0.0','code_revision':revision,'database_user_version':schema,
                    'database':relative_db,'files':{p.relative_to(staging).as_posix():digest(p) for p in sorted(staging.rglob('*')) if p.is_file()}}
        (staging/'backup-manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
        target.parent.mkdir(parents=True, exist_ok=True)
        partial = target.with_name(target.name+'.partial')
        try:
            with tarfile.open(partial,'x:gz') as archive:
                for p in sorted(staging.rglob('*')):
                    if p.is_file():
                        archive.add(p,arcname=p.relative_to(staging).as_posix(),recursive=False)
            os.chmod(partial,0o600)
            os.replace(partial,target)
        finally:
            partial.unlink(missing_ok=True)
        target.with_name(target.name+'.sha256').write_text(digest(target)+'  '+target.name+'\n')
    return manifest


def unpack_verified(archive_path, destination):
    with tarfile.open(archive_path,'r:gz') as archive:
        members=archive.getmembers()
        names=[m.name for m in members]
        if len(names)!=len(set(names)) or 'backup-manifest.json' not in names:
            raise ValueError('duplicate members or missing manifest')
        for member in members:
            safe_name(member.name)
            if not member.isfile():
                raise ValueError('archive must contain regular files only')
        manifest=json.load(archive.extractfile('backup-manifest.json'))
        if manifest.get('format') != 1 or set(names) != set(manifest['files'])|{'backup-manifest.json'}:
            raise ValueError('manifest member mismatch')
        for member in members:
            path=destination/member.name
            path.parent.mkdir(parents=True,exist_ok=True)
            with archive.extractfile(member) as src, path.open('xb') as dst:
                shutil.copyfileobj(src,dst)
            os.chmod(path,0o600)
            if member.name!='backup-manifest.json' and digest(path)!=manifest['files'][member.name]:
                raise ValueError('checksum mismatch: '+member.name)
        safe_name(manifest['database'])
        database_check(destination/manifest['database'])
        return manifest


def restore(archive, target):
    if target.is_symlink() or target.exists() and (not target.is_dir() or any(target.iterdir())):
        raise ValueError('恢复目录必须不存在或为空；不会覆盖现有数据')
    target.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.restore-',dir=target.parent) as tmp:
        staging=Path(tmp)/'data'
        staging.mkdir()
        manifest=unpack_verified(archive,staging)
        if target.exists():
            target.rmdir()
        os.rename(staging,target)
    return manifest


def prune(directory):
    """Retain newest archive per day (7) and ISO week (4), then union."""
    archives=sorted(directory.glob('apocalypse-*.tar.gz'),key=lambda p:p.stat().st_mtime,reverse=True)
    days, weeks, keep = set(),set(),set()
    for p in archives:
        date=datetime.fromtimestamp(p.stat().st_mtime,timezone.utc)
        day,week=date.date(),date.isocalendar()[:2]
        if day not in days and len(days)<7:
            days.add(day);keep.add(p)
        if week not in weeks and len(weeks)<4:
            weeks.add(week);keep.add(p)
    for p in archives:
        if p not in keep:
            p.unlink();p.with_name(p.name+'.sha256').unlink(missing_ok=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='command',required=True)
    b=sub.add_parser('create');b.add_argument('--var-dir',type=Path,required=True);b.add_argument('--out',type=Path,required=True);b.add_argument('--database',default='db/myweb.db')
    v=sub.add_parser('verify');v.add_argument('archive',type=Path)
    r=sub.add_parser('restore');r.add_argument('archive',type=Path);r.add_argument('--target',type=Path,required=True)
    p=sub.add_parser('prune');p.add_argument('directory',type=Path)
    args=parser.parse_args()
    try:
        if args.command=='create': manifest=backup(args.var_dir,args.out,args.database)
        elif args.command=='restore': manifest=restore(args.archive,args.target)
        elif args.command=='verify':
            with tempfile.TemporaryDirectory() as tmp: manifest=unpack_verified(args.archive,Path(tmp))
        else:
            prune(args.directory);print('retention applied');return
        print(json.dumps({'status':'ok','files':len(manifest['files']),'database':manifest['database']}))
    except (ValueError,OSError,sqlite3.Error,tarfile.TarError,KeyError) as exc:
        parser.exit(1,f'{type(exc).__name__}: {exc}\n')


if __name__=='__main__': main()
