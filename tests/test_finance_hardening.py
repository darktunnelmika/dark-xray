from fastapi.testclient import TestClient

from server import make_app
from test_standalone import env,create


def test_resource_credit_adjustment_is_interactive_owner_only(env):
    store,engine,manager,auth,c=env
    assert c.put('/api/owners/seller',json={'name':'Seller','allowed':[],'volume_credit_bytes':0,'unlimited_credit':0}).status_code==200
    assert c.post('/api/admins',json={'username':'seller','password':'SellerPass88','role':'reseller'}).status_code==200
    with store.transaction() as db:
        db.execute("UPDATE api_admins SET permissions=? WHERE id='seller'",('{"finance.read":"own","finance.credit":"own","owners.read":"own"}',))
    token,p=auth.login('seller','SellerPass88','','127.0.0.2',3600,'finance-test')
    with TestClient(make_app(manager,auth,background=False),base_url=engine.config.public_origin) as other:
        other.cookies.set('dark_session',token);other.headers['X-Dark-CSRF']=p.csrf
        r=other.post('/api/resellers/seller/credits',json={
            'volume_bytes':50,'unlimited_units':1,'event_id':'seller-resource-0001'})
        assert r.status_code==403
    with store.lock:
        row=store.db.execute("SELECT volume_credit_bytes,unlimited_credit FROM owners WHERE id='seller'").fetchone()
        assert tuple(row)==(0,0)
        assert store.db.execute("SELECT COUNT(*) FROM resource_credit_ledger WHERE event_id='seller-resource-0001'").fetchone()[0]==0


def test_resource_credit_retry_is_idempotent_and_audited_once(env):
    store,_,_,_,c=env
    assert c.put('/api/owners/seller',json={'name':'Seller','allowed':[],'volume_credit_bytes':0,'unlimited_credit':0}).status_code==200
    assert c.post('/api/admins',json={'username':'seller','password':'SellerPass88','role':'reseller'}).status_code==200
    payload={'volume_bytes':125,'unlimited_units':3,'event_id':'owner-resource-0001'}
    first=c.post('/api/resellers/seller/credits',json=payload)
    second=c.post('/api/resellers/seller/credits',json=payload)
    assert first.status_code==200 and first.json()['recorded'] is True
    assert second.status_code==200 and second.json()['recorded'] is False
    with store.lock:
        owner=store.db.execute("SELECT volume_credit_bytes,unlimited_credit FROM owners WHERE id='seller'").fetchone()
        ledger=store.db.execute("SELECT COUNT(*) FROM resource_credit_ledger WHERE event_id='owner-resource-0001'").fetchone()[0]
        audits=store.db.execute("SELECT detail FROM live_audit WHERE action='representative.credit_adjust' AND owner='seller'").fetchall()
    assert tuple(owner)==(125,3)
    assert ledger==1
    assert len(audits)==1
    assert 'volume_bytes=125' in audits[0]['detail'] and 'unlimited_units=3' in audits[0]['detail']
    retired=c.post('/api/owners/seller/credit',json={'amount':1,'event_id':'legacy-money-credit'})
    assert retired.status_code==410


def test_owner_period_reset_preserves_lifetime_traffic_ledger(env):
    store,engine,manager,_,c=env
    create(c)
    with store.transaction() as db:
        db.execute("UPDATE core_clients SET up=40,down=2 WHERE email='dark-test'")
    manager.tick()
    before=store.owner_stats(env[3].current(c.cookies.get('dark_session'),None).actor,'dark')
    assert before['used_bytes']==42 and before['lifetime_used_bytes']==42
    with store.lock:
        rows_before=store.db.execute("SELECT COUNT(*) FROM traffic_ledger WHERE owner='dark'").fetchone()[0]
    r=c.post('/api/owners/dark/reset-period',json={})
    assert r.status_code==200 and r.json()['reset'] is True
    after=store.owner_stats(env[3].current(c.cookies.get('dark_session'),None).actor,'dark')
    assert after['used_bytes']==0
    assert after['lifetime_used_bytes']==42
    assert after['period']==before['period']+1
    with store.lock:
        rows_after=store.db.execute("SELECT COUNT(*) FROM traffic_ledger WHERE owner='dark'").fetchone()[0]
    assert rows_after==rows_before
