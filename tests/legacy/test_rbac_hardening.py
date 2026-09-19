import json

from fastapi.testclient import TestClient

from dark_policy import Actor,Store
from policy_auth import bootstrap,create_admin
from policy_routes import create_app

PW='legacy-rbac-owner-pass-88'


def _login(client,username,password=PW):
    r=client.post('/v1/auth/login',json={'username':username,'password':password})
    assert r.status_code==200,r.text
    return {'Authorization':'Bearer '+r.json()['access_token']}


def test_legacy_reseller_cannot_self_adjust_resource_credit_even_with_stale_grant(tmp_path):
    store=Store(tmp_path/'policy.db');owner=Actor('root','owner',{})
    bootstrap(store,'root',PW);store.register_owner(owner,'seller')
    create_admin(store,owner,'seller',PW,'reseller')
    with store.transaction() as db:
        db.execute('UPDATE api_admins SET permissions=? WHERE id=?',
                   (json.dumps({'finance.read':'own','finance.credit':'own'}),'seller'))
    with TestClient(create_app(store)) as c:
        h=_login(c,'seller')
        me=c.get('/v1/me',headers=h);assert me.status_code==200
        assert 'finance.credit' not in me.json()['permissions']
        r=c.post('/v1/owners/seller/credits',headers=h,json={'volume_bytes':5,'unlimited_units':1,'event_id':'legacy-self-credit-0001'})
        assert r.status_code==403
    store.close()


def test_legacy_readonly_stale_write_grants_are_ignored(tmp_path):
    store=Store(tmp_path/'policy.db');owner=Actor('root','owner',{})
    bootstrap(store,'root',PW);store.register_owner(owner,'seller')
    create_admin(store,owner,'read',PW,'readonly')
    store.register_client(owner,'client-a','seller')
    with store.transaction() as db:
        db.execute('UPDATE api_admins SET permissions=? WHERE id=?',
                   (json.dumps({'clients.read':'all','clients.edit':'all','clients.delete':'all'}),'read'))
    with TestClient(create_app(store)) as c:
        h=_login(c,'read')
        me=c.get('/v1/me',headers=h);assert me.status_code==200
        assert me.json()['permissions']=={'clients.read':'all'}
        assert c.patch('/v1/clients/client-a',headers=h,json={'manual':True}).status_code==403
        assert c.delete('/v1/clients/client-a',headers=h).status_code==403
    store.close()


def test_legacy_owner_can_adjust_resource_credits(tmp_path):
    store=Store(tmp_path/'policy.db');owner=Actor('root','owner',{})
    bootstrap(store,'root',PW);store.register_owner(owner,'seller',volume_credit_bytes=0,unlimited_credit=0)
    create_admin(store,owner,'seller',PW,'reseller')
    with TestClient(create_app(store)) as c:
        h=_login(c,'root')
        r=c.post('/v1/owners/seller/credits',headers=h,json={
            'volume_bytes':100,'unlimited_units':1,'event_id':'owner-resource-credit-0001'})
        assert r.status_code==200 and r.json()['recorded'] is True
        r=c.post('/v1/clients',headers=h,json={'id':'limited-client','owner':'seller','quota_bytes':25})
        assert r.status_code==201
        r=c.post('/v1/clients',headers=h,json={'id':'unlimited-client','owner':'seller','quota_bytes':0})
        assert r.status_code==201
        assert c.post('/v1/owners/seller/credit',headers=h,json={'amount':1,'event_id':'old-money-credit'}).status_code==404
        assert c.post('/v1/refunds',headers=h,json={'order_id':'sale-1','event_id':'refund-1'}).status_code==404
    store.close()
