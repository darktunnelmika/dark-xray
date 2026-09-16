from fastapi.testclient import TestClient

from server import make_app
from test_standalone import env,create


def test_credit_is_interactive_owner_only_even_with_legacy_reseller_grant(env):
    store,engine,manager,auth,c=env
    assert c.put('/api/owners/seller',json={'name':'Seller','allowed':[]}).status_code==200
    assert c.post('/api/admins',json={'username':'seller','password':'SellerPass88','role':'reseller'}).status_code==200
    with store.transaction() as db:
        db.execute("UPDATE api_admins SET permissions=? WHERE id='seller'",('{"finance.credit":"own","owners.read":"own"}',))
    token,p=auth.login('seller','SellerPass88','','127.0.0.2',3600,'finance-test')
    with TestClient(make_app(manager,auth,background=False),base_url=engine.config.public_origin) as other:
        other.cookies.set('dark_session',token);other.headers['X-Dark-CSRF']=p.csrf
        r=other.post('/api/owners/seller/credit',json={'amount':50,'event_id':'seller-credit-0001'})
        assert r.status_code==403
    with store.lock:
        assert store.db.execute("SELECT credit FROM owners WHERE id='seller'").fetchone()['credit']==0
        assert store.db.execute("SELECT COUNT(*) FROM money_ledger WHERE event_id='seller-credit-0001'").fetchone()[0]==0


def test_credit_retry_is_idempotent_and_audited_once(env):
    store,_,_,_,c=env
    payload={'amount':125,'event_id':'owner-credit-0001'}
    first=c.post('/api/owners/dark/credit',json=payload)
    second=c.post('/api/owners/dark/credit',json=payload)
    assert first.status_code==200 and first.json()['recorded'] is True
    assert second.status_code==200 and second.json()['recorded'] is False
    with store.lock:
        owner=store.db.execute("SELECT credit FROM owners WHERE id='dark'").fetchone()
        ledger=store.db.execute("SELECT COUNT(*) FROM money_ledger WHERE event_id='owner-credit-0001'").fetchone()[0]
        audits=store.db.execute("SELECT detail FROM live_audit WHERE action='finance.credit' AND owner='dark'").fetchall()
    assert owner['credit']==125
    assert ledger==1
    assert len(audits)==1
    assert 'amount=125' in audits[0]['detail'] and 'owner-credit-0001' in audits[0]['detail']


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
