import json
import hashlib
import time
import pytest
from fastapi.testclient import TestClient
from dark_policy import Actor,Store,PermissionDenied,PolicyError
from policy_api import bootstrap,create_app,create_admin,password_hash,verify_password

PW='test-only-strong-password-42'
@pytest.fixture
def env(tmp_path):
    store=Store(tmp_path/'api.db');bootstrap(store,'root',PW)
    owner=Actor('root','owner');store.register_owner(owner,'arda',volume_credit_bytes=1_000_000,unlimited_credit=10);store.register_owner(owner,'dark')
    create_admin(store,owner,'arda',PW);create_admin(store,owner,'read',PW,'readonly')
    app=create_app(store)
    with TestClient(app) as client:
        def auth(username='root'):
            response=client.post('/v1/auth/login',json={'username':username,'password':PW})
            assert response.status_code==200,response.text
            return {'Authorization':'Bearer '+response.json()['access_token']}
        yield store,client,auth
    store.close()

def test_login_no_defaults_or_plaintext(env):
    s,c,auth=env;h=auth();r=c.get('/v1/me',headers=h);assert r.status_code==200 and r.json()['role']=='owner'
    assert s.db.execute('select password_hash from api_admins where id="root"').fetchone()[0]!=PW
    token=h['Authorization'][7:];assert s.db.execute('select digest from sessions').fetchone()[0]!=token
    assert 'no-store' in r.headers['cache-control']

def test_anonymous_cannot_read(env):
    _,c,_=env
    assert c.get('/v1/clients').status_code==401
    assert c.get('/v1/owners').status_code==401

def test_scoped_client_crud(env):
    s,c,auth=env;root=auth();arda=auth('arda');ro=auth('read')
    assert c.post('/v1/clients',headers=arda,json={'id':'a','owner':'arda','limit_ip':1}).status_code==201
    assert c.post('/v1/clients',headers=root,json={'id':'b','owner':'dark'}).status_code==201
    assert [x['id'] for x in c.get('/v1/clients',headers=arda).json()]==['a']
    assert c.patch('/v1/clients/b',headers=arda,json={'manual':True}).status_code==403
    assert c.delete('/v1/clients/a',headers=ro).status_code==403
    assert c.post('/v1/clients',headers=arda,json={'id':'c','owner':'dark'}).status_code==403

def test_no_owner_escalation_or_unknown_fields(env):
    s,c,auth=env;h=auth('arda')
    assert c.post('/v1/clients',headers=h,json={'id':'a','owner':'arda','role':'owner'}).status_code==422
    assert c.put('/v1/owners/arda',headers=h,json={'volume_credit_bytes':100,'unlimited_credit':1}).status_code==403
    assert c.post('/v1/admins',headers=h,json={'username':'evil','password':PW,'role':'owner'}).status_code==403

def test_customer_price_fields_are_not_part_of_policy_api(env):
    _,c,auth=env;h=auth('arda')
    assert c.post('/v1/clients',headers=h,json={'id':'a','owner':'arda','price':1,'order_id':'one'}).status_code==422

def test_usage_not_writeable_by_reseller(env):
    s,c,auth=env;root=auth();h=auth('arda')
    c.post('/v1/clients',headers=h,json={'id':'a','owner':'arda'})
    event={'event_id':'e1','client_id':'a','up_bytes':13,'down_bytes':10}
    assert c.post('/v1/usage',headers=h,json=event).status_code==403
    assert c.post('/v1/usage',headers=root,json=event).json()['recorded'] is True
    assert c.post('/v1/usage',headers=root,json=event).json()['recorded'] is False
    assert c.post('/v1/clients/a/reset-usage',headers=h).status_code==200
    assert c.delete('/v1/clients/a',headers=h).status_code==200
    rows=c.get('/v1/owners',headers=h).json();assert len(rows)==1 and rows[0]['used_bytes']==23

def test_unknown_role_permission_rejected(env):
    _,c,auth=env;root=auth()
    r=c.post('/v1/admins',headers=root,json={'username':'r2','password':PW,'role':'readonly','permissions':{'madeup':'all'}})
    assert r.status_code==400

def test_session_revocation_logout(env):
    _,c,auth=env;h=auth()
    assert c.post('/v1/auth/logout',headers=h).status_code==200
    assert c.get('/v1/me',headers=h).status_code==401

def test_disable_and_password_reset_revoke(env):
    _,c,auth=env;h=auth('arda');root=auth()
    r=c.patch('/v1/admins/arda',headers=root,json={'disabled':True});assert r.status_code==200
    assert c.get('/v1/me',headers=h).status_code==401
    c.patch('/v1/admins/arda',headers=root,json={'disabled':False})
    h=auth('arda');c.patch('/v1/admins/arda',headers=root,json={'password':'different-strong-password'})
    assert c.get('/v1/me',headers=h).status_code==401
    assert c.post('/v1/auth/login',json={'username':'arda','password':PW}).status_code==401

def test_last_owner_not_disabled(env):
    _,c,auth=env;assert c.patch('/v1/admins/root',headers=auth(),json={'disabled':True}).status_code==400

def test_permission_change_effective_without_old_session(env):
    _,c,auth=env;h=auth('arda');root=auth()
    assert c.patch('/v1/admins/arda',headers=root,json={'permissions':{'clients.read':'own'}}).status_code==200
    assert c.get('/v1/me',headers=h).status_code==401
    h=auth('arda');assert c.post('/v1/clients',headers=h,json={'id':'a','owner':'arda'}).status_code==403

def test_rate_limit_forwarded_ip_cannot_bypass(env):
    _,c,_=env
    for n in range(5):assert c.post('/v1/auth/login',json={'username':'nobody','password':'bad'},headers={'X-Forwarded-For':str(n)}).status_code==401
    assert c.post('/v1/auth/login',json={'username':'root','password':PW}).status_code==429

def test_cross_origin_forbidden(env):
    _,c,auth=env;h=auth()|{'Origin':'https://untrusted.invalid'}
    assert c.post('/v1/clients',headers=h,json={'id':'a','owner':'arda'}).status_code==403

def test_size_and_type_validation(env):
    _,c,auth=env;h=auth()
    assert c.post('/v1/clients',headers=h,json={'id':'a','owner':'arda','limit_ip':True}).status_code==422
    assert c.post('/v1/clients',headers=h,json={'id':'a','owner':'arda','limit_ip':-1}).status_code==422
    assert c.post('/v1/clients',headers=h,json={'id':'a'*70000}).status_code==413

def test_real_metrics_not_sample_and_scoped(env):
    _,c,auth=env;r=c.get('/v1/system',headers=auth())
    assert r.status_code==200 and r.json()['sample'] is False and r.json()['memory']['total']>0
    assert c.get('/v1/system',headers=auth('arda')).status_code==403

def test_health_explicit_not_full_backend(env):
    _,c,_=env;cap=c.get('/health').json()['capabilities']
    assert cap['xray'] is False and cap['production_ready'] is False

def test_resource_credit_api_idempotency(env):
    _,c,auth=env;h=auth()
    data={'volume_bytes':300000,'unlimited_units':2,'event_id':'resource-credit-0001'}
    assert c.post('/v1/owners/arda/credits',headers=h,json=data).json()['recorded']
    assert not c.post('/v1/owners/arda/credits',headers=h,json=data).json()['recorded']
    assert c.post('/v1/clients',headers=h,json={'id':'a','owner':'arda','quota_bytes':100000}).status_code==201
    rows=c.get('/v1/ledger/credits',headers=auth('arda')).json()
    assert len(rows)==1 and rows[0]['owner']=='arda' and rows[0]['volume_bytes']==300000
    assert c.get('/v1/ledger/money',headers=h).status_code==422


def test_bootstrap_refuses_overwrite(env):
    s,_,_=env
    with pytest.raises(PolicyError):bootstrap(s,'someone',PW)

def test_password_hash_correct():
    h=password_hash(PW);assert verify_password(PW,h) and not verify_password('wrong',h)
    with pytest.raises(PolicyError):password_hash('short')
    assert not verify_password(PW,'broken')

def test_disabled_account_blocks_policy_without_clearing_manual(env):
    s,c,auth=env;root=auth();c.post('/v1/clients',headers=root,json={'id':'a','owner':'arda'})
    c.patch('/v1/clients/a',headers=root,json={'manual':True})
    c.patch('/v1/admins/arda',headers=root,json={'disabled':True})
    assert set(s.client_reasons('a'))=={'client_manual','owner_account_disabled'}
    c.patch('/v1/admins/arda',headers=root,json={'disabled':False})
    assert s.client_reasons('a')==['client_manual']
