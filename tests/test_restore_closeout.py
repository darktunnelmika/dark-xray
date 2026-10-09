import copy
import json
import time

from test_standalone import env,IB,OWNER


META={'status':'verified','error':'','upload':3000,'download':7000,'total':50000,'expire':2000000000}


def prepare(env,monkeypatch,total=50000,suffix='one',expire=None):
    store,engine,manager,auth,c=env
    inbound=c.post('/api/inbounds',json=copy.deepcopy(IB)).json()['id']
    restore=c.app.state.dark_restore
    monkeypatch.setattr(restore,'_scan',lambda url:{**META,'total':total,'expire':META['expire'] if expire is None else expire})
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
    native_id=result.json()['items'][0]['result']['client_id']
    assert native_id.startswith('rr_')

    with store.lock:
        native=store.db.execute("SELECT owner,quota_bytes,used_bytes FROM clients WHERE id=?",(native_id,)).fetchone()
    assert tuple(native)==('restore_rep',40000,5000)
    after_core=engine.client_detail(native_id)['client']
    assert after_core['id']==before['id']
    assert after_core['totalGB']==40000
    assert after_core['limitIp']==2 and after_core['limitHwid']==1

    history=c.get('/api/dark-restore').json()['items'][0]
    assert history['promoted'] is True and history['promoted_owner']=='restore_rep'
    assert history['dark_used']==5000
    with store.transaction() as db:
        db.execute('UPDATE core_clients SET up=2500,down=3500 WHERE email=?',(native_id,))
    assert c.get('/api/dark-restore').json()['items'][0]['dark_used']==5000

    alias=c.get('/restore/sub/'+item['public_token']+'?format=raw',follow_redirects=False)
    assert alias.status_code==307
    assert '/sub/' in alias.headers['location'] and alias.headers['location'].endswith('?format=raw')
    # The representative profile remains editable after migration even with a client prefix.
    manager.owner_put(OWNER,'restore_rep',name='Restore Rep Updated',allowed=[inbound],
                      volume_credit_bytes=100000,unlimited_credit=5,max_clients=50,
                      prefix='rr_',max_client_ips=2,max_client_hwid=1)
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
    store,engine,manager,auth,c,restore,inbound,item=prepare(env,monkeypatch,total=0,expire=int(time.time())+30*86400)
    make_rep(manager,auth,inbound,volume=0,unlimited=2)
    result=c.post('/api/dark-restore/promote',json={'ids':[item['id']],'representativeId':'restore_rep'})
    assert result.status_code==200 and result.json()['promoted']==1
    native_id=result.json()['items'][0]['result']['client_id']
    assert native_id.startswith('rr_')
    with store.lock:
        native=store.db.execute("SELECT quota_bytes,owner FROM clients WHERE id=?",(native_id,)).fetchone()
    assert tuple(native)==(0,'restore_rep')
    catalog=c.get('/api/dark-restore/representatives').json()
    rep=next(x for x in catalog if x['id']=='restore_rep')
    assert rep['remaining_unlimited']==0 and rep['remaining_volume_bytes']==0


def test_promotion_baselines_complete_hub_and_node_dark_usage(env,monkeypatch):
    store,engine,manager,auth,c,restore,inbound,item=prepare(env,monkeypatch)
    make_rep(manager,auth,inbound,volume=100000)
    # 5 KB local DARK usage plus 4 KB already observed on a remote Node.
    with store.transaction() as db:
        db.execute('UPDATE core_clients SET up=2000,down=3000 WHERE email=?',(item['core_email'],))
        db.execute("""INSERT INTO restore_usage(restore_id,scope,up,down,raw_up,raw_down,updated_at,activity_at)
          VALUES(?,?,?,?,?,?,?,?)""",(item['id'],'node:synthetic',1500,2500,1500,2500,time.time(),time.time()))
    before=c.get('/api/dark-restore').json()['items'][0]
    assert before['local_used']==5000 and before['node_used']==4000 and before['dark_used']==9000
    result=c.post('/api/dark-restore/promote',json={'ids':[item['id']],'representativeId':'restore_rep'})
    assert result.status_code==200,result.text
    assert result.json()['promoted']==1
    native_id=result.json()['items'][0]['result']['client_id']
    with store.lock:
        native=store.db.execute("SELECT quota_bytes,used_bytes FROM clients WHERE id=?",(native_id,)).fetchone()
    # Native quota remains the post-legacy total, while used baseline includes
    # every DARK byte already consumed on Hub and Nodes.
    assert tuple(native)==(40000,9000)


def test_finished_restore_archive_preserves_group_usage_and_active_users(env,monkeypatch):
    expired=int(time.time())-60
    store,engine,manager,auth,c,restore,inbound,item=prepare(env,monkeypatch,expire=expired,suffix='expired')
    gid=item['group_id']
    # Keep a second, still-active client in the same group. Bulk cleanup must
    # never touch it.
    future=int(time.time())+30*86400
    monkeypatch.setattr(restore,'_scan',lambda url:{**META,'expire':future})
    active=c.post('/api/dark-restore/import',json={
        'urls':['https://legacy.example/sub/active'],'inboundIds':[inbound],
        'groupId':gid,'scan':True})
    assert active.status_code==200,active.text
    active_id=next(x['id'] for x in c.get('/api/dark-restore').json()['items'] if x['id']!=item['id'])

    # DARK traffic is immutable group history, not a property of the live row.
    with store.transaction() as db:
        db.execute('UPDATE core_clients SET up=1200,down=800 WHERE email=?',(item['core_email'],))
    before=c.get('/api/dark-restore').json()
    before_group=next(g for g in before['groups'] if g['id']==gid)
    assert before_group['dark_used']==2000 and before_group['clients']==2

    out=c.post('/api/dark-restore/archive-finished',json={'groupId':gid})
    assert out.status_code==200,out.text
    assert out.json()['archived']==1 and out.json()['failed']==0

    after=c.get('/api/dark-restore').json()
    assert {x['id'] for x in after['items']}=={active_id}
    group=next(g for g in after['groups'] if g['id']==gid)
    assert group['clients']==1
    assert group['archived']==1
    assert group['historical_clients']==2
    assert group['dark_used']==before_group['dark_used']==2000

    with store.lock:
        archived=store.db.execute(
            'SELECT archived_at,archived_reason FROM restore_subscriptions WHERE id=?',(item['id'],)).fetchone()
        usage=store.db.execute(
            'SELECT COALESCE(SUM(up+down),0) FROM restore_usage WHERE restore_id=?',(item['id'],)).fetchone()[0]
        active_core=store.db.execute(
            'SELECT 1 FROM core_clients WHERE email=(SELECT core_email FROM restore_subscriptions WHERE id=?)',(active_id,)).fetchone()
    assert archived['archived_at']>0 and archived['archived_reason']=='expired'
    assert usage==2000
    assert active_core is not None

    # Archived subscriptions are history-only and cannot be recreated by
    # rescanning the old URL.
    assert c.get('/restore/sub/'+item['public_token']+'?format=raw').status_code==410
    monkeypatch.setattr(restore,'_scan',lambda url:(_ for _ in ()).throw(AssertionError('archive must not rescan')))
    again=c.post('/api/dark-restore/import',json={
        'urls':['https://legacy.example/sub/expired'],'inboundIds':[inbound],
        'groupId':gid,'scan':True})
    assert again.status_code==200,again.text
    assert again.json()['conflicts'][0]['reason']=='archived_history'


def test_single_restore_delete_is_soft_archive_and_keeps_traffic(env,monkeypatch):
    store,engine,manager,auth,c,restore,inbound,item=prepare(env,monkeypatch,suffix='manual-delete')
    with store.transaction() as db:
        db.execute('UPDATE core_clients SET up=333,down=667 WHERE email=?',(item['core_email'],))
    before=c.get('/api/dark-restore').json()
    gid=item['group_id'];used=next(g for g in before['groups'] if g['id']==gid)['dark_used']
    out=c.delete('/api/dark-restore/'+item['id'])
    assert out.status_code==200,out.text
    assert out.json()['archived'] is True and out.json()['deleted'] is True

    after=c.get('/api/dark-restore').json()
    assert all(x['id']!=item['id'] for x in after['items'])
    group=next(g for g in after['groups'] if g['id']==gid)
    assert group['dark_used']==used==1000 and group['archived']==1
    with store.lock:
        assert store.db.execute('SELECT COUNT(*) FROM restore_subscriptions WHERE id=?',(item['id'],)).fetchone()[0]==1
        assert store.db.execute('SELECT COALESCE(SUM(up+down),0) FROM restore_usage WHERE restore_id=?',(item['id'],)).fetchone()[0]==1000
