import copy
import json
import time

from test_standalone import env,IB,OWNER


META={'status':'verified','error':'','upload':3000,'download':7000,'total':50000,'expire':2000000000}


def prepare(env,monkeypatch,total=50000,suffix='one'):
    store,engine,manager,auth,c=env
    inbound=c.post('/api/inbounds',json=copy.deepcopy(IB)).json()['id']
    restore=c.app.state.dark_restore
    monkeypatch.setattr(restore,'_scan',lambda url:{**META,'total':total})
    r=c.post('/api/dark-restore/import',json={
        'urls':['https://legacy.example/sub/'+suffix],'inboundIds':[inbound],
        'groupName':'Restore Closeout','scan':True})
    assert r.status_code==200,r.text
    item=c.get('/api/dark-restore').json()['items'][0]
    return store,engine,manager,auth,c,restore,inbound,item


def make_rep(manager,auth,inbound,*,volume=100000,unlimited=5):
    manager.owner_put(OWNER,'restore_rep',name='Restore Rep',allowed=[inbound],
                      volume_credit_bytes=volume,unlimited_credit=unlimited,max_clients=50,
                      prefix='rr_',max_client_ips=2,max_client_hwid=1)
    auth.admin_create(OWNER,'restore_rep','RestoreRepPassword88','reseller',{})


def test_closeout_rows_separate_plan_type_and_real_traffic_presence(env,monkeypatch):
    store,engine,manager,auth,c,restore,inbound,item=prepare(env,monkeypatch)
    assert item['plan_type']=='limited' and item['presence_state']=='offline' and item['promoted'] is False
    with store.transaction() as db:
        db.execute('UPDATE core_clients SET up=100,down=200 WHERE email=?',(item['core_email'],))
    current=c.get('/api/dark-restore').json()
    row=current['items'][0];group=next(g for g in current['groups'] if g['id']==row['group_id'])
    assert row['presence_state']=='online' and row['dark_used']==300
    assert group['limited']==1 and group['unlimited']==0 and group['online']==1

    with store.transaction() as db:
        db.execute("UPDATE restore_usage SET activity_at=? WHERE restore_id=?",(time.time()-120,item['id']))
    assert c.get('/api/dark-restore').json()['items'][0]['presence_state']=='idle'
    with store.transaction() as db:
        db.execute("UPDATE restore_usage SET activity_at=? WHERE restore_id=?",(time.time()-600,item['id']))
    assert c.get('/api/dark-restore').json()['items'][0]['presence_state']=='offline'


def test_group_counts_limited_unlimited_and_target_health(env,monkeypatch):
    store,engine,manager,auth,c,restore,inbound,item=prepare(env,monkeypatch)
    monkeypatch.setattr(restore,'_scan',lambda url:{**META,'total':0})
    gid=item['group_id']
    r=c.post('/api/dark-restore/import',json={
        'urls':['https://legacy.example/sub/unlimited'],'inboundIds':[inbound],
        'groupId':gid,'scan':True})
    assert r.status_code==200,r.text
    doc=c.get('/api/dark-restore').json();group=next(g for g in doc['groups'] if g['id']==gid)
    assert group['limited']==1 and group['unlimited']==1 and group['clients']==2
    assert {x['plan_type'] for x in doc['items']}=={'limited','unlimited'}
    # The group target state is surfaced separately from client presence.
    assert group['target_total']>=1 and group['target_ready']<=group['target_total']


def test_promotion_preserves_identity_remaining_quota_and_freezes_restore_usage(env,monkeypatch):
    store,engine,manager,auth,c,restore,inbound,item=prepare(env,monkeypatch)
    make_rep(manager,auth,inbound,volume=100000)
    before=engine.client_detail(item['core_email'])['client']
    with store.transaction() as db:
        db.execute('UPDATE core_clients SET up=2000,down=3000 WHERE email=?',(item['core_email'],))
    restore_before=c.get('/api/dark-restore').json()['items'][0]
    assert restore_before['dark_used']==5000 and restore_before['remaining']==35000

    result=c.post('/api/dark-restore/promote',json={'ids':[item['id']],'representativeId':'restore_rep'})
    assert result.status_code==200,result.text
    assert result.json()['promoted']==1 and result.json()['failed']==0

    with store.lock:
        native=store.db.execute("SELECT owner,quota_bytes,used_bytes FROM clients WHERE id=?",(item['core_email'],)).fetchone()
    assert tuple(native)==('restore_rep',40000,5000)
    after_core=engine.client_detail(item['core_email'])['client']
    assert after_core['id']==before['id']
    assert after_core['totalGB']==40000
    assert after_core['limitIp']==2 and after_core['limitHwid']==1

    history=c.get('/api/dark-restore').json()['items'][0]
    assert history['promoted'] is True and history['promoted_owner']=='restore_rep'
    assert history['dark_used']==5000
    with store.transaction() as db:
        db.execute('UPDATE core_clients SET up=2500,down=3500 WHERE email=?',(item['core_email'],))
    assert c.get('/api/dark-restore').json()['items'][0]['dark_used']==5000

    alias=c.get('/restore/sub/'+item['public_token']+'?format=raw',follow_redirects=False)
    assert alias.status_code==307
    assert '/sub/' in alias.headers['location'] and alias.headers['location'].endswith('?format=raw')
    assert c.put('/api/dark-restore/'+item['id']+'/mapping',json={'inboundIds':[inbound],'nodeIds':[]}).status_code==409
    assert c.delete('/api/dark-restore/'+item['id']).status_code==409

    monkeypatch.setattr(restore,'_scan',lambda url:(_ for _ in ()).throw(AssertionError('promoted URL must not rescan')))
    again=c.post('/api/dark-restore/import',json={
        'urls':['https://legacy.example/sub/one'],'inboundIds':[inbound],
        'groupId':item['group_id'],'scan':True})
    assert again.status_code==200
    assert again.json()['conflicts'][0]['reason']=='promoted_to_native'


def test_promotion_respects_representative_credit_and_leaves_failed_restore_intact(env,monkeypatch):
    store,engine,manager,auth,c,restore,inbound,item=prepare(env,monkeypatch)
    make_rep(manager,auth,inbound,volume=1000)
    before=engine.client_detail(item['core_email'])['client']
    result=c.post('/api/dark-restore/promote',json={'ids':[item['id']],'representativeId':'restore_rep'})
    assert result.status_code==200,result.text
    assert result.json()['promoted']==0 and result.json()['failed']==1
    assert 'credit' in result.json()['items'][0]['error'].lower()
    assert c.get('/api/dark-restore').json()['items'][0]['promoted'] is False
    assert engine.client_detail(item['core_email'])['client']==before
    with store.lock:
        assert store.db.execute("SELECT COUNT(*) FROM clients WHERE id=?",(item['core_email'],)).fetchone()[0]==0


def test_unlimited_promotion_consumes_unlimited_credit_not_volume(env,monkeypatch):
    store,engine,manager,auth,c,restore,inbound,item=prepare(env,monkeypatch,total=0)
    make_rep(manager,auth,inbound,volume=0,unlimited=2)
    result=c.post('/api/dark-restore/promote',json={'ids':[item['id']],'representativeId':'restore_rep'})
    assert result.status_code==200 and result.json()['promoted']==1
    with store.lock:
        native=store.db.execute("SELECT quota_bytes,owner FROM clients WHERE id=?",(item['core_email'],)).fetchone()
    assert tuple(native)==(0,'restore_rep')
    catalog=c.get('/api/dark-restore/representatives').json()
    rep=next(x for x in catalog if x['id']=='restore_rep')
    assert rep['remaining_unlimited']==1 and rep['remaining_volume_bytes']==0
