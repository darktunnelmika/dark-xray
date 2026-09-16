import json
import os
import sqlite3
from pathlib import Path

import pytest

from backup import create_backup,restore_backup,project_version
from dark_policy import PolicyError

PASS='BackupPassphrase-123!'


def minimal_source(tmp_path:Path,*,sessions:bool=True):
    data=tmp_path/'data';data.mkdir()
    db=data/'dark.sqlite3'
    with sqlite3.connect(db) as con:
        con.executescript('''
        CREATE TABLE clients(id TEXT PRIMARY KEY);
        CREATE TABLE owners(id TEXT PRIMARY KEY);
        CREATE TABLE api_admins(id TEXT PRIMARY KEY);
        CREATE TABLE core_clients(email TEXT PRIMARY KEY);
        ''')
        if sessions:con.execute('CREATE TABLE live_sessions(digest TEXT PRIMARY KEY)')
        con.commit()
    (data/'secret.key').write_bytes(b'x'*44)
    os.chmod(data/'secret.key',0o600)
    config=tmp_path/'config.json';config.write_text(json.dumps({'public_origin':'http://127.0.0.1:2087','core_autostart':True}),encoding='utf-8')
    return data,config


def test_backup_manifest_uses_current_project_version_and_restore_reports_it(tmp_path):
    data,config=minimal_source(tmp_path)
    archive=tmp_path/'full.darkbackup'
    manifest=create_backup(data,config,archive,PASS)
    assert manifest['version']==project_version()
    restored=restore_backup(archive,tmp_path/'restored',PASS)
    assert restored['backup_version']==project_version()
    assert restored['core_autostart'] is False and restored['sessions_revoked'] is True
    with sqlite3.connect(tmp_path/'restored/data/dark.sqlite3') as db:
        assert db.execute('SELECT COUNT(*) FROM live_sessions').fetchone()[0]==0


def test_restore_accepts_older_standalone_snapshot_without_live_sessions(tmp_path):
    data,config=minimal_source(tmp_path,sessions=False)
    archive=tmp_path/'legacy.darkbackup'
    create_backup(data,config,archive,PASS)
    out=restore_backup(archive,tmp_path/'legacy-restored',PASS)
    assert out['restored'] is True
    with sqlite3.connect(tmp_path/'legacy-restored/data/dark.sqlite3') as db:
        tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert 'core_clients' in tables and 'live_sessions' not in tables


def test_backup_refuses_config_symlink_before_resolving_it(tmp_path):
    data,config=minimal_source(tmp_path)
    target=tmp_path/'real-config.json';config.rename(target);config.symlink_to(target)
    with pytest.raises(PolicyError,match='symlinks'):
        create_backup(data,config,tmp_path/'bad.darkbackup',PASS)


def test_backup_refuses_database_symlink(tmp_path):
    data,config=minimal_source(tmp_path)
    db=data/'dark.sqlite3';real=data/'real.sqlite3';db.rename(real);db.symlink_to(real)
    with pytest.raises(PolicyError,match='non-symlink'):
        create_backup(data,config,tmp_path/'bad.darkbackup',PASS)


def test_backup_refuses_non_object_config(tmp_path):
    data,config=minimal_source(tmp_path)
    config.write_text('[]',encoding='utf-8')
    with pytest.raises(PolicyError,match='JSON object'):
        create_backup(data,config,tmp_path/'bad.darkbackup',PASS)
