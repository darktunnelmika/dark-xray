"""Large production backups remain complete without whole-file RAM reads."""
import io
import json
import os
import sqlite3
import threading
import zipfile
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
import backup
from backup import create_backup, restore_backup, snapshot_database, MAGIC, derive
from dark_policy import PolicyError, Store
from test_backup_hardening import minimal_source, PASS


def test_large_roundtrip_preserves_ledger_and_never_reads_bulk_file_into_memory(tmp_path,monkeypatch):
    data,config=minimal_source(tmp_path)
    with sqlite3.connect(data/'dark.sqlite3') as db:
        db.execute('CREATE TABLE payload(body BLOB)')
        db.execute('INSERT INTO payload VALUES(zeroblob(?))',(257*1024*1024,))
        db.execute('CREATE TABLE traffic_ledger(event_id TEXT PRIMARY KEY,owner TEXT,up_bytes INTEGER,down_bytes INTEGER)')
        db.execute("INSERT INTO traffic_ledger VALUES('event-1','owner',123,456)")
    original=Path.read_bytes
    def bounded(path):
        assert path.suffix not in ('.sqlite3','.darkbackup'), 'whole bulk file read into RAM'
        return original(path)
    monkeypatch.setattr(Path,'read_bytes',bounded)
    archive=tmp_path/'large.darkbackup';dest=tmp_path/'restored'
    create_backup(data,config,archive,PASS)
    assert archive.stat().st_size<data.joinpath('dark.sqlite3').stat().st_size//20
    restore_backup(archive,dest,PASS)
    with sqlite3.connect(dest/'data/dark.sqlite3') as db:
        assert db.execute('SELECT length(body) FROM payload').fetchone()[0]==257*1024*1024
        assert db.execute('SELECT * FROM traffic_ledger').fetchone()==('event-1','owner',123,456)


def test_streaming_cipher_is_compatible_with_legacy_aesgcm_both_ways(tmp_path):
    data,config=minimal_source(tmp_path);archive=tmp_path/'new.darkbackup'
    create_backup(data,config,archive,PASS)
    raw=archive.read_bytes();i=len(MAGIC)
    plain=AESGCM(derive(PASS,raw[i:i+16])).decrypt(raw[i+16:i+28],raw[i+28:],MAGIC)
    assert zipfile.ZipFile(io.BytesIO(plain)).testzip() is None
    salt,nonce=os.urandom(16),os.urandom(12)
    legacy=tmp_path/'legacy.darkbackup'
    legacy.write_bytes(MAGIC+salt+nonce+AESGCM(derive(PASS,salt)).encrypt(nonce,plain,MAGIC))
    assert restore_backup(legacy,tmp_path/'restored',PASS)['restored']


@pytest.mark.parametrize('mutation',['tag','truncate','password'])
def test_unauthenticated_archive_never_publishes_restore(tmp_path,mutation):
    data,config=minimal_source(tmp_path);archive=tmp_path/'full.darkbackup'
    create_backup(data,config,archive,PASS)
    raw=bytearray(archive.read_bytes())
    if mutation=='tag':raw[-1]^=1
    if mutation=='truncate':raw=raw[:-50]
    archive.write_bytes(raw)
    dest=tmp_path/'restore'
    with pytest.raises(PolicyError):restore_backup(archive,dest,'wrong-password-123' if mutation=='password' else PASS)
    assert not dest.exists() and not list(tmp_path.glob('.dark-restore-*'))


def test_file_store_backup_does_not_take_accounting_lock_and_includes_wal(tmp_path):
    data,config=minimal_source(tmp_path);src=data/'dark.sqlite3'
    writer=sqlite3.connect(src);writer.execute('PRAGMA journal_mode=WAL')
    writer.execute("INSERT INTO clients VALUES('committed-in-wal')");writer.commit()
    store=Store.__new__(Store);store.path=str(src)
    class ForbiddenLock:
        def __enter__(self):raise AssertionError('live accounting lock acquired')
        def __exit__(self,*args):pass
    store.lock=ForbiddenLock();dest=tmp_path/'copy.sqlite3'
    store.backup(dest)
    with sqlite3.connect(dest) as db:
        assert db.execute('SELECT id FROM clients').fetchone()[0]=='committed-in-wal'
    writer.close()


def test_parallel_backup_rejected_without_waiting(tmp_path):
    data,config=minimal_source(tmp_path)
    with backup.backup_slot():
        with pytest.raises(PolicyError,match='already running'):
            create_backup(data,config,tmp_path/'blocked.darkbackup',PASS)


def test_disk_space_failure_does_not_leave_partial_snapshot(tmp_path,monkeypatch):
    data,config=minimal_source(tmp_path);dest=tmp_path/'copy.sqlite3'
    from collections import namedtuple
    monkeypatch.setattr(backup.shutil,'disk_usage',lambda p:namedtuple('Usage','total used free')(100,100,0))
    with pytest.raises(PolicyError,match='free disk'):snapshot_database(data/'dark.sqlite3',dest)
    assert not dest.exists()


def test_atomic_archive_publication_never_overwrites_existing_target(tmp_path):
    source=tmp_path/'zip';source.write_bytes(b'payload');output=tmp_path/'existing'
    output.write_bytes(b'keep-me')
    with pytest.raises(FileExistsError):backup.encrypt_file(source,output,PASS)
    assert output.read_bytes()==b'keep-me'
