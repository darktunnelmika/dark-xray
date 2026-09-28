import json
import time
from types import SimpleNamespace
import pytest
from fastapi.testclient import TestClient
from dark_restore import DarkRestore
from restore_scan import parse_userinfo, scan_subscription, MAX_BYTES
from test_restore_groups import env, prepare, add, row, counters, META, OWNER


@pytest.mark.parametrize('header,status', [
    ('', 'no_usage_data'),
    ('total=0; expire=0', 'partial'),
    ('upload=0;download=0;total=0;expire=0', 'verified'),
    ('upload=3;download=7;total=5;expire=0', 'verified'),
    ('upload=-1;download=0;total=0;expire=0', 'invalid_metadata'),
    ('upload=0.5;download=0;total=0;expire=0', 'invalid_metadata'),
    ('upload=0;UPLOAD=1;download=0;total=0;expire=0', 'invalid_metadata'),
    ('upload=0;download=0;total=0;expire=1790560000000', 'invalid_metadata'),
    ('upload='+str(MAX_BYTES)+';download=1;total=0;expire=0', 'invalid_metadata'),
    ('upload=0;download=0;total='+str(MAX_BYTES+1)+';expire=0', 'invalid_metadata'),
])
def test_parser_distinguishes_missing_zero_unlimited_and_invalid(header, status):
    out = parse_userinfo(header)
    assert out['status'] == status
    if status == 'verified':
        assert out['provided_fields'] == ['download','expire','total','upload']


def test_partial_import_is_quarantined_before_build_or_node_publication(env, monkeypatch):
    store, engine, c, restore, inbound = prepare(env, monkeypatch)
    monkeypatch.setattr(restore, '_scan', lambda url: parse_userinfo('total=0;expire=0'))
    add(c, inbound); item = row(c)
    assert item['service_status'] == 'needs_review'
    assert engine.client_detail(item['core_email'])['client']['enable'] is False
    assert not engine.build_config()['inbounds'][0]['settings']['clients']
    assert not engine.clients()[0]['enable']
    assert c.get('/restore/sub/' + item['public_token']).status_code == 403
    assert c.head('/sub/one', headers={'host': 'legacy.example'}).status_code == 403
    assert row(c)['first_seen'] == 0
    assert store.db.execute('SELECT COUNT(*) FROM clients').fetchone()[0] == 0


def test_explicit_zero_complete_metadata_is_unlimited_not_quarantined(env, monkeypatch):
    _, engine, c, restore, inbound = prepare(env, monkeypatch)
    monkeypatch.setattr(restore, '_scan', lambda url: parse_userinfo('upload=0;download=0;total=0;expire=0'))
    add(c, inbound); item = row(c)
    assert item['service_status'] == 'eligible' and item['unlimited_volume']
    assert c.get('/restore/sub/' + item['public_token']).status_code == 200
    assert engine.client_detail(item['core_email'])['client']['enable'] is True


def test_quota_uses_legacy_plus_dark_and_disables_without_new_sub_request(env, monkeypatch):
    store, engine, c, restore, inbound = prepare(env, monkeypatch)
    add(c, inbound); item = row(c); original = engine.client_detail(item['core_email'])['client'].copy()
    counters(store, item['core_email'], 20000, 20000)
    assert item['first_seen'] == 0
    # Manager/node reconciliation calls clients/apply independently of subscription GET.
    engine.clients()
    body = engine.client_detail(item['core_email'])['client']
    assert body['enable'] is False
    assert {k:v for k,v in body.items() if k!='enable'} == {k:v for k,v in original.items() if k!='enable'}
    assert row(c)['service_status'] == 'exhausted' and row(c)['dark_used'] == 40000
    assert row(c)['first_seen'] == 0
    assert c.get('/restore/sub/' + item['public_token']).status_code == 403
    assert not engine.build_config()['inbounds'][0]['settings']['clients']


def test_expiry_disables_core_flags_not_only_subscription(env, monkeypatch):
    store, engine, c, restore, inbound = prepare(env, monkeypatch)
    add(c, inbound); item = row(c)
    with store.transaction() as db:
        db.execute('UPDATE restore_subscriptions SET legacy_expire=? WHERE id=?',(int(time.time())-1,item['id']))
    engine.clients()
    assert row(c)['service_status'] == 'expired'
    assert not engine.client_detail(item['core_email'])['client']['enable']
    assert row(c)['first_seen'] == 0


def test_pending_import_is_not_treated_as_an_unlimited_user(env, monkeypatch):
    _, engine, c, _, inbound = prepare(env, monkeypatch)
    add(c, inbound, scan=False); item=row(c)
    assert item['service_status']=='needs_review' and not item['unlimited_volume']
    assert not engine.client_detail(item['core_email'])['client']['enable']


def test_reconciliation_keeps_native_policy_and_ledger_unchanged(env, monkeypatch):
    store, engine, c, restore, inbound = prepare(env, monkeypatch)
    assert c.post('/api/clients',json={'owner':'dark','client':{'email':'native-safe'},'inboundIds':[inbound]}).status_code==202
    add(c,inbound); item=row(c)
    native=engine.client_detail('native-safe'); native_rows=[tuple(r) for r in store.db.execute('SELECT * FROM clients')]
    counters(store,item['core_email'],40000,0)
    for _ in range(3): engine.clients()
    assert engine.client_detail('native-safe')==native
    assert [tuple(r) for r in store.db.execute('SELECT * FROM clients')]==native_rows
    assert engine.client_detail(item['core_email'])['client']['enable'] is False
    count=store.db.execute("SELECT COUNT(*) FROM restore_events WHERE event='eligibility.changed'").fetchone()[0]
    engine.clients()
    assert store.db.execute("SELECT COUNT(*) FROM restore_events WHERE event='eligibility.changed'").fetchone()[0]==count


def test_reinitialization_does_not_stack_adapters_or_forget_usage(env,monkeypatch):
    store,engine,c,restore,inbound=prepare(env,monkeypatch)
    add(c,inbound); item=row(c); adapter=engine.clients; original=engine._dark_restore_original_clients
    counters(store,item['core_email'],40000,0); engine.clients()
    second=DarkRestore(store,engine,c.app.state.nodes)
    assert engine.clients is adapter and engine._dark_restore_original_clients is original
    engine.clients()
    assert not engine.client_detail(item['core_email'])['client']['enable']
    assert second.rows()[0]['dark_used']==40000


def review_value(item,**extra):
    return {k:item['legacy_'+k] for k in ('upload','download','total','expire')} | {
        'note':'Verified against the source export', 'confirmed':True,
        'expectedRevision':item['metadata_revision'], **extra}


def test_manual_review_requires_confirmation_revision_and_preserves_identity_usage(env,monkeypatch):
    store,engine,c,restore,inbound=prepare(env,monkeypatch)
    add(c,inbound,scan=False); item=row(c); original=engine.client_detail(item['core_email'])['client']
    counters(store,item['core_email'],11,22)
    url='/api/dark-restore/'+item['id']+'/metadata'
    value=review_value(item,total=1000,expire=2000000000)
    assert c.put(url,json={**value,'confirmed':False}).status_code==400
    assert c.put(url,json={**value,'total':True}).status_code==422
    assert c.put(url,json=value).status_code==200
    fresh=row(c)
    assert fresh['metadata_state']=='manual' and fresh['dark_used']==33
    assert fresh['service_status']=='eligible'
    body=engine.client_detail(item['core_email'])['client']
    for key in ('id','password','email'):assert body[key]==original[key]
    assert c.put(url,json=value).status_code==409
    assert store.db.execute("SELECT COUNT(*) FROM restore_events WHERE event='metadata.reviewed'").fetchone()[0]==1


def test_suspension_and_resume_never_reset_quota_or_bypass_expiry(env,monkeypatch):
    store,engine,c,_,inbound=prepare(env,monkeypatch)
    add(c,inbound); item=row(c); url='/api/dark-restore/'+item['id']+'/suspension'
    out=c.put(url,json={'suspended':True,'expectedRevision':item['metadata_revision']})
    assert out.status_code==200 and row(c)['service_status']=='suspended'
    assert c.get('/sub/one',headers={'host':'legacy.example'}).status_code==403
    counters(store,item['core_email'],40000,0)
    out=c.put(url,json={'suspended':False,'expectedRevision':row(c)['metadata_revision']})
    assert out.status_code==200 and row(c)['service_status']=='exhausted'
    assert not engine.client_detail(item['core_email'])['client']['enable']
    assert row(c)['dark_used']==40000


def test_external_disable_is_never_automatically_undone(env,monkeypatch):
    _,engine,c,_,inbound=prepare(env,monkeypatch)
    add(c,inbound); item=row(c)
    engine.update(item['core_email'],{'enable':False}); engine.clients()
    assert row(c)['service_status']=='suspended'
    engine.clients(); assert not engine.client_detail(item['core_email'])['client']['enable']


def test_complete_rescan_can_release_only_a_pending_import(env,monkeypatch):
    _,engine,c,restore,inbound=prepare(env,monkeypatch)
    first=add(c,inbound,scan=False); item=row(c)
    again=add(c,inbound,group='',groupId=first['group']['id'])
    assert again['updated']==1 and row(c)['service_status']=='eligible'
    assert engine.client_detail(item['core_email'])['client']['enable']


def test_old_saved_verified_metadata_is_labelled_not_invented_as_new_scan(env,monkeypatch):
    store,engine,c,restore,inbound=prepare(env,monkeypatch)
    add(c,inbound); item=row(c)
    with store.transaction() as db:db.execute('DELETE FROM restore_safety')
    restored=DarkRestore(store,engine,c.app.state.nodes)
    assert restored.rows()[0]['metadata_state']=='legacy_saved'
    assert restored.rows()[0]['service_status']=='eligible'


def test_metadata_endpoint_is_owner_only_and_csrf_protected(env,monkeypatch):
    store,engine,c,restore,inbound=prepare(env,monkeypatch); add(c,inbound); item=row(c)
    manager,auth=env[2:4]
    manager.owner_put(OWNER,'rep',name='Rep',allowed=[inbound]); auth.admin_create(OWNER,'rep','RepPassword12345','reseller',{})
    with TestClient(c.app,base_url=engine.config.public_origin) as other:
        assert other.get('/api/dark-restore/safety').status_code==401
        login=other.post('/api/auth/login',json={'username':'rep','password':'RepPassword12345'})
        other.headers['X-Dark-CSRF']=login.json()['csrf']
        assert other.get('/api/dark-restore/safety').status_code==403
        assert other.put('/api/dark-restore/'+item['id']+'/metadata',json=review_value(item)).status_code==403
    csrf=c.headers.pop('X-Dark-CSRF')
    assert c.put('/api/dark-restore/'+item['id']+'/metadata',json=review_value(item)).status_code==403
    c.headers['X-Dark-CSRF']=csrf


def test_scan_pins_validated_address_and_never_fetches_source_body(env,monkeypatch):
    _,_,_,restore,_=prepare(env,monkeypatch); captured={}
    monkeypatch.setattr(restore,'_public_addresses',lambda host:['93.184.216.34'])
    class Response:
        status=200
        def getheader(self,key,default=''):return 'upload=0;download=0;total=0;expire=0'
        def read(self,*a):raise AssertionError('Body must not be read')
    class Connection:
        def __init__(self,host,port,address,**kw):captured.update(host=host,port=port,address=address,context=kw['context'])
        def request(self,method,target,headers):captured.update(method=method,target=target)
        def getresponse(self):return Response()
        def close(self):pass
    monkeypatch.setattr('restore_scan._PinnedHTTPS',Connection)
    out=scan_subscription(restore,'https://legacy.example:2096/sub/private-token')
    assert out['status']=='verified'
    assert (captured['host'],captured['port'],captured['address'])==('legacy.example',2096,'93.184.216.34')
    assert captured['context'].check_hostname and captured['context'].verify_mode==2


def test_scan_refuses_own_hub_after_dns_cutover(env,monkeypatch):
    _,engine,_,restore,_=prepare(env,monkeypatch)
    engine.config.public_address='93.184.216.34'
    monkeypatch.setattr(restore,'_public_addresses',lambda host:['93.184.216.34'])
    def no_connection(*a,**k):raise AssertionError('Must not query own subscription as legacy source')
    monkeypatch.setattr('restore_scan._PinnedHTTPS',no_connection)
    out=scan_subscription(restore,'https://legacy.example/sub/private-token')
    assert out['status']=='source_is_dark'
    assert 'private-token' not in json.dumps(out)
