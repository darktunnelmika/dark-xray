"""User data must remain live while update backup is fully verified.

Disposable SQLite only; this suite never contacts a production Node/Hub.
"""
import inspect
import json
import sqlite3
import sys
from pathlib import Path

import pytest

from test_update_transaction import UPDATE,fast_fixture


def test_fast_probe_never_runs_database_quick_check(monkeypatch,tmp_path):
    _conf,_db=fast_fixture(tmp_path,monkeypatch)
    monkeypatch.setattr(UPDATE,'_compat_panel_route',lambda:{
        'ok':True,'ui_status':200,'asset_status':200})
    seen=[]
    actual_connect=sqlite3.connect

    class Track:
        def __init__(self,inner):self.inner=inner
        def __enter__(self):return self
        def __exit__(self,*args):self.inner.close()
        def execute(self,query,*args):
            seen.append(query)
            if 'quick_check' in query or 'integrity_check' in query:
                raise AssertionError('An exhaustive scan cannot be a startup health check')
            return self.inner.execute(query,*args)

    monkeypatch.setattr(UPDATE.sqlite3,'connect',lambda *a,**kw:Track(actual_connect(*a,**kw)))
    ok,detail=UPDATE._doctor_once()
    assert ok and 'db=readable-schema' in detail
    assert 'sqlite_master' in ' '.join(seen)


def test_fast_probe_denies_invalid_or_incomplete_db(monkeypatch,tmp_path):
    conf,db=fast_fixture(tmp_path,monkeypatch)
    monkeypatch.setattr(UPDATE,'_compat_panel_route',
                        lambda:{'ok':True,'ui_status':200,'asset_status':200})
    db.unlink();db.write_bytes(b'not SQLite')
    assert UPDATE._doctor_once()[0] is False
    db.unlink()
    with sqlite3.connect(db) as out:out.execute('CREATE TABLE other_data(id INTEGER)')
    assert UPDATE._doctor_once()[0] is False
    (conf/'config.json').write_text('not valid JSON')
    assert UPDATE._doctor_once()[0] is False


def test_online_snapshot_precedes_first_service_stop_and_keeps_integrity_gate():
    source=inspect.getsource(UPDATE.main)
    assert source.index('database_preflight(DB)') < source.index('database_snapshot(db_backup)')
    assert source.index('database_snapshot(db_backup)') < source.index("run(['systemctl','stop','dark-xray.service'])")
    assert source.count('database_preflight(DB)')==1
    assert 'database_snapshot(db_backup)' in source
    source_snapshot=inspect.getsource(UPDATE.database_snapshot)
    assert "PRAGMA quick_check" in source_snapshot


def test_online_backup_failure_aborts_before_service_stop(tmp_path,monkeypatch):
    app=tmp_path/'app';(app/'.venv/bin').mkdir(parents=True)
    (app/'.venv/bin/python').write_text('fake executable')
    conf=tmp_path/'etc';conf.mkdir();(conf/'config.json').write_text('{}')
    data=tmp_path/'data';data.mkdir()
    source=tmp_path/'source';source.mkdir()
    monkeypatch.setattr(UPDATE,'APP',app)
    monkeypatch.setattr(UPDATE,'CONF',conf)
    monkeypatch.setattr(UPDATE,'DATA',data)
    monkeypatch.setattr(UPDATE,'DB',data/'dark.sqlite3')
    monkeypatch.setattr(UPDATE.os,'geteuid',lambda:0)
    monkeypatch.setattr(UPDATE,'database_preflight',lambda *a:{'bytes':2048,'quick_check':'ok'})
    monkeypatch.setattr(UPDATE,'require_live_panel',lambda *a:None)
    monkeypatch.setattr(UPDATE,'validate_source',lambda *a:None)
    monkeypatch.setattr(UPDATE,'disk_preflight',lambda *a:{'free_data_bytes':1000000000})
    monkeypatch.setattr(UPDATE,'dependency_preflight',lambda *a:None)
    monkeypatch.setattr(UPDATE,'source_snapshot',lambda *a:None)
    monkeypatch.setattr(UPDATE,'source_version',lambda *a:'0.10.1-rc11')
    monkeypatch.setattr(UPDATE,'source_commit',lambda *a:'a'*40)
    calls=[]
    monkeypatch.setattr(UPDATE,'run',lambda args,*a,**kw:calls.append(args))
    monkeypatch.setattr(UPDATE,'quiet',lambda args,*a,**kw:calls.append(args))
    monkeypatch.setattr(UPDATE,'database_snapshot',
                        lambda *a:(_ for _ in ()).throw(RuntimeError('snapshot write failure')))
    monkeypatch.setattr(sys,'argv',['update.py','--source',str(source),'--non-interactive'])
    with pytest.raises(SystemExit,match='running HUB untouched'):
        UPDATE.main()
    assert not any(len(a)>1 and a[0]=='systemctl' and a[1]=='stop' for a in calls)


def test_online_sqlite_snapshot_keeps_newer_live_metered_bytes(tmp_path):
    live=tmp_path/'live.sqlite3';saved=tmp_path/'backup.sqlite3'
    with sqlite3.connect(live) as db:
        db.execute('CREATE TABLE metered(bytes INTEGER)')
        db.execute('INSERT INTO metered VALUES(100)')
    UPDATE.database_snapshot(saved,live)
    with sqlite3.connect(live) as db:db.execute('UPDATE metered SET bytes=250')
    with sqlite3.connect(saved) as db:
        assert db.execute('SELECT bytes FROM metered').fetchone()[0]==100
        assert db.execute('PRAGMA quick_check').fetchone()[0]=='ok'
    with sqlite3.connect(live) as db:
        assert db.execute('SELECT bytes FROM metered').fetchone()[0]==250



def test_systemd_stop_failure_reactivates_old_hub_without_copying_source(tmp_path,monkeypatch):
    app=tmp_path/'app';(app/'.venv/bin').mkdir(parents=True)
    (app/'.venv/bin/python').write_text('fake executable')
    conf=tmp_path/'etc';conf.mkdir();(conf/'config.json').write_text('{}')
    data=tmp_path/'data';data.mkdir()
    candidate=tmp_path/'candidate';candidate.mkdir()
    monkeypatch.setattr(UPDATE,'APP',app)
    monkeypatch.setattr(UPDATE,'CONF',conf)
    monkeypatch.setattr(UPDATE,'DATA',data)
    monkeypatch.setattr(UPDATE,'DB',data/'dark.sqlite3')
    monkeypatch.setattr(UPDATE.os,'geteuid',lambda:0)
    monkeypatch.setattr(UPDATE,'database_preflight',lambda *a:{'bytes':2048,'quick_check':'ok'})
    monkeypatch.setattr(UPDATE,'require_live_panel',lambda *a:None)
    monkeypatch.setattr(UPDATE,'validate_source',lambda *a:None)
    monkeypatch.setattr(UPDATE,'disk_preflight',lambda *a:{'free_data_bytes':1000000000})
    monkeypatch.setattr(UPDATE,'dependency_preflight',lambda *a:None)
    monkeypatch.setattr(UPDATE,'source_snapshot',lambda *a:None)
    monkeypatch.setattr(UPDATE,'database_snapshot',lambda *a:None)
    monkeypatch.setattr(UPDATE,'source_version',lambda *a:'0.10.1-rc11')
    monkeypatch.setattr(UPDATE,'source_commit',lambda *a:'a'*40)
    monkeypatch.setattr(UPDATE,'copy_source',
                        lambda *a:pytest.fail('source must not be copied after failed stop'))
    calls=[]
    def fake_run(args,*a,**kw):
        calls.append(list(args))
        if list(args)==['systemctl','stop','dark-xray.service']:
            raise RuntimeError('synthetic systemd stop failure')
    monkeypatch.setattr(UPDATE,'run',fake_run)
    class Result:
        returncode=0
    monkeypatch.setattr(UPDATE,'quiet',lambda *a,**kw:Result())
    monkeypatch.setattr(sys,'argv',['update.py','--source',str(candidate),'--non-interactive'])
    with pytest.raises(SystemExit,match='original service restarted'):
        UPDATE.main()
    assert ['systemctl','stop','dark-xray.service'] in calls
    assert ['systemctl','start','dark-xray.service'] in calls
