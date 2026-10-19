import sqlite3
import sys
from pathlib import Path
import pytest
import traffic_summary as ts
from dark_policy import Store,PolicyError
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from seed_traffic_summary import seed

ENGINE='engine:'+'a'*20+':'

def source(path,n=10000):
    db=sqlite3.connect(path)
    db.executescript('''PRAGMA journal_mode=WAL;
    CREATE TABLE traffic_ledger(event_id TEXT PRIMARY KEY,owner TEXT NOT NULL,client_id TEXT NOT NULL,period INTEGER NOT NULL,up_bytes INTEGER NOT NULL,down_bytes INTEGER NOT NULL,observed_at REAL NOT NULL);
    CREATE TABLE clients(id TEXT PRIMARY KEY,used_bytes INTEGER);
    INSERT INTO clients VALUES('c',123456);
    ''')
    db.executemany('INSERT INTO traffic_ledger VALUES(?,?,?,?,?,?,?)',((ENGINE+str(i),'o','c',i%2,i,2*i,float(i)) for i in range(1,n+1)))
    db.execute("INSERT INTO traffic_ledger VALUES('external','o','c',0,9,8,1)")
    db.commit();return db

def test_seed_compact_restore_totals_replay_and_transactions(tmp_path):
    path=tmp_path/'source.sqlite3';db=source(path)
    expected=db.execute('SELECT owner,period,SUM(up_bytes),SUM(down_bytes),COUNT(*),MAX(observed_at) FROM traffic_ledger GROUP BY owner,period ORDER BY period').fetchall()
    seed(path,tmp_path/'stage.sqlite3',install=True)
    assert db.execute('SELECT * FROM traffic_totals ORDER BY period').fetchall()==expected
    target=tmp_path/'compact.sqlite3';ts.operational_snapshot(path,target)
    restored=sqlite3.connect(target);restored.row_factory=sqlite3.Row
    assert restored.execute('PRAGMA user_version').fetchone()[0]==5
    assert restored.execute('SELECT COUNT(*) FROM traffic_ledger').fetchone()[0]==250
    assert [tuple(r) for r in restored.execute('SELECT * FROM traffic_totals ORDER BY period')]==expected
    assert ts.previous_event(restored,'external')['up_bytes']==9
    with pytest.raises(PolicyError,match='replayed'):ts.previous_event(restored,ENGINE+'1')
    with pytest.raises(sqlite3.IntegrityError,match='replay'):
        restored.execute('INSERT INTO traffic_ledger VALUES(?,?,?,?,?,?,?)',(ENGINE+'1','o','c',0,1,2,1.))
    restored.rollback()
    event=(ENGINE+'10001','o','c',0,5,6,10001.)
    restored.execute('INSERT INTO traffic_ledger VALUES(?,?,?,?,?,?,?)',event)
    restored.rollback()
    assert [tuple(r) for r in restored.execute('SELECT * FROM traffic_totals ORDER BY period')]==expected
    restored.execute('INSERT INTO traffic_ledger VALUES(?,?,?,?,?,?,?)',event);restored.commit()
    assert restored.execute('SELECT up_bytes FROM traffic_totals WHERE period=0').fetchone()[0]==expected[0][2]+5
    assert target.stat().st_size<(db.execute('PRAGMA page_count').fetchone()[0]*4096)//5
    db.close();restored.close()

def test_unseeded_backup_refuses_and_does_not_create_output(tmp_path):
    path=tmp_path/'db';db=source(path,10);out=tmp_path/'out'
    with pytest.raises(PolicyError,match='initialized'):ts.operational_snapshot(path,out)
    assert not out.exists();db.close()

@pytest.mark.parametrize('event',[ENGINE+'1',ENGINE+'01',ENGINE+'0',ENGINE+'-1',ENGINE+'9223372036854775807',ENGINE+'9223372036854775808','node:'+'b'*24+':2','node:'+'g'*24+':2','external'])
def test_trigger_and_python_classification_agree(tmp_path,event):
    path=tmp_path/'db';db=source(path,0)
    db.execute('DELETE FROM traffic_ledger');ts.ensure(db);db.commit()
    db.execute('INSERT INTO traffic_ledger VALUES(?,?,?,?,?,?,?)',(event,'o','c',0,1,2,1.));db.commit()
    assert bool(db.execute('SELECT 1 FROM traffic_watermarks').fetchone())==bool(ts.internal_key(event))
    assert bool(db.execute('SELECT 1 FROM traffic_external_events').fetchone())== (not bool(ts.internal_key(event)))
    db.close()

def test_live_tail_caught_up_before_install(tmp_path,monkeypatch):
    import seed_traffic_summary as migration
    path=tmp_path/'db';db=source(path,5);fold=migration.fold;calls=[]
    def concurrent(src,dst,after=0):
        out=fold(src,dst,after)
        if not calls:
            db.execute('INSERT INTO traffic_ledger VALUES(?,?,?,?,?,?,?)',(ENGINE+'6','o','c',1,6,12,6.));db.commit()
        calls.append(out);return out
    monkeypatch.setattr(migration,'fold',concurrent)
    seed(path,tmp_path/'stage',install=True)
    assert db.execute('SELECT SUM(up_bytes),SUM(down_bytes) FROM traffic_totals').fetchone()==db.execute('SELECT SUM(up_bytes),SUM(down_bytes) FROM traffic_ledger').fetchone()
    db.close()

def test_compact_encrypted_roundtrip_and_store_accounting(tmp_path):
    import backup
    from test_backup_hardening import minimal_source,PASS
    data,config=minimal_source(tmp_path)
    with sqlite3.connect(data/'dark.sqlite3') as db:
        db.execute('DROP TABLE clients');db.execute('DROP TABLE owners');db.execute('ALTER TABLE api_admins ADD COLUMN role TEXT')
    store=Store(data/'dark.sqlite3')
    store.db.execute("INSERT INTO owners(id) VALUES('o')")
    store.db.execute("INSERT INTO clients(id,owner) VALUES('c','o')")
    for i in range(1,301):store.record_usage(ENGINE+str(i),'c',i,i+1,now=float(i))
    store.record_usage('external','c',7,8)
    total=store._usage(store.db,'o')
    archive=tmp_path/'compact.darkbackup'
    manifest=backup.create_backup(data,config,archive,PASS,profile='operational')
    assert manifest['schema']==3
    dest=tmp_path/'restore';backup.restore_backup(archive,dest,PASS)
    recovered=Store(dest/'data/dark.sqlite3')
    assert recovered._usage(recovered.db,'o')==total
    assert recovered.record_usage('external','c',7,8) is False
    with pytest.raises(PolicyError,match='different'):recovered.record_usage('external','c',8,8)
    with pytest.raises(PolicyError,match='replayed'):recovered.record_usage(ENGINE+'1','c',1,2)
    assert recovered.record_usage(ENGINE+'301','c',10,20) is True
    assert recovered._usage(recovered.db,'o')==total+30
    assert recovered.db.execute('SELECT used_bytes FROM clients').fetchone()[0]==total+30
    assert recovered.db.execute('PRAGMA user_version').fetchone()[0]==5
    store.db.close();recovered.db.close()
