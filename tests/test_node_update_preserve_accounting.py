"""Node rollback never rewinds an accounting database with newer bytes."""
import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest

ROOT=Path(__file__).resolve().parents[1]
SPEC=importlib.util.spec_from_file_location('node_update_accounting',ROOT/'tools/update_node.py')
assert SPEC and SPEC.loader
NODE=importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(NODE)


def test_agent_activation_failure_keeps_newer_usage_and_restores_runtime(tmp_path,monkeypatch):
    app=tmp_path/'app';data=tmp_path/'data';transaction=tmp_path/'tx'
    src=tmp_path/'candidate';new_venv=tmp_path/'candidate-venv'
    for path in (app,data,transaction,src,new_venv):path.mkdir()
    (app/'.venv').mkdir()
    (app/'.venv'/'legacy').write_text('previous environment')
    (new_venv/'candidate').write_text('new environment')
    (app/'VERSION').write_text('0.10.1-rc11')
    meta=data/'installed-source.json'
    meta.write_text(json.dumps({'commit':'old','version':'0.10.1-rc11','ref':'stable'}))
    live=data/'node.sqlite3'
    with sqlite3.connect(live) as db:
        db.execute('CREATE TABLE metered(bytes INTEGER NOT NULL)')
        db.execute('INSERT INTO metered VALUES(100)')
    monkeypatch.setattr(NODE,'APP',app)
    monkeypatch.setattr(NODE,'DATA',data)
    monkeypatch.setattr(NODE,'source_snapshot',lambda path:path.write_bytes(b'source-only'))
    def snapshot(path):
        with sqlite3.connect(live) as a, sqlite3.connect(path) as b:a.backup(b)
        return live.stat()
    monkeypatch.setattr(NODE,'snapshot_database',snapshot)
    def candidate_source(_src):
        with sqlite3.connect(live) as db:db.execute('UPDATE metered SET bytes=220')
    monkeypatch.setattr(NODE,'copy_source',candidate_source)
    restored=[]
    monkeypatch.setattr(NODE,'restore_source',lambda path:restored.append(path))
    monkeypatch.setattr(NODE,'restore_database',lambda *a:pytest.fail('NODE ROLLBACK OVERWROTE LIVE METERS'))
    for method in ('service_import_probe','install_units','install_wrapper','install_logrotate','health_probe'):
        monkeypatch.setattr(NODE,method,lambda *a,**kw:None)
    monkeypatch.setattr(NODE,'atomic_bytes',lambda p,b,*a:p.write_bytes(b))
    starts=[0]
    def fake_run(args,**kwargs):
        if args[:2]==['systemctl','start']:
            starts[0]+=1
            if starts[0]==1:raise RuntimeError('synthetic candidate activation failure')
        return None
    monkeypatch.setattr(NODE,'run',fake_run)
    with pytest.raises(RuntimeError,match='LIVE accounting database preserved'):
        NODE._activate_preflighted(src,new_venv,'a'*40,'0.10.1-rc11','canary',transaction)
    with sqlite3.connect(live) as db:
        assert db.execute('SELECT bytes FROM metered').fetchone()[0]==220
    with sqlite3.connect(transaction/'node.sqlite3') as db:
        assert db.execute('SELECT bytes FROM metered').fetchone()[0]==100
    assert starts[0]==2
    assert len(restored)==1
    assert (app/'.venv'/'legacy').read_text()=='previous environment'
    assert json.loads(meta.read_text())['commit']=='old'
