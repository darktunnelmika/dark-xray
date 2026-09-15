import json,time
import pytest
from test_standalone import env,create,OWNER


def test_uncertain_periodic_reset_resolution_advances_cycle_without_replay(env,monkeypatch):
    store,engine,m,auth,c=env
    create(c,extra={'reset':1,'resetCount':3})
    with store.transaction() as db:
        db.execute('UPDATE client_cycles SET next_at=? WHERE email=?',(time.time()-5,'dark-test'))
        db.execute("UPDATE managed_clients SET op='reset',state='reset_inflight' WHERE email=?",('dark-test',))
    m.recover_resets()
    assert m.meta('dark-test')['state']=='uncertain'
    calls=[]
    def no_replay(email):
        calls.append(email)
        raise AssertionError('destructive reset was replayed')
    monkeypatch.setattr(engine,'reset',no_replay)
    r=c.post('/api/clients/dark-test/resolve-reset',json={'confirmation':'dark-test'})
    assert r.status_code==200,r.text
    assert calls==[]
    meta=m.meta('dark-test');assert meta['op']=='none' and meta['state']=='applied'
    with store.lock:cycle=store.db.execute('SELECT completed,next_at FROM client_cycles WHERE email=?',('dark-test',)).fetchone()
    assert cycle['completed']==1 and cycle['next_at']>time.time()
    m.tick(suppress=False);assert calls==[] and m.meta('dark-test')['op']=='none'


def test_delete_recovery_uses_durable_core_tombstone_and_finalizes_once(env):
    store,engine,m,auth,c=env
    create(c)
    with store.transaction() as db:
        db.execute('UPDATE core_clients SET up=33,down=7 WHERE email=?',('dark-test',))
        db.execute("UPDATE managed_clients SET op='delete',state='pending' WHERE email=?",('dark-test',))
    # Simulate the exact crash window: core deletion committed, Manager did not yet
    # charge the final snapshot or delete the policy client.
    final=engine.delete('dark-test')
    assert final['traffic']=={'up':33,'down':7}
    snap=engine.deleted_client_snapshot('dark-test');assert snap['traffic']=={'up':33,'down':7}
    with store.lock:assert store.db.execute('SELECT 1 FROM clients WHERE id=?',('dark-test',)).fetchone()
    m.tick(suppress=False)
    assert m.meta('dark-test')['state']=='deleted'
    with store.lock:
        assert store.db.execute('SELECT 1 FROM clients WHERE id=?',('dark-test',)).fetchone() is None
        ledger=store.db.execute('SELECT COALESCE(SUM(up_bytes+down_bytes),0) FROM traffic_ledger WHERE client_id=?',('dark-test',)).fetchone()[0]
    assert ledger==40
    # Reconciliation is idempotent: no second charge after the managed tombstone.
    m.tick(suppress=False)
    with store.lock:again=store.db.execute('SELECT COALESCE(SUM(up_bytes+down_bytes),0) FROM traffic_ledger WHERE client_id=?',('dark-test',)).fetchone()[0]
    assert again==40


def test_delete_recovery_after_final_charge_does_not_double_count(env):
    store,engine,m,auth,c=env
    create(c)
    with store.transaction() as db:
        db.execute('UPDATE core_clients SET up=21 WHERE email=?',('dark-test',))
        db.execute("UPDATE managed_clients SET op='delete',state='pending' WHERE email=?",('dark-test',))
    final=engine.delete('dark-test')
    m._charge_snapshot(m.meta('dark-test'),final)
    before=store.owner_stats(OWNER,'dark')['used_bytes'];assert before==21
    # Simulate crash after charging but before policy/tombstone finalization.
    m.tick(suppress=False)
    after=store.owner_stats(OWNER,'dark')['used_bytes'];assert after==21
    assert m.meta('dark-test')['state']=='deleted'
