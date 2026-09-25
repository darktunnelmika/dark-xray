import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest

ROOT=Path(__file__).resolve().parents[1]
SPEC=importlib.util.spec_from_file_location('dark_update_tx',ROOT/'tools/update.py')
assert SPEC and SPEC.loader
UPDATE=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(UPDATE)


def make_db(path:Path,value:str='before'):
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE state(value TEXT NOT NULL)')
        db.execute('INSERT INTO state VALUES(?)',(value,));db.execute('PRAGMA user_version=7');db.commit()


def value(path:Path):
    with sqlite3.connect(path) as db:return db.execute('SELECT value FROM state').fetchone()[0]


def test_database_snapshot_restore_roundtrip(tmp_path):
    db=tmp_path/'dark.sqlite3';snap=tmp_path/'rollback.sqlite3';make_db(db)
    info=UPDATE.database_preflight(db);assert info['quick_check']=='ok' and info['user_version']==7
    UPDATE.database_snapshot(snap,db)
    with sqlite3.connect(db) as con:con.execute("UPDATE state SET value='after'");con.commit()
    UPDATE.restore_database(snap,db)
    assert value(db)=='before' and UPDATE.database_preflight(db)['quick_check']=='ok'


def test_database_preflight_rejects_symlink(tmp_path):
    real=tmp_path/'real.sqlite3';make_db(real);link=tmp_path/'dark.sqlite3';link.symlink_to(real)
    with pytest.raises(SystemExit,match='unsafe'):UPDATE.database_preflight(link)


def test_database_preflight_rejects_non_sqlite(tmp_path):
    bad=tmp_path/'dark.sqlite3';bad.write_text('not sqlite')
    with pytest.raises(SystemExit,match='cannot be opened'):UPDATE.database_preflight(bad)


def test_doctor_gate_requires_config_db_and_panel_route(monkeypatch):
    state={'checks':{'configuration':'ok','database':'ok','panel_route':{'ok':True,'ui_status':200,'asset_status':200}}}
    class CP:
        returncode=0
        @property
        def stdout(self):return json.dumps(state)
    monkeypatch.setattr(UPDATE.subprocess,'run',lambda *a,**k:CP())
    assert UPDATE._doctor_once()[0] is True
    state['checks']['panel_route']['asset_status']=404;state['checks']['panel_route']['ok']=False
    assert UPDATE._doctor_once()[0] is False


def test_source_version_is_strict(tmp_path):
    (tmp_path/'VERSION').write_text('0.7.2-standalone-lab\n')
    assert UPDATE.source_version(tmp_path)=='0.7.2-standalone-lab'
    (tmp_path/'VERSION').write_text('latest please')
    with pytest.raises(SystemExit,match='malformed'):UPDATE.source_version(tmp_path)


def test_legacy_doctor_without_panel_route_uses_strict_compat_probe(monkeypatch):
    state={'checks':{'configuration':'ok','database':'ok'}}
    class CP:
        returncode=0
        @property
        def stdout(self):return json.dumps(state)
    monkeypatch.setattr(UPDATE.subprocess,'run',lambda *a,**k:CP())
    monkeypatch.setattr(UPDATE,'_compat_panel_route',lambda:{'ok':True,'ui_status':200,'asset_status':200,'probe':'legacy-compat'})
    ok,detail=UPDATE._doctor_once()
    assert ok is True and 'legacy-compat' in detail


def test_legacy_doctor_compat_probe_remains_fail_closed(monkeypatch):
    state={'checks':{'configuration':'ok','database':'ok'}}
    class CP:
        returncode=0
        @property
        def stdout(self):return json.dumps(state)
    monkeypatch.setattr(UPDATE.subprocess,'run',lambda *a,**k:CP())
    monkeypatch.setattr(UPDATE,'_compat_panel_route',lambda:{'ok':False,'ui_status':200,'asset_status':404,'probe':'legacy-compat'})
    assert UPDATE._doctor_once()[0] is False


def test_rollback_reactivation_tolerates_initial_systemd_restart_failure(monkeypatch):
    calls=[]
    states={'doctor':0}

    class CP:
        def __init__(self,returncode=0):self.returncode=returncode

    def fake_quiet(args):
        args=[str(x) for x in args];calls.append(args)
        if args[:3]==['systemctl','restart','dark-xray.service']:
            return CP(1)
        if args[:4]==['systemctl','is-active','--quiet','dark-xray.service']:
            return CP(1 if states['doctor']==0 else 0)
        if args[:2]==['systemctl','start']:
            states['doctor']=1
        return CP(0)

    def fake_require_live_panel(timeout=10.0):
        if states['doctor']==0:
            states['doctor']=1
            raise RuntimeError('service still recovering')

    monkeypatch.setattr(UPDATE,'quiet',fake_quiet)
    monkeypatch.setattr(UPDATE,'require_live_panel',fake_require_live_panel)
    monkeypatch.setattr(UPDATE.time,'sleep',lambda _seconds:None)
    UPDATE.reactivate_previous_panel(timeout=2.0)
    assert ['systemctl','reset-failed','dark-xray.service'] in calls
    assert ['systemctl','kill','--kill-who=all','dark-xray.service'] in calls
    assert ['systemctl','stop','--no-block','dark-xray.service'] in calls
    assert ['systemctl','start','--no-block','dark-xray.service'] in calls


def test_status_clears_stale_error_on_successful_rollback(tmp_path,monkeypatch):
    status=tmp_path/'status.json'
    status.write_text(json.dumps({'state':'failed','error':'old failure'}))
    monkeypatch.setattr(UPDATE,'STATUS_FILE',status)
    monkeypatch.setattr(UPDATE,'JOB_ID','job')
    UPDATE._status('rolled_back','rollback',100,'ok',rollback_ok=True)
    doc=json.loads(status.read_text())
    assert doc['state']=='rolled_back' and doc['rollback_ok'] is True
    assert 'error' not in doc
