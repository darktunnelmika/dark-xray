import json
import time

from test_standalone import env, create


def row_for(doc,email):
    return next(x for x in doc['items'] if x['email']==email)


def test_sync_v4_classifies_durable_states_and_runtime(env):
    store,engine,manager,_,c=env
    create(c,'sync-v4')
    now=time.time()

    with store.transaction() as db:
        db.execute("UPDATE managed_clients SET op='upsert',state='error',error='apply failed',attempts=2,retry_at=? WHERE email='sync-v4'",(now+60,))
    doc=c.get('/api/sync').json();row=row_for(doc,'sync-v4')
    assert row['reason_code']=='retry_wait'
    assert row['next_action']=='retry_now'
    assert row['automatic_retry'] is True
    assert row['retry_in_seconds']>0
    assert doc['summary']['retry_wait']==1
    assert doc['runtime']['generation_state']=='stopped_staged'
    assert doc['runtime']['next_action']=='start_apply'

    with store.transaction() as db:
        db.execute("UPDATE managed_clients SET op='reset',state='uncertain',error='ambiguous reset',retry_at=0 WHERE email='sync-v4'")
    doc=c.get('/api/sync').json();row=row_for(doc,'sync-v4')
    assert row['reason_code']=='uncertain_reset'
    assert row['next_action']=='resolve_reset'
    assert doc['summary']['action_required']==1

    with store.transaction() as db:
        db.execute("UPDATE managed_clients SET op='none',state='conflict',error='identity mismatch' WHERE email='sync-v4'")
    row=row_for(c.get('/api/sync').json(),'sync-v4')
    assert row['reason_code']=='identity_conflict' and row['drift'] is True
    assert row['next_action']=='inspect'

    with store.transaction() as db:
        db.execute("UPDATE managed_clients SET state='missing',error='runtime missing' WHERE email='sync-v4'")
    row=row_for(c.get('/api/sync').json(),'sync-v4')
    assert row['reason_code']=='runtime_missing'
    assert row['next_action']=='restore_missing'

    with store.transaction() as db:
        db.execute("UPDATE managed_clients SET state='applied',error='',external_disabled=1 WHERE email='sync-v4'")
    row=row_for(c.get('/api/sync').json(),'sync-v4')
    assert row['reason_code']=='external_disabled'
    assert row['next_action']=='restore_control'


def test_manual_retry_bypasses_backoff_and_reconciles(env):
    store,_,_,_,c=env
    create(c,'retry-v4')
    with store.transaction() as db:
        db.execute("UPDATE managed_clients SET op='upsert',state='error',error='temporary',attempts=3,retry_at=? WHERE email='retry-v4'",(time.time()+300,))
    before=row_for(c.get('/api/sync').json(),'retry-v4')
    assert before['reason_code']=='retry_wait'

    r=c.post('/api/clients/retry-v4/sync-retry',json={})
    assert r.status_code==200,r.text
    assert r.json()['meta']['state']=='applied'
    after=row_for(c.get('/api/sync').json(),'retry-v4')
    assert after['reason_code']=='clean'
    with store.lock:
        audit=store.db.execute("SELECT action FROM live_audit WHERE target='retry-v4' ORDER BY id DESC LIMIT 1").fetchone()
    assert audit and audit[0]=='sync.retry_now'


def test_missing_client_requires_explicit_restore_and_preserves_identity(env):
    store,engine,_,_,c=env
    created=create(c,'missing-v4')
    before=engine.client_detail('missing-v4')['client']
    with store.transaction() as db:
        db.execute("DELETE FROM core_clients WHERE email='missing-v4'")
        db.execute("UPDATE managed_clients SET op='none',state='missing',error='CoreEngine client missing; automatic recreation refused' WHERE email='missing-v4'")

    bad=c.post('/api/clients/missing-v4/restore-missing',json={'confirmation':'wrong'})
    assert bad.status_code==403
    assert all(x['email']!='missing-v4' for x in engine.clients())

    r=c.post('/api/clients/missing-v4/restore-missing',json={'confirmation':'missing-v4'})
    assert r.status_code==200,r.text
    assert r.json()['meta']['state']=='applied'
    after=engine.client_detail('missing-v4')['client']
    assert after['subId']==before['subId']
    assert after['id']==before['id']
    assert row_for(c.get('/api/sync').json(),'missing-v4')['reason_code']=='clean'


def test_sync_v4_excludes_deleted_history_but_audit_remains(env):
    store,_,_,_,c=env
    create(c,'deleted-v4')
    with store.transaction() as db:
        db.execute("UPDATE managed_clients SET state='deleted',op='none',desired='{}' WHERE email='deleted-v4'")
    doc=c.get('/api/sync').json()
    assert all(x['email']!='deleted-v4' for x in doc['items'])
    assert doc['summary']['total']==0
    with store.lock:
        assert store.db.execute("SELECT COUNT(*) FROM live_audit WHERE target='deleted-v4'").fetchone()[0]>=1


def test_sync_v4_node_summary_uses_persisted_assignment_state(env):
    store,_,_,_,c=env
    create(c,'node-sync-v4')
    r=c.post('/api/nodes',json={'id':'sync-node','name':'SYNC NODE','origin':'https://sync-node.example.test',
        'dataAddress':'sync-node.example.test','priority':10,'failoverEnabled':True,
        'token':'dkn_'+('R'*60),'enabled':True,'inboundIds':[1]})
    assert r.status_code==200,r.text
    with store.transaction() as db:
        db.execute("UPDATE remote_nodes SET last_seen=?,last_latency_ms=7,last_error='' WHERE id='sync-node'",(time.time(),))
    doc=c.get('/api/sync').json()
    assert doc['nodes']['total']==1
    assert doc['nodes']['online']==1
    assert doc['nodes']['pending_deploys']==1
