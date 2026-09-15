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
