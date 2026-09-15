import base64
import json
import os
import socket
import time
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from dark_policy import Store,Actor,PolicyError
from auth import Auth
from core import Config,CoreEngine,CoreError
from manager import Manager,SYSTEM
from server import make_app

OWNER=Actor('dark','owner',{})
IB={'remark':'DARK Test','listen':'127.0.0.1','port':19443,'protocol':'vless','enable':True,'tag':'dark-test',
    'settings':{'decryption':'none'},'streamSettings':{'network':'tcp','security':'none'},'sniffing':{}}

@pytest.fixture
def env(tmp_path):
    store=Store(tmp_path/'dark.sqlite3')
    config=Config(xray_binary=str(tmp_path/'missing-xray'),xray_assets=str(tmp_path),public_address='vpn.example.test',test_engine=True)
    engine=CoreEngine(config,store,tmp_path/'runtime');manager=Manager(store,engine);auth=Auth(store,tmp_path/'secret.key')
    auth.bootstrap('dark','Test!OnlyPassword123')
    manager.owner_put(OWNER,'dark',name='DARK',allowed=[])
    app=make_app(manager,auth,background=False)
    with TestClient(app,base_url=config.public_origin) as client:
        r=client.post('/api/auth/login',json={'username':'dark','password':'Test!OnlyPassword123'})
        assert r.status_code==200,r.text
        client.headers['X-Dark-CSRF']=r.json()['csrf']
        yield store,engine,manager,auth,client
    store.close()

def create(c,email='dark-test',owner='dark',extra=None):
    if not c.get('/api/inbounds').json():
        r=c.post('/api/inbounds',json=IB);assert r.status_code==200,r.text
    ids=[x['id'] for x in c.get('/api/inbounds').json()]
    r=c.post('/api/clients',json={'owner':owner,'client':{'email':email,**(extra or {})},'inboundIds':ids})
    assert r.status_code==202,r.text
    return r.json()

def test_health_is_standalone(env):
    _,_,_,_,c=env
    assert c.get('/health').json()['mode']=='standalone'
    assert c.get('/api/me').json()['independent'] is True

def test_no_panel_dependencies(env):
    _,_,_,_,c=env
    for route in ['/native/panel/inbounds','/engine/panel/inbounds','/native/ws']:
        assert c.get(route).status_code==404
    for key in ('upstream_url','upstream_token','subscription_url','expected_native'):
        with pytest.raises(TypeError):Config(**{key:'not-permitted'})

def test_own_inbound_and_customer(env):
    store,engine,m,_,c=env
    result=create(c)
    assert result['state']=='applied'
    assert engine.inbounds()[0]['remark']=='DARK Test'
    assert engine.client_detail('dark-test')['inboundIds']==[1]
    assert c.get('/api/sync').json()['runtime']['running'] is False
    assert c.get('/api/sync').json()['runtime']['dirty'] is True

def test_missing_core_is_reported_not_simulated(env):
    _,_,_,_,c=env;create(c)
    r=c.post('/api/core/start',json={})
    assert r.status_code==503
    assert 'executable is missing' in r.text
    assert c.get('/api/core/state').json()['running'] is False

def test_actual_resources_no_remote_source(env):
    _,_,_,_,c=env;r=c.get('/api/system')
    assert r.status_code==200
    assert r.json()['sample'] is False
    assert r.json()['engine']['mem']['total']>0
    assert 'DARK local' in r.json()['source']

def test_grpc_compilation(env):
    _,engine,_,_,c=env
    ib=json.loads(json.dumps(IB));ib['streamSettings']={'network':'grpc','security':'none','grpcSettings':{'serviceName':'dark-service'}}
    assert c.post('/api/inbounds',json=ib).status_code==200
    create(c)
    cfg=engine.build_config()
    assert cfg['inbounds'][0]['streamSettings']['grpcSettings']['serviceName']=='dark-service'
    assert cfg['api']['listen'].startswith('127.0.0.1:')
    assert cfg['policy']['levels']['0']['statsUserUplink'] is True
    assert 'clients' in cfg['inbounds'][0]['settings']

def test_no_credentials_in_inbound_json(env):
    _,_,_,_,c=env
    ib=dict(IB,settings={'clients':[{'id':'hidden-bypass'}]})
    assert c.post('/api/inbounds',json=ib).status_code==422

def test_inbound_protected_port(env):
    _,_,_,_,c=env
    assert c.post('/api/inbounds',json=dict(IB,port=2087)).status_code==422
    assert c.post('/api/inbounds',json=dict(IB,port=10085)).status_code==422

def test_duplicate_port(env):
    _,_,_,_,c=env
    assert c.post('/api/inbounds',json=IB).status_code==200
    assert c.post('/api/inbounds',json=dict(IB,tag='other')).status_code==422

def test_client_dependent_inbound_not_deleted(env):
    _,_,_,_,c=env;create(c)
    assert c.delete('/api/inbounds/1').status_code==422

def test_subscription_is_generated_locally(env):
    _,_,_,_,c=env;create(c)
    link=c.get('/api/clients/dark-test/links').json()
    assert link['engine']['links'][0]['uri'].startswith('vless://')
    url=link['subscription_url'];r=c.get(url)
    assert r.status_code==200,r.text
    decoded=base64.b64decode(r.content).decode()
    assert '@vpn.example.test:19443?' in decoded
    assert 'subscription-userinfo' in r.headers

def test_unknown_sub_format_not_silently_changed(env):
    _,_,_,_,c=env;r=create(c)
    assert c.get(r['subscription_url']+'?format=singbox').status_code==400

def test_hwid_is_local_and_transactional(env):
    _,_,_,_,c=env;r=create(c,extra={'limitHwid':1})
    url=r['subscription_url']
    assert c.get(url).status_code==403
    assert c.get(url,headers={'x-hwid':'device-aaa'}).status_code==200
    assert c.get(url,headers={'x-hwid':'device-aaa'}).status_code==200
    assert c.get(url,headers={'x-hwid':'device-bbb'}).status_code==403
    rows=c.get('/api/clients/dark-test/devices').json()
    assert len(rows)==1 and 'digest' not in rows[0]
    assert c.delete('/api/clients/dark-test/devices').status_code==200
    assert c.get(url,headers={'x-hwid':'device-bbb'}).status_code==200





def test_api_version_uses_version_file(env):
    store,engine,m,auth,c=env
    expected=(Path(__file__).resolve().parents[1]/'VERSION').read_text().strip()
    assert c.get('/health').json()['version']==expected
    assert c.get('/api/me').json()['version']==expected


def test_web_owner_creation_attaches_owner_profile(env):
    store,engine,m,auth,c=env
    assert c.post('/api/inbounds',json=IB).status_code==200
    r=c.post('/api/admins',json={'username':'Mika','password':'MikaPass88','role':'owner','permissions':{}})
    assert r.status_code==200,r.text
    with store.lock:
        admin=store.db.execute("SELECT role,password_hash FROM api_admins WHERE id='Mika'").fetchone()
        owner=store.db.execute("SELECT id FROM owners WHERE id='Mika'").fetchone()
        profile=store.db.execute("SELECT id,allowed FROM owner_profiles WHERE id='Mika'").fetchone()
    assert admin['role']=='owner' and owner['id']=='Mika' and profile['id']=='Mika'
    assert json.loads(profile['allowed'])==[1]
    token,p=auth.login('Mika','MikaPass88','','127.0.0.9')
    with TestClient(make_app(m,auth,background=False),base_url=engine.config.public_origin) as other:
        other.cookies.set('dark_session',token);other.headers['X-Dark-CSRF']=p.csrf
        me=other.get('/api/me');assert me.status_code==200 and me.json()['role']=='owner'
        ids={x['id'] for x in other.get('/api/owners').json()}
        assert 'Mika' in ids


def test_reseller_scope_and_shared_inbound(env):
    store,engine,m,auth,c=env
    create(c,'dark-a')
    assert c.put('/api/owners/arda',json={'name':'ARDA','allowed':[1],'max_clients':1,'quota_bytes':100}).status_code==200
    assert c.post('/api/admins',json={'username':'arda','password':'AnotherTestOnly123','role':'reseller'}).status_code==200
    create(c,'arda-a','arda')
    token,p=auth.login('arda','AnotherTestOnly123','', '127.0.0.2')
    with TestClient(make_app(m,auth,background=False),base_url=engine.config.public_origin) as other:
        other.cookies.set('dark_session',token);other.headers['X-Dark-CSRF']=p.csrf
        assert [r['email'] for r in other.get('/api/clients').json()]==['arda-a']
        assert other.get('/api/clients/dark-a').status_code==403
        assert other.get('/api/inbounds/1').status_code==403
        assert other.get('/api/settings/routing').status_code==403
        assert other.post('/api/clients',json={'owner':'arda','client':{'email':'arda-b'},'inboundIds':[1]}).status_code==400
    with store.transaction() as db:db.execute('UPDATE core_clients SET up=150 WHERE email=?',('arda-a',))
    m.tick()
    assert engine.client_detail('arda-a')['client']['enable'] is False
    assert engine.client_detail('dark-a')['client']['enable'] is True
    assert engine.inbound(1)['enable'] is True

def test_traffic_ledger_survives_reset_and_delete(env):
    store,engine,m,_,c=env;create(c)
    with store.transaction() as db:db.execute('UPDATE core_clients SET up=23 WHERE email=?',('dark-test',))
    m.tick();assert store.owner_stats(OWNER,'dark')['used_bytes']==23
    assert c.post('/api/clients/dark-test/action',json={'action':'reset'}).status_code==202
    assert store.owner_stats(OWNER,'dark')['used_bytes']==23
    assert c.post('/api/clients/dark-test/action',json={'action':'delete'}).status_code==202
    assert store.owner_stats(OWNER,'dark')['used_bytes']==23

def test_manual_disable_survives_credit(env):
    _,engine,m,_,c=env;create(c)
    c.post('/api/clients/dark-test/action',json={'action':'disable'})
    c.put('/api/owners/dark',json={'name':'DARK','allowed':[1],'quota_bytes':1000000})
    assert engine.client_detail('dark-test')['client']['enable'] is False

def test_local_hosts_change_link(env):
    _,_,_,_,c=env;create(c)
    r=c.put('/api/settings/hosts',json={'value':[{'inboundId':1,'address':'iran.example.test','port':2020,'remark':'DARK tunnel'}]})
    assert r.status_code==200,r.text
    link=c.get('/api/clients/dark-test/links').json()['engine']['links'][0]['uri']
    assert '@iran.example.test:2020?' in link

def test_routing_references(env):
    _,_,_,_,c=env
    r=c.put('/api/settings/routing',json={'value':{'rules':[{'type':'field','domain':['domain:example.com'],'outboundTag':'missing'}]}})
    assert r.status_code==422
    assert c.put('/api/settings/routing',json={'value':{'rules':[{'type':'field','domain':['domain:example.com'],'outboundTag':'block'}]}}).status_code==200
    assert c.put('/api/settings/outbounds',json={'value':[{'tag':'direct','protocol':'freedom'}]}).status_code==422

def test_x25519_keys(env):
    _,_,_,_,c=env;r=c.post('/api/keys/x25519',json={})
    assert r.status_code==200
    assert len(base64.urlsafe_b64decode(r.json()['privateKey']+'='))==32
    assert len(r.json()['shortId'])==16

def test_csrf_and_origin_checks(env):
    _,_,_,_,c=env
    assert c.post('/api/inbounds',json=IB,headers={'X-Dark-CSRF':'wrong'}).status_code==403
    assert c.post('/api/inbounds',json=IB,headers={'Origin':'https://wrong.example'}).status_code==403

def test_ip_policy_is_independent(env):
    _,_,_,_,c=env;create(c)
    p=c.get('/api/ip-policy').json()
    assert p['schema']==1 and p['clients']['dark-test']['limit_ip']==1
    assert p['enforce'] is False
    assert c.get('/api/ip-status').json()['engine']['limiter']=='independent DARK Guard'

def test_fixed_day_cycle_scheduled(env):
    _,_,_,_,c=env;c.post('/api/inbounds',json=IB)
    r=c.post('/api/clients',json={'owner':'dark','client':{'email':'abc','reset':30},'inboundIds':[1]})
    assert r.status_code==202
    store=env[0]
    with store.lock: row=store.db.execute('SELECT days,next_at FROM client_cycles WHERE email=?',('abc',)).fetchone()
    assert row['days']==30 and row['next_at']>time.time()

def test_backup_has_own_runtime_tables(env):
    import zipfile,io,sqlite3,tempfile
    _,_,_,_,c=env;create(c)
    r=c.get('/api/backup');assert r.status_code==200
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        assert 'dark.sqlite3' in z.namelist()
        assert json.loads(z.read('manifest.json'))['runtime_tables_included'] is True

@pytest.mark.parametrize('val',[True,-1,0,65536,'10085'])
def test_bad_api_ports(val):
    with pytest.raises(ValueError):Config(xray_api_port=val)
