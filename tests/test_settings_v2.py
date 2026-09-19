import importlib.util
import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from auth import Auth
from core import Config, CoreEngine
from dark_policy import Actor, Store
from manager import Manager
from server import make_app

OWNER=Actor('dark','owner',{})
IB={'remark':'Settings Test','listen':'127.0.0.1','port':19444,'protocol':'vless','enable':True,'tag':'settings-test',
    'settings':{'decryption':'none'},'streamSettings':{'network':'tcp','security':'none'},'sniffing':{}}

@pytest.fixture
def env(tmp_path):
    store=Store(tmp_path/'dark.sqlite3')
    config=Config(xray_binary=str(tmp_path/'missing-xray'),xray_assets=str(tmp_path),public_address='vpn.example.test',test_engine=True)
    engine=CoreEngine(config,store,tmp_path/'runtime');manager=Manager(store,engine);auth=Auth(store,tmp_path/'secret.key')
    auth.bootstrap('dark','Test!OnlyPassword123');manager.owner_put(OWNER,'dark',name='DARK',allowed=[])
    app=make_app(manager,auth,background=False)
    with TestClient(app,base_url=config.public_origin) as c:
        r=c.post('/api/auth/login',json={'username':'dark','password':'Test!OnlyPassword123'});assert r.status_code==200
        c.headers['X-Dark-CSRF']=r.json()['csrf']
        yield store,engine,c
    store.close()


def test_settings_v2_defaults(env):
    _,_,c=env
    panel=c.get('/api/settings/panel').json()['value']
    runtime=c.get('/api/settings/runtime').json()['value']
    sub=c.get('/api/settings/subscription').json()['value']
    assert panel['language']=='en' and panel['session_max_age_minutes']==480
    assert runtime['access_mode']=='ssh' and runtime['bind_port']==2087 and runtime['panel_path']=='/'
    assert sub['enabled'] is True and sub['default_format']=='base64'


def test_panel_and_runtime_validation(env):
    _,_,c=env
    panel=c.get('/api/settings/panel').json()['value']
    panel.update(language='fa',timezone='Asia/Tehran',page_size=100,session_max_age_minutes=120,density='compact',reduced_motion=True)
    assert c.put('/api/settings/panel',json={'value':panel}).status_code==200
    bad=dict(panel,timezone='Not/AZone')
    assert c.put('/api/settings/panel',json={'value':bad}).status_code==422

    runtime=c.get('/api/settings/runtime').json()['value']
    runtime.update(bind_port=2443,public_address='edge.example.test',panel_path='/dark-admin',poll_seconds=9,core_autostart=True)
    r=c.put('/api/settings/runtime',json={'value':runtime});assert r.status_code==200,r.text
    status=c.get('/api/runtime-config').json()
    assert status['pending']['bind_port']['to']==2443
    assert status['pending']['panel_path']['to']=='/dark-admin'
    assert status['apply_command']=='sudo darkxray settings-apply'
    assert c.post('/api/inbounds',json=IB).status_code==200
    runtime['bind_port']=19444
    assert c.put('/api/settings/runtime',json={'value':runtime}).status_code==422




def test_reserved_panel_uri_paths_are_rejected(env,tmp_path):
    _,_,c=env
    runtime=c.get('/api/settings/runtime').json()['value']
    for path in ('/api','/assets','/sub','/node','/health','/sub/private'):
        candidate=dict(runtime,panel_path=path)
        assert c.put('/api/settings/runtime',json={'value':candidate}).status_code==422
        with pytest.raises(ValueError):
            Config(xray_binary=str(tmp_path/'xray'),xray_assets=str(tmp_path),panel_path=path,test_engine=True)


def test_domain_mode_requires_domain_and_email(env):
    _,_,c=env
    runtime=c.get('/api/settings/runtime').json()['value']
    runtime.update(access_mode='domain_tls',domain='',acme_email='')
    assert c.put('/api/settings/runtime',json={'value':runtime}).status_code==422
    runtime.update(domain='panel.example.com',acme_email='admin@example.com')
    assert c.put('/api/settings/runtime',json={'value':runtime}).status_code==200


def test_subscription_settings_are_used(env):
    _,_,c=env
    assert c.post('/api/inbounds',json=IB).status_code==200
    ids=[x['id'] for x in c.get('/api/inbounds').json()]
    r=c.post('/api/clients',json={'owner':'dark','client':{'email':'subtest'},'inboundIds':ids});assert r.status_code==202
    sub=c.get('/api/settings/subscription').json()['value']
    sub.update(default_format='raw',profile_update_interval_hours=12,remark_template='{protocol} :: {email}',support_url='https://support.example.com')
    assert c.put('/api/settings/subscription',json={'value':sub}).status_code==200
    out=c.get(r.json()['subscription_url'])
    assert out.status_code==200
    assert out.content.startswith(b'vless://')
    assert out.headers['profile-update-interval']=='12'
    assert out.headers['support-url']=='https://support.example.com'
    assert 'VLESS%20%3A%3A%20subtest' in out.text
    sub['enabled']=False
    assert c.put('/api/settings/subscription',json={'value':sub}).status_code==200
    assert c.get(r.json()['subscription_url']).status_code==404


def test_subscription_userinfo_reports_local_plus_remote_node_traffic(env):
    store,_,c=env
    assert c.post('/api/inbounds',json=IB).status_code==200
    iid=c.get('/api/inbounds').json()[0]['id']
    r=c.post('/api/clients',json={'owner':'dark','client':{'email':'global-sub','totalGB':10_000},'inboundIds':[iid]})
    assert r.status_code==202,r.text
    with store.transaction() as db:
        db.execute("UPDATE core_clients SET up=100,down=200 WHERE email='global-sub'")
        db.execute("""INSERT INTO remote_node_client_usage(
          node_id,client_id,raw_up,raw_down,current_up,current_down,seq,initialized,last_seen)
          VALUES('n1','global-sub',300,400,300,400,1,1,1)""")
        db.execute("""INSERT INTO remote_node_client_usage(
          node_id,client_id,raw_up,raw_down,current_up,current_down,seq,initialized,last_seen)
          VALUES('n2','global-sub',50,60,50,60,1,1,1)""")
    out=c.get(r.json()['subscription_url']+'?format=raw')
    assert out.status_code==200,out.text
    info=out.headers['subscription-userinfo']
    assert 'upload=450' in info
    assert 'download=660' in info
    assert 'total=10000' in info


def test_disabled_subscription_has_no_device_registration_side_effect(env):
    store,_,c=env
    assert c.post('/api/inbounds',json=IB).status_code==200
    iid=c.get('/api/inbounds').json()[0]['id']
    r=c.post('/api/clients',json={'owner':'dark','client':{'email':'hwid-disabled','limitHwid':1},'inboundIds':[iid]})
    assert r.status_code==202,r.text
    sub=c.get('/api/settings/subscription').json()['value'];sub['enabled']=False
    assert c.put('/api/settings/subscription',json={'value':sub}).status_code==200
    out=c.get(r.json()['subscription_url'],headers={'x-hwid':'device-should-not-register'})
    assert out.status_code==404
    with store.lock:
        assert store.db.execute("SELECT COUNT(*) FROM core_devices WHERE email='hwid-disabled'").fetchone()[0]==0


def test_subscription_policy_status_describes_real_format_and_hwid_behavior(env):
    store,_,c=env
    assert c.post('/api/inbounds',json=IB).status_code==200
    iid=c.get('/api/inbounds').json()[0]['id']
    limited=c.post('/api/clients',json={'owner':'dark','client':{'email':'hwid-one','limitHwid':1},'inboundIds':[iid]})
    assert limited.status_code==202,limited.text
    assert c.get(limited.json()['subscription_url'],headers={'x-hwid':'browser-device-1','x-device-os':'ios'}).status_code==200
    status=c.get('/api/subscription/status')
    assert status.status_code==200,status.text
    doc=status.json()
    assert doc['base_url'].endswith('/sub/<token>')
    assert doc['traffic_scope']=='local_plus_remote_nodes'
    assert doc['supported_formats']==['base64','raw','clash','json']
    assert doc['auto_detect_rules']==[{'contains':'clash','format':'clash'},{'contains':'mihomo','format':'clash'}]
    assert doc['device_policy']['clients_with_hwid_limit']==1
    assert doc['device_policy']['registered_devices']==1
    assert doc['device_policy']['clients_at_device_limit']==1
    assert doc['device_policy']['required_header']=='x-hwid'
    assert 'TURKEY FAST' in doc['remark_preview']


def test_subscription_auto_detect_only_changes_known_clash_mihomo_user_agents(env):
    _,_,c=env
    assert c.post('/api/inbounds',json=IB).status_code==200
    iid=c.get('/api/inbounds').json()[0]['id']
    r=c.post('/api/clients',json={'owner':'dark','client':{'email':'ua-sub'},'inboundIds':[iid]})
    assert r.status_code==202,r.text
    sub=c.get('/api/settings/subscription').json()['value'];sub.update(default_format='raw',auto_detect=True)
    assert c.put('/api/settings/subscription',json={'value':sub}).status_code==200
    raw=c.get(r.json()['subscription_url'],headers={'user-agent':'v2rayNG/1.9'})
    clash=c.get(r.json()['subscription_url'],headers={'user-agent':'Mihomo/1.19'})
    assert raw.status_code==200 and raw.content.startswith(b'vless://')
    assert clash.status_code==200 and clash.headers['content-type'].startswith('application/yaml')
    explicit=c.get(r.json()['subscription_url']+'?format=json',headers={'user-agent':'Mihomo/1.19'})
    assert explicit.status_code==200 and explicit.headers['content-type'].startswith('application/json')


def test_login_cookie_uses_panel_session_policy(env):
    _,_,c=env
    panel=c.get('/api/settings/panel').json()['value'];panel['session_max_age_minutes']=90
    assert c.put('/api/settings/panel',json={'value':panel}).status_code==200
    c.post('/api/auth/logout',json={})
    r=c.post('/api/auth/login',json={'username':'dark','password':'Test!OnlyPassword123'})
    assert r.status_code==200
    assert 'Max-Age=5400' in r.headers.get('set-cookie','')


def test_settings_apply_plan_is_pure(tmp_path):
    path=Path(__file__).resolve().parents[1]/'tools'/'settings_apply.py'
    spec=importlib.util.spec_from_file_location('settings_apply',path);mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
    current={'public_origin':'http://127.0.0.1:2087','bind_port':2087,'public_address':'1.2.3.4','poll_seconds':5,'core_autostart':False,
             'xray_api_port':10085,'tls_certificate':'','tls_private_key':''}
    desired={'access_mode':'ssh','bind_port':2443,'public_address':'edge.example.com','panel_path':'/dark-admin','poll_seconds':10,'core_autostart':True,'domain':'','acme_email':''}
    valid=mod.validate_desired(desired,current,set())
    plan=mod.build_plan(current,valid)
    assert plan['pending']['bind_port']=={'from':2087,'to':2443}
    assert plan['requires_acme'] is False


def test_legacy_panel_section_is_hydrated(env):
    store,engine,c=env
    with store.transaction() as db:
        db.execute("INSERT INTO core_sections(name,body) VALUES('panel',?) ON CONFLICT(name) DO UPDATE SET body=excluded.body",(json.dumps({'title':'OLD DARK','support_url':''}),))
    value=c.get('/api/settings/panel').json()['value']
    assert value['title']=='OLD DARK'
    assert value['language']=='en'
    assert value['session_max_age_minutes']==480


def test_database_session_expiry_matches_setting(env):
    store,_,c=env
    panel=c.get('/api/settings/panel').json()['value'];panel['session_max_age_minutes']=60
    assert c.put('/api/settings/panel',json={'value':panel}).status_code==200
    c.post('/api/auth/logout',json={})
    before=time.time()
    r=c.post('/api/auth/login',json={'username':'dark','password':'Test!OnlyPassword123'});assert r.status_code==200
    with store.lock:
        expiry=store.db.execute('SELECT MAX(expires_at) FROM live_sessions').fetchone()[0]
    assert 3590 <= expiry-before <= 3610



def test_panel_uri_path_scopes_ui_api_and_cookie(tmp_path):
    store=Store(tmp_path/'dark.sqlite3')
    config=Config(xray_binary=str(tmp_path/'missing-xray'),xray_assets=str(tmp_path),public_address='vpn.example.test',panel_path='/dark-admin',test_engine=True)
    engine=CoreEngine(config,store,tmp_path/'runtime');manager=Manager(store,engine);auth=Auth(store,tmp_path/'secret.key')
    auth.bootstrap('dark','Test!OnlyPassword123');manager.owner_put(OWNER,'dark',name='DARK',allowed=[])
    app=make_app(manager,auth,background=False)
    with TestClient(app,base_url=config.public_origin,follow_redirects=False) as c:
        assert c.get('/').status_code==404
        r=c.get('/dark-admin');assert r.status_code==307 and r.headers['location']=='/dark-admin/'
        assert c.get('/dark-admin/').status_code==200
        assert c.get('/dark-admin/assets/style.css').status_code==200
        assert c.post('/api/auth/login',json={'username':'dark','password':'Test!OnlyPassword123'}).status_code==404
        r=c.post('/dark-admin/api/auth/login',json={'username':'dark','password':'Test!OnlyPassword123'});assert r.status_code==200
        assert 'Path=/dark-admin' in r.headers.get('set-cookie','')
        assert c.get('/health').status_code==200
    store.close()



def test_runtime_stage_supports_port_address_and_path(tmp_path):
    path=Path(__file__).resolve().parents[1]/'tools'/'runtime_stage.py'
    spec=importlib.util.spec_from_file_location('runtime_stage',path);mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
    base={'access_mode':'ssh','bind_port':2087,'public_address':'1.2.3.4','panel_path':'/','poll_seconds':5,'core_autostart':False,'domain':'','acme_email':''}
    value,changed=mod.apply_overrides(base,panel_path='/dark-next',bind_port=2443,public_address='edge.example.com')
    assert value['panel_path']=='/dark-next' and value['bind_port']==2443 and value['public_address']=='edge.example.com'
    assert set(changed)=={'panel_path','bind_port','public_address'}
    with pytest.raises(SystemExit):mod.apply_overrides(base,panel_path='/sub')
    with pytest.raises(SystemExit):mod.apply_overrides(base,bind_port=80)
    with pytest.raises(SystemExit):mod.apply_overrides(base,public_address='https://bad.example')
