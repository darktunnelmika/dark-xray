#!/usr/bin/env python3
from pathlib import Path


def once(path,old,new):
    p=Path(path);s=p.read_text()
    if old not in s:raise SystemExit(f'anchor missing in {path}: {old[:150]!r}')
    p.write_text(s.replace(old,new,1))

# Durable deleted-core snapshot. The snapshot and core row removal occur in one
# SQLite transaction so a process crash cannot leave Manager without the final
# counters it needs for the immutable traffic ledger.
once('backend/core.py',
"""            CREATE TABLE IF NOT EXISTS core_devices(id INTEGER PRIMARY KEY AUTOINCREMENT,email TEXT NOT NULL,
              digest TEXT NOT NULL,device_os TEXT NOT NULL,model TEXT NOT NULL,first_seen REAL NOT NULL,last_seen REAL NOT NULL,
              UNIQUE(email,digest));
            ''')
""",
"""            CREATE TABLE IF NOT EXISTS core_devices(id INTEGER PRIMARY KEY AUTOINCREMENT,email TEXT NOT NULL,
              digest TEXT NOT NULL,device_os TEXT NOT NULL,model TEXT NOT NULL,first_seen REAL NOT NULL,last_seen REAL NOT NULL,
              UNIQUE(email,digest));
            CREATE TABLE IF NOT EXISTS core_client_tombstones(
              email TEXT PRIMARY KEY,body TEXT NOT NULL,inbounds TEXT NOT NULL,
              up INTEGER NOT NULL,down INTEGER NOT NULL,deleted_at REAL NOT NULL);
            ''')
""")

old="""    @serialized
    def delete(self,email:str):
        self._write();self.collect_stats(force=True)
        with self.store.lock:
            r=self.store.db.execute('SELECT body,inbounds,up,down FROM core_clients WHERE email=?',(email,)).fetchone()
            final=(json.loads(r['body'])|{'inboundIds':json.loads(r['inbounds']),'traffic':{'up':r['up'],'down':r['down']}}) if r else None
        with self.store.transaction() as db:
            db.execute('DELETE FROM core_clients WHERE email=?',(email,));db.execute('DELETE FROM core_devices WHERE email=?',(email,))
        # Usernames remain reserved in managed_clients tombstones.
        return final
"""
new="""    @serialized
    def delete(self,email:str):
        self._write();self.collect_stats(force=True)
        final=None
        with self.store.transaction() as db:
            r=db.execute('SELECT body,inbounds,up,down FROM core_clients WHERE email=?',(email,)).fetchone()
            if r:
                final=json.loads(r['body'])|{'inboundIds':json.loads(r['inbounds']),'traffic':{'up':r['up'],'down':r['down']}}
                db.execute('''INSERT INTO core_client_tombstones(email,body,inbounds,up,down,deleted_at)
                    VALUES(?,?,?,?,?,?) ON CONFLICT(email) DO UPDATE SET body=excluded.body,
                    inbounds=excluded.inbounds,up=excluded.up,down=excluded.down,deleted_at=excluded.deleted_at''',
                    (email,r['body'],r['inbounds'],r['up'],r['down'],time.time()))
            db.execute('DELETE FROM core_clients WHERE email=?',(email,));db.execute('DELETE FROM core_devices WHERE email=?',(email,))
        # Usernames remain reserved in managed_clients tombstones.
        return final

    def deleted_client_snapshot(self,email:str)->dict|None:
        with self.store.lock:r=self.store.db.execute('SELECT body,inbounds,up,down,deleted_at FROM core_client_tombstones WHERE email=?',(email,)).fetchone()
        if not r:return None
        return json.loads(r['body'])|{'inboundIds':json.loads(r['inbounds']),
            'traffic':{'up':r['up'],'down':r['down']},'deletedAt':r['deleted_at']}
"""
if old not in Path('backend/core.py').read_text():raise SystemExit('core delete anchor changed')
once('backend/core.py',old,new)

# Delete becomes crash-idempotent and recovers the final traffic snapshot from
# the core tombstone if the core row disappeared before Manager finalized.
old="""        if op=='delete':
            if existing:
                if existing.get('subId')!=desired.get('subId'):raise CoreError('Identity conflict: refusing to delete a different engine client',status=409)
                final=self.engine.delete(email)
                if final:self._charge_snapshot(self.meta(email),final)
            self.store.delete_client(SYSTEM,email)
            with self.store.transaction() as db:
                db.execute(\"UPDATE managed_clients SET op='none',state='deleted',desired='{}',error='',updated_at=? WHERE email=?\",(time.time(),email))
            return
"""
new="""        if op=='delete':
            final=None
            if existing:
                if existing.get('subId')!=desired.get('subId'):raise CoreError('Identity conflict: refusing to delete a different engine client',status=409)
                final=self.engine.delete(email)
            else:
                # A prior process may have committed the core deletion and crashed
                # before Manager charged/finalized it. Core keeps the final counters
                # in a durable tombstone specifically for this recovery path.
                final=self.engine.deleted_client_snapshot(email)
                if final and final.get('subId')!=desired.get('subId'):
                    raise CoreError('Deleted core snapshot identity conflict; refusing recovery',status=409)
            if final:self._charge_snapshot(self.meta(email),final)
            with self.store.lock:policy_exists=self.store.db.execute('SELECT 1 FROM clients WHERE id=?',(email,)).fetchone() is not None
            if policy_exists:self.store.delete_client(SYSTEM,email)
            with self.store.transaction() as db:
                db.execute(\"UPDATE managed_clients SET op='none',state='deleted',desired='{}',error='',retry_at=0,attempts=0,updated_at=? WHERE email=?\",(time.time(),email))
            return
"""
once('backend/manager.py',old,new)

# Resolving an uncertain scheduled reset means "accept current outcome, do not
# replay". Consume the due schedule before tick() so _schedule_cycles cannot
# immediately enqueue the same destructive reset again.
old="""            with self.store.transaction() as db:
                db.execute(\"UPDATE managed_clients SET op='none',state='applied',error='',retry_at=0,attempts=0 WHERE email=?\",(email,))
            row=self.own_row(actor,email)
            self.audit(actor,row['owner'],'reset.resolve_current',email,'Current engine counters accepted; destructive reset NOT replayed')
            self.tick(suppress=False)
"""
new="""            with self.store.transaction() as db:
                db.execute(\"UPDATE managed_clients SET op='none',state='applied',error='',retry_at=0,attempts=0 WHERE email=?\",(email,))
            self._complete_cycle(email)
            row=self.own_row(actor,email)
            self.audit(actor,row['owner'],'reset.resolve_current',email,'Current engine counters accepted; destructive reset NOT replayed; due reset cycle advanced')
            self.tick(suppress=False)
"""
once('backend/manager.py',old,new)

Path('tests/test_destructive_recovery.py').write_text(r'''import json,time
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
''')

once('tests/run-tests.sh',
"""python -m pytest tests/test_accounting_stability.py -q --junitxml=qa/junit/accounting-stability.xml
""",
"""python -m pytest tests/test_accounting_stability.py -q --junitxml=qa/junit/accounting-stability.xml
python -m pytest tests/test_destructive_recovery.py -q --junitxml=qa/junit/destructive-recovery.xml
""")
Path('VERSION').write_text('0.7.4-standalone-lab\n')
print('0.7.4 destructive recovery patch applied')
