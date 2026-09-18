"""v0.6 regressions: real DB/HTTP/Unix IPC, fake nft command runner explicitly.

These tests NEVER claim a real packet firewall or real Xray proxy connection.
"""
import dataclasses,json,os,socket,subprocess,sys,threading,time
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from test_standalone import env, create, OWNER, IB
from core import Config,CoreEngine,CoreError
from dark_policy import Store,Policy,PolicyError,Actor,Guard,jail_for
from guardd import BrokerConfig,NftFirewall,BrokerServer
from guard_bridge import BrokerClient,BrokerExecutor
from backup import create_backup,restore_backup

class FakeNft:
    """No kernel effect: record generated command shape, emulate named elements."""
    def __init__(self):self.calls=[];self.exists=False;self.elements=set();self.fail=False
    def __call__(self,args,**kw):
        self.calls.append((args,kw));script=kw.get('input') or ''
        if self.fail:return subprocess.CompletedProcess(args,1,'','TEST kernel refused')
        if args[1:]==['-j','list','tables']:
            return subprocess.CompletedProcess(args,0,json.dumps({'nftables':[{'table':{'family':'inet','name':'dark_xray_ip'}}] if self.exists else []}),'')
        if args[1:]==['-j','list','table','inet','dark_xray_ip']:
            return subprocess.CompletedProcess(args,0,json.dumps({'nftables':[{'table':{'comment':'DARK XRAY IP Guard v1'}}]}),'')
        if 'get' in args:return subprocess.CompletedProcess(args,1,'','TEST nonexistent')
        if '-c' not in args and 'table inet dark_xray_ip {' in script:self.exists=True
        return subprocess.CompletedProcess(args,0,'','')

def broker_config(**kw):
    return BrokerConfig.from_dict({'allowed_uid':1001,'allowed_ports':[443,2020],
                                  'protected_ports':[22,2087,10085],'direct_source_verified':True,**kw})

def test_privileged_inbound_port_is_accepted(env):
    *_,c=env
    r=c.post('/api/inbounds',json={**IB,'port':443})
    assert r.status_code==200,r.text
    assert r.json()['port']==443

@pytest.mark.parametrize('port',[22,2087,10085,0,-1,65536,True,'443'])
def test_management_and_invalid_inbound_ports_refused(env,port):
    *_,c=env
    assert c.post('/api/inbounds',json={**IB,'port':port}).status_code==422

@pytest.mark.parametrize('cfg',[
 {'allowed_uid':0}, {'allowed_ports':[22]}, {'allowed_ports':[443,2087]},
 {'allowed_ports':[]}, {'direct_source_verified':'yes'}, {'nft_binary':'/tmp/evil'},
 {'socket_path':'/tmp/unsafe.sock'}, {'exempt_ips':['not-an-IP']}, {'unknown':'value'}])
def test_root_broker_rejects_unsafe_configuration(cfg):
    with pytest.raises((PolicyError,TypeError)):broker_config(**cfg)

def test_broker_applies_only_numeric_address_and_approved_ports():
    cfg=broker_config();fake=FakeNft();fw=NftFirewall(cfg,runner=fake);fw.bootstrap()
    result=fw.dispatch({'operation':'ban','ip':'8.8.8.8','ports':[443,2020],'seconds':60},1001)
    assert result['ok'] and not result['packet_block_verified']
    script=fake.calls[-1][1]['input']
    assert '8.8.8.8 . 443 timeout 60s' in script
    assert '8.8.8.8 . 2020 timeout 60s' in script
    assert all('shell' not in kw for _,kw in fake.calls)
    assert all('flush ruleset' not in (kw.get('input') or '') for _,kw in fake.calls)

def test_nft_bootstrap_does_not_accept_other_table_owner():
    class OtherNft(FakeNft):
        def __call__(self,args,**kw):
            if args[1:]==['-j','list','table','inet','dark_xray_ip']:
                return subprocess.CompletedProcess(args,0,'{"nftables":[{"table":{"comment":"OTHER"}}]}','')
            return super().__call__(args,**kw)
    fake=OtherNft();fake.exists=True
    with pytest.raises(PolicyError,match='foreign'):NftFirewall(broker_config(),fake).bootstrap()

@pytest.mark.parametrize('message,uid',[
 ({'operation':'ban','ip':'8.8.8.8','ports':[22],'seconds':60},1001),
 ({'operation':'ban','ip':'127.0.0.1','ports':[443],'seconds':60},1001),
 ({'operation':'ban','ip':'10.0.0.1','ports':[443],'seconds':60},1001),
 ({'operation':'ban','ip':'8.8.8.8; flush ruleset','ports':[443],'seconds':60},1001),
 ({'operation':'ban','ip':'8.8.8.8','ports':[443],'seconds':86401},1001),
 ({'operation':'exec','command':'id'},1001),
 ({'operation':'status'},2002),
 ({'operation':'clear','command':'rm -rf /'},1001),
])
def test_broker_rejects_injection_unauthorized_or_management_requests(message,uid):
    fake=FakeNft();fw=NftFirewall(broker_config(),fake);fw.bootstrap();before=len(fake.calls)
    with pytest.raises(PolicyError):fw.dispatch(message,uid)
    assert len(fake.calls)==before

def test_broker_direct_sources_must_be_root_verified():
    fw=NftFirewall(broker_config(direct_source_verified=False),FakeNft());fw.bootstrap()
    with pytest.raises(PolicyError,match='Root'):
        fw.dispatch({'operation':'ban','ip':'8.8.8.8','ports':[443],'seconds':60},1001)

def test_ipc_uses_actual_unix_socket_and_peer_credentials(tmp_path):
    # dataclass override ONLY in this IPC test permits current test runner UID.
    cfg=dataclasses.replace(broker_config(),allowed_uid=os.getuid())
    fw=NftFirewall(cfg,FakeNft());fw.bootstrap();path=tmp_path/'broker.sock'
    server=BrokerServer(str(path),fw);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        client=BrokerClient(str(path));status=client.status()
        assert status['ready'] and status['allowed_ports']==[443,2020]
        assert client.request({'operation':'ban','ip':'8.8.4.4','ports':[2020],'seconds':30})['command_accepted']
        assert client.clear()['cleared']
    finally:server.shutdown();server.server_close();thread.join()

def test_enforcement_mode_requires_operator_approval(env):
    *_,c=env
    r=c.put('/api/settings/ipguard',json={'value':{'mode':'enforce','window_seconds':120,'ban_seconds':60,'exempt_ips':[]}})
    assert r.status_code==422

def test_ip_policy_change_tracks_client_limit_without_manual_export(env):
    _,engine,_,_,c=env;create(c)
    old=engine.ip_status()['policy_hash']
    assert c.patch('/api/clients/dark-test',json={'client':{'limitIp':2}}).status_code==202
    assert engine.ip_policy().clients['dark-test'].limit_ip==2
    assert engine.ip_status()['policy_hash']!=old
    assert engine.ip_status()['state']=='observing'

def test_guard_unavailable_is_error_not_applied(env):
    _,engine,manager,_,c=env;create(c);engine.config.direct_source_verified=True
    r=c.put('/api/settings/ipguard',json={'value':{'mode':'enforce','window_seconds':120,'ban_seconds':60,'exempt_ips':[]}})
    assert r.status_code==200
    status=engine.ip_status()
    assert status['state']=='error' and not status['applied']
    assert not status['loaded_policy_hash']

def test_guard_linked_real_ipc_with_simulated_kernel(env,tmp_path):
    store,engine,manager,_,c=env;create(c);engine.config.direct_source_verified=True
    cfg=dataclasses.replace(broker_config(allowed_ports=[19443]),allowed_uid=os.getuid())
    fake=FakeNft();fw=NftFirewall(cfg,fake);fw.bootstrap()
    path=tmp_path/'local-guard.sock';server=BrokerServer(str(path),fw)
    worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start();engine.config.guard_socket=str(path)
    try:
        r=c.put('/api/settings/ipguard',json={'value':{'mode':'enforce','window_seconds':120,'ban_seconds':60,'exempt_ips':[]}})
        assert r.status_code==200 and engine.ip_status()['applied']
        access=engine.runtime/'access.log'
        access.write_text('from tcp:8.8.8.8:50001 accepted tcp:1.1.1.1:443 [dark-test -> direct] email: dark-test\n')
        engine.read_ip_log()
        with access.open('a') as f:f.write('from tcp:8.8.4.4:50002 accepted tcp:1.1.1.1:443 [dark-test -> direct] email: dark-test\n')
        engine.read_ip_log()
        bans=c.get('/api/ip/events').json()['bans']
        assert bans[0]['ip']=='8.8.4.4' and bans[0]['state']=='applied'
        assert any('8.8.4.4 . 19443 timeout 60s' in (kw.get('input') or '') for _,kw in fake.calls)
        assert c.post('/api/ip/unban',json={'ip':'8.8.4.4'}).status_code==200
        assert c.put('/api/settings/ipguard',json={'value':{'mode':'observe','window_seconds':120,'ban_seconds':60,'exempt_ips':[]}}).status_code==200
    finally:server.shutdown();server.server_close();worker.join()

def test_lowering_ip_limit_rejects_excess_existing_source_on_next_event():
    db=Store(':memory:')
    try:
        raw={'schema':1,'source_mode':'direct','original_ip_verified':True,'clients':{'a':{'owner':'dark','ports':[2020],'limit_ip':2}}}
        guard=Guard(Policy.from_dict(raw),db)
        assert guard.observe('a','8.8.8.8',100)['decision']=='allow'
        assert guard.observe('a','8.8.4.4',101)['decision']=='allow'
        raw['clients']['a']['limit_ip']=1;guard=Guard(Policy.from_dict(raw),db)
        assert guard.observe('a','8.8.4.4',102)['decision']=='violation'
        assert guard.observe('a','8.8.8.8',103)['decision']=='allow'
    finally:db.close()

def test_new_cycles_preserve_manual_disable_and_historical_traffic(env):
    db,engine,m,_,c=env;create(c,extra={'reset':1,'resetCount':1})
    with db.transaction() as sql:sql.execute('UPDATE core_clients SET up=123 WHERE email=?',('dark-test',))
    m.tick();c.post('/api/clients/dark-test/action',json={'action':'disable'})
    with db.transaction() as sql:sql.execute('UPDATE client_cycles SET next_at=?',(time.time()-10,))
    m.tick()
    assert db.owner_stats(OWNER,'dark')['used_bytes']==123
    assert engine.client_detail('dark-test')['client']['enable'] is False
    with db.lock:
        row=db.db.execute('SELECT completed,next_at FROM client_cycles').fetchone()
        assert row['completed']==1 and row['next_at']>time.time()
    with db.transaction() as sql:
        sql.execute('UPDATE core_clients SET up=7');sql.execute('UPDATE client_cycles SET next_at=?',(time.time()-10,))
    m.tick()
    assert engine.clients()[0]['traffic']['up']==7  # max reset count prevents another reset

@pytest.mark.parametrize('action',['reset','delete'])
def test_final_observed_bytes_survive_reset_or_delete(env,monkeypatch,action):
    db,engine,m,_,c=env;create(c)
    with db.transaction() as sql:sql.execute('UPDATE core_clients SET up=23')
    m.tick();old=engine.collect_stats;done=False
    def collect(*,force=False,strict=False):
        nonlocal done
        if force and not done:
            with db.transaction() as sql:sql.execute('UPDATE core_clients SET up=up+7')
            done=True
        return old(force=force,strict=strict)
    monkeypatch.setattr(engine,'collect_stats',collect)
    result=c.post('/api/clients/dark-test/action',json={'action':action})
    assert result.status_code==202,result.text
    assert db.owner_stats(OWNER,'dark')['used_bytes']==30

def test_disabled_host_does_not_expose_unexpected_direct_address(env):
    *_,c=env;r=create(c)
    c.put('/api/settings/hosts',json={'value':[{'inboundId':1,'address':'example.test','port':443,'enable':False}]})
    links=c.get('/api/clients/dark-test/links').json()['engine']['links']
    assert links==[]

@pytest.mark.parametrize('bad',[{'network':'ws','security':'reality','realitySettings':{}},
 {'network':'kcp','security':'tls','tlsSettings':{}}, {'network':'grpc','security':'reality','realitySettings':[]},
 {'network':'tcp','security':'reality','realitySettings':{'privateKey':'bad'}}])
def test_transport_guard_rejects_invalid_combinations(env,bad):
    *_,c=env
    assert c.post('/api/inbounds',json={**IB,'streamSettings':bad}).status_code==422

def test_full_encrypted_backup_restore_retains_mfa_key_and_revokes_sessions(env,tmp_path):
    db,engine,m,auth,c=env;create(c)
    source=Path(db.path).parent;config=source/'config.json';config.write_text(json.dumps(dataclasses.asdict(engine.config)))
    output=tmp_path/'backup.darkbackup';manifest=create_backup(source,config,output,'Test-backup-password-06')
    assert 'data/secret.key' in manifest['files']
    assert output.read_bytes()[:2]!=b'PK' and b'Test!OnlyPassword123' not in output.read_bytes()
    dest=tmp_path/'recovered';result=restore_backup(output,dest,'Test-backup-password-06')
    assert result['sessions_revoked'] and result['mfa_key_restored']
    assert (dest/'data/secret.key').read_bytes()==(source/'secret.key').read_bytes()
    restored=Store(dest/'data/dark.sqlite3')
    try:
        assert restored.db.execute('SELECT COUNT(*) FROM core_clients').fetchone()[0]==1
        assert restored.db.execute('SELECT COUNT(*) FROM live_sessions').fetchone()[0]==0
    finally:restored.close()
    with pytest.raises(PolicyError):restore_backup(output,dest,'Test-backup-password-06')
    with pytest.raises(PolicyError):restore_backup(output,tmp_path/'wrong-pass','Wrong-passphrase-123')
    assert not (tmp_path/'wrong-pass').exists()

def test_config_public_tls_listener_and_protected_bind_port(tmp_path):
    cfg=Config(public_origin='https://panel.example.test:8443',secure_cookie=True,bind_port=8443)
    assert 8443 in cfg.protected_ports
    with pytest.raises(ValueError):Config(tls_certificate='/tmp/cert.pem')
    with pytest.raises(ValueError):Config(bind_port=443)

def test_http_unban_not_permitted_to_reseller(env):
    _,engine,m,auth,c=env;create(c)
    c.put('/api/owners/arda',json={'name':'ARDA','allowed':[1]})
    c.post('/api/admins',json={'username':'arda','password':'AnotherTestOnly123','role':'reseller'})
    token,p=auth.login('arda','AnotherTestOnly123','', '127.0.0.2')
    with TestClient(make_app(m,auth,background=False),base_url=engine.config.public_origin) as other:
        other.cookies.set('dark_session',token);other.headers['X-Dark-CSRF']=p.csrf
        assert other.post('/api/ip/unban',json={'ip':'8.8.8.8'}).status_code==403

# Import here keeps fixtures and collection readable.
from server import make_app

@pytest.mark.skipif(not os.environ.get('DARK_REAL_XRAY'),reason='Real Xray binary unavailable; not substituted by a mock')
def test_real_xray_end_to_end_opt_in(tmp_path):
    binary=Path(os.environ['DARK_REAL_XRAY']);tool=Path(__file__).resolve().parents[1]/'tools/smoke-real.py'
    report=tmp_path/'actual.json'
    result=subprocess.run([sys.executable,str(tool),'--binary',str(binary),'--report',str(report)],capture_output=True,text=True,timeout=120)
    assert result.returncode==0,result.stdout+result.stderr
    data=json.loads(report.read_text())
    assert data['passed'] and data['live_proxy_connection_tested']

def test_common_outbound_shapes_fail_with_clear_dark_validation(env):
    *_,c=env
    cases=[
      ([{'tag':'v','protocol':'vless','settings':{'address':'edge.example','port':443,'id':''}}],'VLESS outbound requires a UUID'),
      ([{'tag':'m','protocol':'vmess','settings':{'vnext':[]}}],'VMess outbound requires vnext'),
      ([{'tag':'t','protocol':'trojan','settings':{'servers':[{'address':'edge.example','port':443,'password':''}]}}],'trojan outbound requires a password'),
      ([{'tag':'w','protocol':'wireguard','settings':{'secretKey':'','address':[],'peers':[]}}],'WireGuard outbound requires secretKey'),
      ([{'tag':'l','protocol':'loopback','settings':{}}],'Loopback outbound requires inboundTag'),
      ([{'tag':'d','protocol':'dns','settings':{'rewritePort':70000}}],'DNS outbound rewritePort is invalid'),
    ]
    for value,message in cases:
        r=c.put('/api/settings/outbounds',json={'value':value})
        assert r.status_code==422,(value,r.text)
        assert message in r.text


def test_guided_vless_flat_outbound_shape_is_accepted(env):
    *_,c=env
    value=[
      {'tag':'direct','protocol':'freedom','settings':{}},
      {'tag':'edge','protocol':'vless','settings':{
        'address':'edge.example.test','port':443,'id':'33333333-3333-4333-8333-333333333333',
        'flow':'','encryption':'none'},
       'streamSettings':{'network':'grpc','security':'none','grpcSettings':{'serviceName':'dark'},'sockopt':{}}}
    ]
    r=c.put('/api/settings/outbounds',json={'value':value})
    assert r.status_code==200,r.text
    saved=c.get('/api/settings/outbounds').json()['value']
    assert saved[1]['settings']['address']=='edge.example.test'
    assert saved[1]['streamSettings']['grpcSettings']['serviceName']=='dark'


@pytest.mark.parametrize('value',[
 [{'tag':'loop','protocol':'freedom','streamSettings':{'sockopt':{'dialerProxy':'loop'}}}],
 [{'tag':'edge','protocol':'freedom','streamSettings':{'sockopt':{'dialerProxy':'missing'}}}],
 [{'tag':'a','protocol':'freedom','streamSettings':{'sockopt':{'dialerProxy':'b'}}},
  {'tag':'b','protocol':'freedom','streamSettings':{'sockopt':{'dialerProxy':'a'}}}],
 [{'tag':'bad','protocol':'freedom','streamSettings':[]}],
])
def test_outbound_chain_errors_are_rejected_before_core(env,value):
    *_,c=env
    assert c.put('/api/settings/outbounds',json={'value':value}).status_code==422

def test_missing_balancer_rejected(env):
    *_,c=env
    assert c.put('/api/settings/routing',json={'value':{'rules':[{'type':'field','balancerTag':'missing'}]}}).status_code==422

@pytest.mark.parametrize('overrides',[{'privateKey':{}},{'serverNames':42},{'shortIds':None}])
def test_reality_malformed_types_not_server_500(env,overrides):
    *_,c=env
    keys=c.post('/api/keys/x25519',json={}).json()
    rt={'privateKey':keys['privateKey'],'serverNames':['example.test'],'shortIds':['0123456789abcdef'],'target':'example.test:443',**overrides}
    assert c.post('/api/inbounds',json={**IB,'streamSettings':{'network':'tcp','security':'reality','realitySettings':rt}}).status_code==422

def test_ip_log_read_error_cannot_be_hidden_as_applied(env):
    _,engine,*_=env
    engine._guard_status={'state':'applied','applied':True,'error':''};engine.ip_error='Local log failed'
    status=engine.ip_status();assert status['state']=='error' and status['error']=='Local log failed' and not status['applied']

def test_real_local_http_server_session_and_native_sections(tmp_path):
    """Actual TCP/HTTP round-trips, not Starlette TestClient or a browser."""
    import httpx,uvicorn
    from auth import Auth
    from manager import Manager
    from server import make_app
    s=socket.socket();s.bind(('127.0.0.1',0));port=s.getsockname()[1];s.close()
    cfg=Config(public_origin=f'http://127.0.0.1:{port}',bind_port=port,
               xray_binary=str(tmp_path/'missing-xray'),xray_assets=str(tmp_path),public_address='example.test')
    store=Store(tmp_path/'dark.sqlite3');engine=CoreEngine(cfg,store,tmp_path/'runtime');manager=Manager(store,engine)
    auth=Auth(store,tmp_path/'secret.key');auth.bootstrap('qa','Temporary-test-password-06');manager.owner_put(Actor('qa','owner',{}),'qa',name='QA',allowed=[])
    server=uvicorn.Server(uvicorn.Config(make_app(manager,auth,background=False),host='127.0.0.1',port=port,log_level='error',access_log=False))
    thread=threading.Thread(target=server.run,daemon=True);thread.start()
    try:
        for _ in range(100):
            if server.started:break
            time.sleep(.02)
        assert server.started
        with httpx.Client(base_url=cfg.public_origin,trust_env=False,timeout=15) as c:
            assert c.get('/health').json()['mode']=='standalone'
            assert c.get('/').status_code==200
            assert c.get('/assets/forms06.js').status_code==200
            assert c.get('/api/me').status_code==401
            login=c.post('/api/auth/login',json={'username':'qa','password':'Temporary-test-password-06'});assert login.status_code==200
            c.headers['X-Dark-CSRF']=login.json()['csrf']
            assert c.get('/api/system').json()['sample'] is False
            inbound=c.post('/api/inbounds',json=IB);assert inbound.status_code==200
            assert c.put('/api/settings/hosts',json={'value':[{'inboundId':inbound.json()['id'],'address':'entry.example.test','port':443}]}).status_code==200
            assert c.get('/api/settings/hosts').json()['value'][0]['address']=='entry.example.test'
            assert c.post('/api/core/start',json={}).status_code==503 # never pretends an absent engine is running
            assert c.post('/api/auth/logout',json={}).status_code==200
            assert c.get('/api/me').status_code==401
    finally:
        server.should_exit=True;thread.join(timeout=10);store.close()

def test_offline_core_import_permissions_with_private_installer_umask(tmp_path):
    """Harmless fixture bytes, never executed and never called real Xray."""
    import zipfile,hashlib
    archive=tmp_path/'fixture.zip'
    with zipfile.ZipFile(archive,'w') as z:
        z.writestr('xray',b'TEST ARCHIVE FIXTURE - NOT A REAL CORE\n')
        z.writestr('geoip.dat',b'TEST GEO FIXTURE')
    sha=hashlib.sha256(archive.read_bytes()).hexdigest()
    dest=tmp_path/'core-parent'/'v-test';tool=Path(__file__).resolve().parents[1]/'tools/import-core.py'
    r=subprocess.run([sys.executable,str(tool),'--archive',str(archive),'--sha256',sha,'--destination',str(dest)],capture_output=True,text=True,umask=0o077)
    assert r.returncode==0,r.stderr
    assert (dest.parent.stat().st_mode&0o777)==0o755
    assert (dest.stat().st_mode&0o777)==0o755
    assert ((dest/'xray').stat().st_mode&0o777)==0o755
    assert ((dest/'geoip.dat').stat().st_mode&0o777)==0o644
    repeat=subprocess.run([sys.executable,str(tool),'--archive',str(archive),'--sha256',sha,'--destination',str(dest)],capture_output=True,text=True)
    assert repeat.returncode!=0

def test_core_import_rejects_bad_hash_before_creating_destination(tmp_path):
    import zipfile
    archive=tmp_path/'fixture.zip'
    with zipfile.ZipFile(archive,'w') as z:z.writestr('xray',b'NOT EXECUTED')
    dest=tmp_path/'core';tool=Path(__file__).resolve().parents[1]/'tools/import-core.py'
    r=subprocess.run([sys.executable,str(tool),'--archive',str(archive),'--sha256','0'*64,'--destination',str(dest)],capture_output=True,text=True)
    assert r.returncode!=0 and not dest.exists()
