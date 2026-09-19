import json

import pytest
from fastapi.testclient import TestClient

from auth import Auth,DEFAULTS
from core import Config,CoreEngine
from dark_policy import Actor,Store
from manager import Manager
from server import make_app


OWNER=Actor('dark','owner',{})


def inbound_payload(port=25101):
    return {
        'remark':'REP V2','listen':'127.0.0.1','port':port,'protocol':'vless',
        'enable':True,'tag':'rep-v2','settings':{'decryption':'none'},
        'streamSettings':{'network':'tcp','security':'none'},'sniffing':{}
    }


@pytest.fixture
def env(tmp_path):
    store=Store(tmp_path/'dark.sqlite3')
    config=Config(xray_binary=str(tmp_path/'missing-xray'),xray_assets=str(tmp_path),
                  public_address='vpn.example.test',test_engine=True)
    engine=CoreEngine(config,store,tmp_path/'runtime')
    manager=Manager(store,engine)
    auth=Auth(store,tmp_path/'secret.key')
    auth.bootstrap('dark','OwnerPass88')
    manager.owner_put(OWNER,'dark',name='DARK OWNER',allowed=[])
    app=make_app(manager,auth,background=False)
    with TestClient(app,base_url=config.public_origin) as c:
        login=c.post('/api/auth/login',json={'username':'dark','password':'OwnerPass88','otp':''})
        assert login.status_code==200,login.text
        c.headers['X-Dark-CSRF']=login.json()['csrf']
        yield store,engine,manager,auth,c
    manager.close();engine.close();store.close()


def create_inbound(c):
    r=c.post('/api/inbounds',json=inbound_payload())
    assert r.status_code==200,r.text
    return r.json()['id']


def rep_body(inbound_id,**overrides):
    body={
        'name':'Seller One','password':'SellerPass88','enabled':True,
        'allowed':[inbound_id],'volume_credit_bytes':10_000_000,'unlimited_credit':25,'max_clients':25,
        'prefix':'s_','max_client_ips':2,'max_client_hwid':1,
    }
    body.update(overrides)
    return body


def test_representative_create_unifies_login_profile_and_fixed_scope(env):
    store,_,_,auth,c=env
    inbound_id=create_inbound(c)
    r=c.put('/api/resellers/seller',json=rep_body(inbound_id))
    assert r.status_code==200,r.text
    doc=r.json()
    assert doc['id']=='seller' and doc['login_ready'] is True and doc['enabled'] is True
    assert doc['allowed']==[inbound_id]
    assert doc['prefix']=='s_' and doc['max_client_ips']==2 and doc['max_client_hwid']==1
    assert doc['volume_credit_bytes']==10_000_000 and doc['volume_credit_remaining_bytes']==10_000_000
    assert doc['unlimited_credit']==25 and doc['unlimited_credit_remaining']==25
    with store.lock:
        admin=store.db.execute("SELECT role,permissions,disabled FROM api_admins WHERE id='seller'").fetchone()
        profile=store.db.execute("SELECT prefix,max_client_ips,max_client_hwid FROM owner_profiles WHERE id='seller'").fetchone()
    assert admin['role']=='reseller' and admin['disabled']==0
    assert json.loads(admin['permissions'])==DEFAULTS['reseller']
    assert tuple(profile)==('s_',2,1)
    rows=c.get('/api/resellers')
    assert rows.status_code==200
    assert [x['id'] for x in rows.json()]==['seller']
    assert 'dark' not in {x['id'] for x in rows.json()}
    token,p=auth.login('seller','SellerPass88','','127.0.0.2')
    assert p.actor.role=='reseller'
    assert p.actor.permissions==DEFAULTS['reseller']
    assert token


def test_public_admin_api_cannot_create_second_owner_or_readonly(env):
    _,_,_,_,c=env
    owner=c.post('/api/admins',json={'username':'owner2','password':'OwnerPass99','role':'owner','permissions':{}})
    readonly=c.post('/api/admins',json={'username':'observer','password':'ObservePass99','role':'readonly','permissions':{}})
    assert owner.status_code==409 and 'one primary owner' in owner.text
    assert readonly.status_code==409 and 'only representative' in readonly.text
    edit=c.patch('/api/admins/dark',json={'disabled':True})
    assert edit.status_code==409
    assert c.get('/api/me').status_code==200


def test_representative_prefix_ip_and_hwid_are_real_client_policies(env):
    _,_,_,_,c=env
    inbound_id=create_inbound(c)
    assert c.put('/api/resellers/seller',json=rep_body(inbound_id)).status_code==200

    wrong_prefix=c.post('/api/clients',json={
        'owner':'seller','client':{'email':'wrong','limitIp':1,'limitHwid':1},'inboundIds':[inbound_id]})
    assert wrong_prefix.status_code==400 and 'prefix' in wrong_prefix.text.lower()

    too_many_ip=c.post('/api/clients',json={
        'owner':'seller','client':{'email':'s_ip','limitIp':3,'limitHwid':1},'inboundIds':[inbound_id]})
    assert too_many_ip.status_code==400 and 'IP limit' in too_many_ip.text

    too_many_hwid=c.post('/api/clients',json={
        'owner':'seller','client':{'email':'s_hwid','limitIp':2,'limitHwid':2},'inboundIds':[inbound_id]})
    assert too_many_hwid.status_code==400 and 'HWID limit' in too_many_hwid.text

    good=c.post('/api/clients',json={
        'owner':'seller','client':{'email':'s_ok','limitIp':2,'limitHwid':1},'inboundIds':[inbound_id]})
    assert good.status_code==202,good.text
    patch=c.patch('/api/clients/s_ok',json={'client':{'limitHwid':2}})
    assert patch.status_code==400 and 'HWID cap' in patch.text


def test_disabling_representative_revokes_login_and_blocks_owned_clients(env):
    store,engine,_,auth,c=env
    inbound_id=create_inbound(c)
    assert c.put('/api/resellers/seller',json=rep_body(inbound_id)).status_code==200
    created=c.post('/api/clients',json={
        'owner':'seller','client':{'email':'s_off','limitIp':1,'limitHwid':1},'inboundIds':[inbound_id]})
    assert created.status_code==202,created.text
    _,principal=auth.login('seller','SellerPass88','','127.0.0.2')
    assert principal.actor.role=='reseller'

    disabled=rep_body(inbound_id,password=None,enabled=False)
    r=c.put('/api/resellers/seller',json=disabled)
    assert r.status_code==200,r.text
    assert r.json()['enabled'] is False
    assert c.post('/api/auth/login',json={'username':'seller','password':'SellerPass88','otp':''}).status_code==403
    with store.lock:
        owner=store.db.execute("SELECT manual,account_disabled FROM owners WHERE id='seller'").fetchone()
    assert tuple(owner)==(1,1)
    assert engine.client_detail('s_off')['client']['enable'] is False


def test_representative_delete_requires_no_clients_and_preserves_primary_owner(env):
    store,_,_,_,c=env
    inbound_id=create_inbound(c)
    assert c.put('/api/resellers/seller',json=rep_body(inbound_id)).status_code==200
    assert c.post('/api/clients',json={
        'owner':'seller','client':{'email':'s_keep','limitIp':1,'limitHwid':1},'inboundIds':[inbound_id]}).status_code==202
    blocked=c.delete('/api/resellers/seller')
    assert blocked.status_code==409 and 'clients' in blocked.text.lower()
    assert c.delete('/api/resellers/dark').status_code==409
    with store.lock:
        assert store.db.execute("SELECT role FROM api_admins WHERE id='dark'").fetchone()['role']=='owner'
