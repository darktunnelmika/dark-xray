import base64
import json
import sqlite3
import pytest
from fastapi.testclient import TestClient
from test_standalone import env, IB, OWNER
from dark_restore import DarkRestore
from dark_policy import PolicyError

META={'status':'verified','error':'','upload':3000,'download':7000,'total':50000,'expire':2000000000}

def prepare(env,monkeypatch):
    store,engine,manager,auth,c=env
    inbound=c.post('/api/inbounds',json=IB).json()['id']
    restore=c.app.state.dark_restore
    monkeypatch.setattr(restore,'_scan',lambda url:dict(META))
    return store,engine,c,restore,inbound

def add(c,inbound,group='Rep A',suffix='one',**extra):
    response=c.post('/api/dark-restore/import',json={
        'urls':['https://legacy.example/sub/'+suffix],
        'inboundIds':[inbound],'groupName':group,**extra})
    assert response.status_code==200,response.text
    return response.json()

def row(c,rid=None):
    items=c.get('/api/dark-restore').json()['items']
    return next(x for x in items if x['id']==rid) if rid else items[0]

def counters(store,email,up,down):
    with store.transaction() as db:
        db.execute('UPDATE core_clients SET up=?,down=? WHERE email=?',(up,down,email))

def test_group_created_with_scan_and_existing_group_reused(env,monkeypatch):
    store,engine,c,restore,inbound=prepare(env,monkeypatch)
    a=add(c,inbound);gid=a['group']['id']
    add(c,inbound,group='',suffix='two',groupId=gid)
    doc=c.get('/api/dark-restore').json()
    assert doc['usage_scope']=='since_migration'
    group=next(g for g in doc['groups'] if g['id']==gid)
    assert (group['name'],group['clients'],group['dark_used'])==('Rep A',2,0)
    assert all(x['group_id']==gid and x['dark_used']==0 for x in doc['items'])
    assert c.get('/api/clients').json()==[] and c.get('/api/unmanaged').json()==[]
    assert store.db.execute('SELECT count(*) FROM clients').fetchone()[0]==0

def test_auto_groups_do_not_mix_unnamed_import_batches(env,monkeypatch):
    _,_,c,_,inbound=prepare(env,monkeypatch)
    first=add(c,inbound,group='')['group']['id']
    second=add(c,inbound,group='',suffix='two')['group']['id']
    assert first!=second
    assert len(c.get('/api/dark-restore',params={'groupId':first}).json()['items'])==1

def test_primary_dark_usage_excludes_legacy_but_remaining_is_preserved(env,monkeypatch):
    store,_,c,_,inbound=prepare(env,monkeypatch)
    add(c,inbound);x=row(c)
    assert x['dark_used']==0 and x['legacy_used']==10000 and x['remaining']==40000
    counters(store,x['core_email'],1000,2000)
    x=row(c);assert x['dark_used']==3000 and x['effective_used']==13000 and x['remaining']==37000
    group=next(g for g in c.get('/api/dark-restore').json()['groups'] if g['id']==x['group_id'])
    assert group['dark_used']==3000
    response=c.get('/restore/sub/'+x['public_token']+'?format=raw')
    assert response.status_code==200
    assert response.headers['subscription-userinfo']=='upload=4000; download=9000; total=50000; expire=2000000000'

def test_local_resets_and_reinitialization_do_not_lose_or_double_count_usage(env,monkeypatch):
    store,engine,c,restore,inbound=prepare(env,monkeypatch)
    add(c,inbound);x=row(c);email=x['core_email']
    counters(store,email,100,200);counters(store,email,100,200)
    engine.reset(email);counters(store,email,3,7)
    assert row(c)['dark_used']==310
    DarkRestore(store,engine,c.app.state.nodes)
    assert row(c)['dark_used']==310
    counters(store,email,4,8)
    assert row(c)['dark_used']==312
    for _ in range(3):assert row(c)['dark_used']==312

def test_reimport_never_rescans_taken_over_url_or_resets_credentials_usage_group(env,monkeypatch):
    store,engine,c,restore,inbound=prepare(env,monkeypatch)
    add(c,inbound);x=row(c);body=engine.client_detail(x['core_email'])['client']
    counters(store,x['core_email'],123,456)
    assert c.get('/restore/sub/'+x['public_token']).status_code==200
    def must_not_scan(url):raise AssertionError('Took over URL must never become a new legacy baseline')
    monkeypatch.setattr(restore,'_scan',must_not_scan)
    again=add(c,inbound,group='',groupId=x['group_id'])
    assert again['updated']==1
    new=row(c)
    for key in ('id','core_email','public_token','group_id','created_at','legacy_upload','legacy_download','legacy_total','legacy_expire'):
        assert new[key]==x[key]
    assert new['dark_used']==579 and new['first_seen']>0
    assert engine.client_detail(x['core_email'])['client']==body

def test_failed_rescan_preserves_partial_legacy_quota(env,monkeypatch):
    _,_,c,restore,inbound=prepare(env,monkeypatch)
    monkeypatch.setattr(restore,'_scan',lambda _:dict(META,status='partial'))
    add(c,inbound);x=row(c)
    monkeypatch.setattr(restore,'_scan',lambda _:{'status':'partial','error':'offline','upload':0,'download':0,'total':0,'expire':0})
    add(c,inbound)
    assert row(c)['legacy_total']==x['legacy_total'] and row(c)['legacy_expire']==x['legacy_expire']

def test_group_conflict_is_reported_without_moving_or_updating_customer(env,monkeypatch):
    store,_,c,_,inbound=prepare(env,monkeypatch)
    add(c,inbound);old=row(c)
    other=add(c,inbound,group='Rep B')
    assert other['created']==other['updated']==0 and len(other['conflicts'])==1
    assert row(c)==old
    assert c.get('/api/dark-restore',params={'groupId':other['group']['id']}).json()['items']==[]

def test_bulk_group_assignment_preserves_client_and_quota_and_usage(env,monkeypatch):
    store,engine,c,_,inbound=prepare(env,monkeypatch)
    add(c,inbound);x=row(c);counters(store,x['core_email'],111,222)
    original=engine.client_detail(x['core_email'])
    group=c.post('/api/dark-restore/groups',json={'name':'نماینده دوم'}).json()
    assigned=c.post('/api/dark-restore/groups/assign',json={'ids':[x['id']],'groupId':group['id']})
    assert assigned.status_code==200,assigned.text
    new=row(c)
    assert new['group_id']==group['id'] and new['dark_used']==333
    assert new['public_token']==x['public_token'] and new['created_at']==x['created_at']
    assert engine.client_detail(x['core_email'])==original
    assert c.put('/api/dark-restore/groups/'+group['id'],json={'name':'Rep renamed'}).status_code==200
    assert row(c)['group_name']=='Rep renamed'
    bad=c.post('/api/dark-restore/groups/assign',json={'ids':[x['id'],'missing'],'groupId':x['group_id']})
    assert bad.status_code==404 and row(c)['group_id']==group['id']

def test_group_validation_and_duplicate_url_in_batch(env,monkeypatch):
    _,_,c,_,inbound=prepare(env,monkeypatch)
    assert c.post('/api/dark-restore/groups',json={'name':'   '}).status_code==400
    a=c.post('/api/dark-restore/groups',json={'name':' Rep A '}).json()
    b=c.post('/api/dark-restore/groups',json={'name':'rep a'}).json()
    assert a['id']==b['id']
    bad=add(c,inbound,urls=['https://legacy.example/sub/one']*2)
    assert bad['created']==1 and bad['duplicates']==1
    assert c.post('/api/dark-restore/import',json={'inboundIds':[inbound],'urls':['https://legacy.example/sub/two'],'groupId':'missing'}).status_code==404


def test_archive_finished_preserves_group_traffic_and_restore_history(env,monkeypatch):
    store,engine,c,_,inbound=prepare(env,monkeypatch)
    add(c,inbound);x=row(c);rid=x['id'];gid=x['group_id'];email=x['core_email']
    counters(store,email,1234,4321)
    before=c.get('/api/dark-restore').json()
    group_before=next(g for g in before['groups'] if g['id']==gid)
    assert group_before['dark_used']==5555 and before['archive']['finished']==0

    # Mark the migrated service finished without deleting any history.
    with store.transaction() as db:
        db.execute('UPDATE restore_subscriptions SET legacy_expire=? WHERE id=?',(int(__import__('time').time())-1,rid))

    summary=c.get('/api/dark-restore').json()['archive']
    assert summary['finished']==1
    out=c.post('/api/dark-restore/archive-finished')
    assert out.status_code==200,out.text
    assert out.json()['archived']==1 and out.json()['history_preserved'] is True

    doc=c.get('/api/dark-restore').json()
    assert doc['items']==[]
    group=next(g for g in doc['groups'] if g['id']==gid)
    assert group['clients']==0 and group['archived_clients']==1
    assert group['dark_used']==5555
    with store.lock:
        archived=store.db.execute('SELECT deleted_at,deleted_reason FROM restore_subscriptions WHERE id=?',(rid,)).fetchone()
        usage=store.db.execute('SELECT SUM(up+down) FROM restore_usage WHERE restore_id=?',(rid,)).fetchone()[0]
        core=store.db.execute('SELECT 1 FROM core_clients WHERE email=?',(email,)).fetchone()
    assert archived['deleted_at']>0 and archived['deleted_reason']=='finished'
    assert usage==5555 and core is None
    assert c.get('/restore/sub/'+x['public_token']).status_code==410


def test_manual_restore_delete_is_soft_archive_and_keeps_usage(env,monkeypatch):
    store,engine,c,_,inbound=prepare(env,monkeypatch)
    add(c,inbound);x=row(c);counters(store,x['core_email'],10,20)
    response=c.delete('/api/dark-restore/'+x['id'])
    assert response.status_code==200,response.text
    assert response.json()['archived'] is True
    assert c.get('/api/dark-restore').json()['items']==[]
    with store.lock:
        assert store.db.execute('SELECT deleted_at FROM restore_subscriptions WHERE id=?',(x['id'],)).fetchone()[0]>0
        assert store.db.execute('SELECT SUM(up+down) FROM restore_usage WHERE restore_id=?',(x['id'],)).fetchone()[0]==30

def test_group_and_usage_tables_survive_sqlite_backup(env,monkeypatch,tmp_path):
    store,_,c,_,inbound=prepare(env,monkeypatch)
    add(c,inbound);x=row(c);counters(store,x['core_email'],2,9)
    dest=sqlite3.connect(tmp_path/'backup.sqlite3')
    with store.lock:store.db.backup(dest)
    assert dest.execute('PRAGMA quick_check').fetchone()[0]=='ok'
    assert dest.execute('SELECT sum(up+down) FROM restore_usage').fetchone()[0]==11
    assert dest.execute('SELECT group_id,public_token FROM restore_subscriptions').fetchone()==(x['group_id'],x['public_token'])
    dest.close()

def test_upgrade_old_schema_seeds_only_dark_counters_once(env,monkeypatch):
    store,engine,c,_,inbound=prepare(env,monkeypatch)
    add(c,inbound);x=row(c);counters(store,x['core_email'],17,23)
    original=engine.client_detail(x['core_email'])
    with store.transaction() as db:
        db.execute('DROP TRIGGER restore_local_usage_v1')
        db.execute('DROP TABLE restore_usage')
        db.execute('DROP INDEX restore_by_group')
        db.execute('ALTER TABLE restore_subscriptions DROP COLUMN group_id')
        db.execute('DROP TABLE restore_groups')
    a=DarkRestore(store,engine,c.app.state.nodes)
    migrated=a.rows()[0]
    assert migrated['group_id']=='grp_ungrouped'
    assert migrated['dark_used']==40 and migrated['legacy_used']==10000
    assert engine.client_detail(x['core_email'])==original
    DarkRestore(store,engine,c.app.state.nodes)
    assert a.rows()[0]['dark_used']==40

def test_restore_node_usage_uses_separate_ledger_and_counts_first_sample(env,monkeypatch):
    store,engine,c,restore,inbound=prepare(env,monkeypatch)
    monkeypatch.setattr('nodes.socket.getaddrinfo',lambda *a,**k:[(2,1,6,'',('93.184.216.34',443))])
    for n in ('n1','n2'):
        r=c.post('/api/nodes',json={'id':n,'name':n,'origin':'https://'+n+'.example.com','token':'dkn_'+'T'*60,'enabled':True,'inboundIds':[inbound]})
        assert r.status_code==200,r.text
    add(c,inbound);x=row(c);email=x['core_email']
    counters(store,email,1,2)
    registry=c.app.state.nodes
    for node in ('n1','n2'):
        result=registry.apply_traffic_snapshot(node,[{'sourceEmail':email,'up':100,'down':200}],captured_at=1000)
        assert result['restore_usage']['clients']==1 and result['charged_bytes']==0
    assert row(c)['dark_used']==603
    registry.apply_traffic_snapshot('n1',[{'sourceEmail':email,'up':100,'down':200}],captured_at=1010)
    registry.apply_traffic_snapshot('n1',[{'sourceEmail':email,'up':7,'down':3}],captured_at=1020)
    registry.apply_traffic_snapshot('n1',[{'sourceEmail':email,'up':300,'down':400}],captured_at=999)
    assert row(c)['dark_used']==613
    assert store.db.execute('SELECT count(*) FROM traffic_ledger').fetchone()[0]==0
    assert store.db.execute('SELECT count(*) FROM clients').fetchone()[0]==0
    DarkRestore(store,engine,registry)
    registry.apply_traffic_snapshot('n1',[{'sourceEmail':email,'up':8,'down':4}],captured_at=1030)
    assert row(c)['dark_used']==615
    assert store.db.execute('SELECT count(*) FROM restore_usage WHERE scope<>?',('local',)).fetchone()[0]==2

def test_unassigned_node_snapshot_never_charges_restore(env,monkeypatch):
    store,_,c,restore,inbound=prepare(env,monkeypatch)
    add(c,inbound);x=row(c)
    assert restore.record_node_usage('unassigned',[{'sourceEmail':x['core_email'],'up':100,'down':200}])=={'clients':0}
    assert row(c)['dark_used']==0

def test_anonymous_and_reseller_cannot_manage_restore_groups(env,monkeypatch):
    _,_,c,_,inbound=prepare(env,monkeypatch)
    store,engine,manager,auth,_=env
    manager.owner_put(OWNER,'rep',name='Representative',allowed=[inbound])
    auth.admin_create(OWNER,'rep','RepPassword12345','reseller',{})
    c.post('/api/auth/logout')
    assert c.get('/api/dark-restore').status_code==401
    login=c.post('/api/auth/login',json={'username':'rep','password':'RepPassword12345'})
    assert login.status_code==200,login.text
    c.headers['X-Dark-CSRF']=login.json()['csrf']
    assert c.get('/api/dark-restore').status_code==403
    assert c.post('/api/dark-restore/groups',json={'name':'intrusion'}).status_code==403
    assert c.post('/api/dark-restore/groups/assign',json={'ids':['rst_bad'],'groupId':'grp_ungrouped'}).status_code==403
