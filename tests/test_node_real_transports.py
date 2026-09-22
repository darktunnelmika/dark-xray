"""Opt-in two-node transport acceptance using Hub-exported raw subscriptions.

Real Xray and Agent HTTPS; Hub handlers use TestClient. TLS verification stays
on with the disposable CA explicitly trusted. REALITY's handshake target is a
private loopback TLS 1.3 fixture, never an external website. No product method,
transport, counter or core is mocked. Negative packets must not hit the target.
This matrix is not proof of every option combination, WAN or client-app support.
"""
from __future__ import annotations

import base64
import contextlib
import copy
import http.server
import json
import os
import socket
import ssl
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, unquote, urlsplit

import pytest
from fastapi.testclient import TestClient
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

import nodes as nodes_module
from auth import Auth
from core import Config, CoreEngine
from dark_policy import Actor, Store
from manager import Manager
from server import make_app
from test_node_real_data_plane import (
    real_binary, real_agent, tls_material, EMAIL, CLIENT_UUID, TOKEN, PASSWORD,
    transfer, target_server, free_port, stable_identity, usage, meter,
)

ROOT = Path(__file__).resolve().parents[1]
CONTROL_EMAIL = 'transport-control'
CONTROL_UUID = '33333333-3333-4333-8333-333333333333'
CLIENT_PASSWORD = 'Disposable-Transport-Customer-Password!'
CONTROL_PASSWORD = 'Disposable-Transport-Control-Password!'


@dataclass(frozen=True)
class Case:
    protocol: str
    network: str
    security: str
    flow: str = ''

    @property
    def label(self):
        return '-'.join((self.protocol, self.network, self.security)) + ('-vision' if self.flow else '')


CASES = [
    *[Case('vless', net, 'tls') for net in ('tcp', 'raw', 'ws', 'grpc', 'httpupgrade', 'xhttp')],
    Case('vless', 'kcp', 'none'),
    Case('vless', 'tcp', 'reality', 'xtls-rprx-vision'),
    Case('vless', 'grpc', 'reality'), Case('vless', 'xhttp', 'reality'),
    *[Case('vmess', net, 'tls') for net in ('tcp', 'ws', 'grpc')],
    Case('trojan', 'tcp', 'tls'), Case('trojan', 'grpc', 'tls'),
    Case('shadowsocks', 'tcp', 'none'),
]


def b64decode(raw):
    return base64.urlsafe_b64decode(raw + '=' * (-len(raw) % 4))


def decode_link(uri):
    """Decode only the emitted link, never repair it with server-side settings."""
    p = urlsplit(uri)
    if p.scheme == 'vmess':
        d = json.loads(b64decode(uri.removeprefix('vmess://')))
        protocol, address, port = 'vmess', d['add'], int(d['port'])
        user = {'id':d['id'], 'alterId':int(d['aid']), 'security':d['scy']}
        q = {'type':d['net'], 'security':d.get('tls') or 'none', 'sni':d.get('sni',''),
             'path':d.get('path',''), 'serviceName':d.get('path',''), 'host':d.get('host','')}
        for key in ('alpn','fp'): q[key] = d.get(key, '')
        if d.get('allowInsecure'): raise AssertionError('Insecure TLS export refused')
    elif p.scheme == 'ss':
        protocol, address, port = 'shadowsocks', p.hostname, p.port
        method, password = b64decode(p.username).decode().split(':',1)
        user = {'method':method, 'password':password}
        q = {'type':'tcp', 'security':'none'}
    elif p.scheme in ('vless','trojan'):
        protocol, address, port = p.scheme, p.hostname, p.port
        pairs = parse_qs(p.query, keep_blank_values=True)
        assert all(len(v)==1 for v in pairs.values()), 'Ambiguous subscription parameter'
        q = {k:v[0] for k,v in pairs.items()}
        user = ({'id':unquote(p.username or ''), 'encryption':q.get('encryption','none')}
                if protocol=='vless' else {'password':unquote(p.username or '')})
        if q.get('flow'): user['flow'] = q['flow']
    else:
        raise AssertionError('Unsupported test subscription scheme')
    # Prevent a fixture from following an accidentally external exported link.
    assert address == '127.0.0.1' and type(port) is int and 1024 < port <= 65535
    assert q.get('allowInsecure', '0') not in ('1','true')
    return SimpleNamespace(protocol=protocol, address=address, port=port, user=user, query=q)


def outbound_from_link(uri, ca):
    d = decode_link(uri); q = d.query
    if d.protocol in ('vless','vmess'):
        settings = {'vnext':[{'address':d.address, 'port':d.port, 'users':[d.user]}]}
    else:
        settings = {'servers':[{'address':d.address, 'port':d.port, **d.user}]}
    net, sec = q['type'], q['security']
    stream = {'network':net, 'security':sec}
    if net in ('ws','httpupgrade','xhttp'):
        values = {'path':q['path']}
        if net=='ws': values['headers'] = {'Host':q['host']}
        else: values['host'] = q['host']
        if net=='xhttp': values['mode'] = q['mode']
        stream[net+'Settings'] = values
    elif net=='grpc': stream['grpcSettings'] = {'serviceName':q['serviceName']}
    elif net=='kcp': stream['kcpSettings'] = {}  # Only the default mKCP options are in this matrix.
    elif net not in ('tcp','raw'): raise AssertionError('Unsupported exported transport')
    if sec=='tls':
        stream['tlsSettings'] = {'serverName':q['sni'], 'allowInsecure':False,
            'disableSystemRoot':True,
            'certificates':[{'certificateFile':str(ca), 'usage':'verify'}]}
        if q.get('alpn'): stream['tlsSettings']['alpn'] = q['alpn'].split(',')
        if q.get('fp'): stream['tlsSettings']['fingerprint'] = q['fp']
    elif sec=='reality':
        stream['realitySettings'] = {'serverName':q['sni'], 'fingerprint':q['fp'],
            'password':q['pbk'], 'shortId':q['sid'], 'spiderX':q.get('spx','/')}
    else:
        assert sec=='none'
    return {'protocol':d.protocol, 'settings':settings, 'streamSettings':stream}


@contextlib.contextmanager
def transport_client(root, binary, outbound):
    root.mkdir(mode=0o700); port = free_port()
    config = {'log':{'loglevel':'warning'}, 'inbounds':[{'listen':'127.0.0.1','port':port,
        'protocol':'socks','settings':{'auth':'noauth','udp':False}}], 'outbounds':[outbound]}
    # One outbound only: there is no direct route or fallback on the client.
    path = root/'client.json'; path.write_text(json.dumps(config)); path.chmod(0o600)
    subprocess.run([str(binary),'run','-test','-config',str(path)], check=True,
                   capture_output=True, timeout=10)
    with (root/'client.log').open('wb') as log:
        proc = subprocess.Popen([str(binary),'run','-config',str(path)], stdout=log, stderr=subprocess.STDOUT)
        try:
            deadline = time.monotonic()+8
            while True:
                assert proc.poll() is None, 'Xray transport client exited'
                try:
                    with socket.create_connection(('127.0.0.1',port),timeout=.1): break
                except OSError:
                    if time.monotonic()>=deadline: raise
                    time.sleep(.02)
            yield SimpleNamespace(port=port, process=proc)
        finally:
            if proc.poll() is None: proc.terminate()
            try: proc.wait(timeout=5)
            except subprocess.TimeoutExpired: proc.kill(); proc.wait(timeout=5)


class HandshakeTarget(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        # This endpoint is NOT the nonce-bearing customer data target.
        self.send_response(404); self.send_header('Content-Length','0'); self.end_headers()
    def log_message(self, *args): pass


@contextlib.contextmanager
def reality_target(material):
    _, cert, key = material
    server = http.server.ThreadingHTTPServer(('127.0.0.1',0), HandshakeTarget)
    server.daemon_threads = True
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ctx.maximum_version = ssl.TLSVersion.TLSv1_3
    ctx.set_ecdh_curve('X25519'); ctx.set_alpn_protocols(['h2','http/1.1'])
    ctx.load_cert_chain(str(cert),str(key))
    server.socket = ctx.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever,kwargs={'poll_interval':.05}); thread.start()
    try: yield f'127.0.0.1:{server.server_port}'
    finally:
        server.shutdown(); server.server_close(); thread.join(5)
        assert not thread.is_alive()


def server_stream(case, material, target):
    host, cert, key = material
    stream = {'network':case.network, 'security':case.security}
    if case.network in ('ws','httpupgrade','xhttp'):
        opts = {'path':'/dark-transport-check/v3'}
        if case.network=='ws': opts['headers']={'Host':host}
        else: opts['host']=host
        if case.network=='xhttp': opts['mode']='packet-up'
        stream[case.network+'Settings']=opts
    elif case.network=='grpc': stream['grpcSettings']={'serviceName':'dark-transport-check'}
    if case.security=='tls':
        stream['tlsSettings']={'serverName':host, 'alpn':(['h2'] if case.network in ('grpc','xhttp') else ['http/1.1']),
            'certificates':[{'certificateFile':str(cert), 'keyFile':str(key)}]}
    if case.security=='reality':
        private=X25519PrivateKey.generate().private_bytes(serialization.Encoding.Raw,
                serialization.PrivateFormat.Raw,serialization.NoEncryption())
        stream['realitySettings']={'target':target, 'serverNames':[host],
            'privateKey':base64.urlsafe_b64encode(private).decode().rstrip('='), 'shortIds':['a1b2c3d4']}
    return stream


@pytest.fixture
def transport_fleet(request,tmp_path,monkeypatch,tls_material,real_binary):
    case = request.param; ca, materials = tls_material
    trust = ssl.create_default_context(cafile=str(ca))
    assert trust.check_hostname and trust.verify_mode == ssl.CERT_REQUIRED
    monkeypatch.setattr(nodes_module,'ssl',SimpleNamespace(create_default_context=lambda:trust,
        SSLError=ssl.SSLError,SSLContext=ssl.SSLContext))
    with contextlib.ExitStack() as stack:
        target_address = stack.enter_context(reality_target(materials[0])) if case.security=='reality' else ''
        agents = [stack.enter_context(real_agent(tmp_path/f'agent{i}',f'transport-{i}',mat,real_binary.path))
                  for i,mat in enumerate(materials,1)]
        origins={a.origin for a in agents}; original=nodes_module.resolve_origin
        def resolve(origin):
            if origin not in origins: return original(origin)
            p=urlsplit(origin); return origin,p.hostname,p.port,('127.0.0.1',)
        monkeypatch.setattr(nodes_module,'resolve_origin',resolve)
        root=tmp_path/'hub';root.mkdir(mode=0o700)
        store=Store(root/'dark.sqlite3');stack.callback(store.close)
        cfg=Config(core_autostart=False,test_engine=False,xray_binary=str(real_binary.path),
                   xray_assets=str(real_binary.path.parent),xray_api_port=free_port())
        engine=CoreEngine(cfg,store,root/'runtime');stack.callback(engine.close)
        manager=Manager(store,engine);stack.callback(manager.close)
        auth=Auth(store,root/'secret.key');auth.bootstrap('dark',PASSWORD)
        app=make_app(manager,auth,background=False);reg=app.state.nodes
        http=stack.enter_context(TestClient(app,base_url=cfg.public_origin))
        def api(path,body=None,method=None):
            response=http.request(method or ('POST' if body is not None else 'GET'),path,json=body)
            assert 200<=response.status_code<300,(path,response.status_code,response.text[:250])
            return response.json()
        http.headers['X-Dark-CSRF']=api('/api/auth/login',{'username':'dark','password':PASSWORD})['csrf']
        ids=[];hosts=[]
        for i,(node,mat) in enumerate(zip(agents,materials,strict=True),1):
            node.data_port=free_port()
            settings={'decryption':'none'} if case.protocol=='vless' else {}
            if case.protocol=='shadowsocks':settings={'method':'aes-128-gcm','network':'tcp'}
            # Both REALITY listeners may borrow this single local TLS handshake target.
            stream=server_stream(case,materials[0] if case.security=='reality' else mat,target_address)
            iid=api('/api/inbounds',{'remark':case.label,'tag':f'transport-{i}',
                'listen':'127.0.0.1','port':node.data_port,'protocol':case.protocol,'enable':True,
                'settings':settings,'streamSettings':stream,'sniffing':{},'panelMeta':{'deployLocal':False}})['id']
            ids.append(iid);node.iid=iid
            reg.put(node.id,node.id,node.origin,TOKEN,True,[iid],urlsplit(node.origin).hostname);reg.probe(node.id)
            hosts.append({'inboundId':iid,'address':'127.0.0.1','port':node.data_port,'remark':node.id,
                          'runtime':'node:'+node.id,'enable':True})
        manager.owner_put(Actor('dark','owner',{}),'dark',name='Transport QA',allowed=ids)
        for email,identity,password in ((EMAIL,CLIENT_UUID,CLIENT_PASSWORD),(CONTROL_EMAIL,CONTROL_UUID,CONTROL_PASSWORD)):
            api('/api/clients',{'owner':'dark','client':{'email':email,'id':identity,'password':password,
                'flow':case.flow,'limitIp':0,'limitHwid':0,'totalGB':100*1024**3,
                'expiryTime':int((time.time()+86400)*1000)},'inboundIds':ids})
        api('/api/settings/hosts',{'value':hosts},'PUT')
        for node in agents:
            assert api('/api/nodes/'+node.id+'/sync',{})['desired_state_applied'] is True
            reg.probe(node.id)
        def links(email):
            url=api('/api/clients/'+email+'/links')['subscription_url']
            response=http.get(url+'?format=raw');assert response.status_code==200,response.text[:250]
            lines=response.text.splitlines();decoded={decode_link(uri).port:uri for uri in lines if uri.strip()}
            assert len(lines)==len(decoded)==2 and set(decoded)=={n.data_port for n in agents}
            return decoded
        raw=links(EMAIL);control_raw=links(CONTROL_EMAIL)
        target=stack.enter_context(target_server());outbounds=[];clients=[];controls=[]
        for i,node in enumerate(agents):
            uri=raw[node.data_port];decoded=decode_link(uri)
            assert (decoded.protocol,decoded.query['type'],decoded.query['security'])==(case.protocol,case.network,case.security)
            assert decoded.user.get('id',decoded.user.get('password'))==(CLIENT_UUID if case.protocol in ('vless','vmess') else CLIENT_PASSWORD)
            outbound=outbound_from_link(uri,ca);outbounds.append(outbound)
            clients.append(stack.enter_context(transport_client(tmp_path/f'client-{i}',real_binary.path,outbound)))
            controls.append(stack.enter_context(transport_client(tmp_path/f'control-{i}',real_binary.path,
                outbound_from_link(control_raw[node.data_port],ca))))
        f=SimpleNamespace(case=case,agents=agents,clients=clients,controls=controls,outbounds=outbounds,
            reg=reg,store=store,engine=engine,api=api,http=http,target=target,root=tmp_path,
            binary=real_binary,ca=ca,evidence={})
        try: yield f
        finally:
            report={'case':case.label,'real_xray':True,'agent_https':True,'client_tls_verification_disabled':False,
                'binary_sha256':real_binary.digest,'binary_version':real_binary.version,
                'provider_wan_tested':False,'hub_http':'in-process TestClient','automatic_scheduler_tested':False,
                'all_option_combinations_tested':False,'external_reality_target_contacted':False,**f.evidence}
            out=ROOT/'qa/node-real-transports';out.mkdir(parents=True,exist_ok=True)
            name=request.node.name.replace('/','_').replace('[','-').replace(']','')
            (out/(name+'.json')).write_text(json.dumps(report,indent=2)+'\n')


@pytest.mark.parametrize('transport_fleet',CASES,indirect=True,ids=lambda c:c.label)
def test_generated_transport_routes_accounts_rejects_wrong_user_and_disables_only_customer(transport_fleet):
    f=transport_fleet;identity=stable_identity(f)
    for client in f.clients:transfer(client.port,f.target)
    before=meter(f);assert before[0]>0 and before[2]==before[0]
    assert all(up>0 and down>0 for _,up,down in before[1])
    assert meter(f)==before,'real counters were charged twice'
    pids=[a.engine.process.pid for a in f.agents]
    for node in f.agents:assert f.api('/api/nodes/'+node.id+'/sync',{})['desired_state_applied'] is True
    assert [a.engine.process.pid for a in f.agents]==pids
    for i,outbound in enumerate(f.outbounds):
        bad=copy.deepcopy(outbound)
        if bad['protocol'] in ('vless','vmess'):bad['settings']['vnext'][0]['users'][0]['id']=str(uuid.uuid4())
        else:bad['settings']['servers'][0]['password']='Wrong-Disposable-Password!'
        with transport_client(f.root/f'wrong-user-{i}',f.binary.path,bad) as client:
            transfer(client.port,f.target,allowed=False)
        transfer(f.clients[i].port,f.target)
    f.api('/api/clients/'+EMAIL+'/action',{'action':'disable'})
    for node in f.agents:assert f.api('/api/nodes/'+node.id+'/sync',{})['desired_state_applied'] is True
    for client in f.clients:transfer(client.port,f.target,allowed=False)
    for client in f.controls:transfer(client.port,f.target)
    assert stable_identity(f)==identity and usage(f)[0]>=before[0]
    assert not f.api('/api/clients/'+CONTROL_EMAIL)['block_reasons']
    f.evidence.update(both_generated_links_transferred=True,wrong_user_rejected_on_both=True,
        manual_disable_enforced_on_both=True,control_customer_remained_usable=True,
        duplicate_counter_idempotent=True,unchanged_sync_preserved_pids=True,charged_bytes=before[0])


@pytest.mark.parametrize('fault',['untrusted-ca','wrong-sni','wrong-path'])
@pytest.mark.parametrize('transport_fleet',[Case('vless','ws','tls')],indirect=True,ids=lambda c:c.label)
def test_tls_and_websocket_negative_controls(transport_fleet,fault):
    f=transport_fleet;bad=copy.deepcopy(f.outbounds[0])
    if fault=='untrusted-ca':bad['streamSettings']['tlsSettings']['certificates']=[]
    elif fault=='wrong-sni':bad['streamSettings']['tlsSettings']['serverName']='wrong.example.test'
    else:bad['streamSettings']['wsSettings']['path']='/not-the-configured-path'
    with transport_client(f.root/'negative-tls',f.binary.path,bad) as client:transfer(client.port,f.target,allowed=False)
    for client in f.clients:transfer(client.port,f.target)
    f.evidence['negative_control_rejected']=fault


@pytest.mark.parametrize('fault',['wrong-key','wrong-short-id'])
@pytest.mark.parametrize('transport_fleet',[Case('vless','tcp','reality','xtls-rprx-vision')],indirect=True,ids=lambda c:c.label)
def test_reality_authentication_negative_controls(transport_fleet,fault):
    f=transport_fleet;bad=copy.deepcopy(f.outbounds[0]);rt=bad['streamSettings']['realitySettings']
    if fault=='wrong-key':
        key=X25519PrivateKey.generate().public_key().public_bytes(serialization.Encoding.Raw,serialization.PublicFormat.Raw)
        rt['password']=base64.urlsafe_b64encode(key).decode().rstrip('=')
    else:rt['shortId']='11223344'
    with transport_client(f.root/'negative-reality',f.binary.path,bad) as client:transfer(client.port,f.target,allowed=False)
    for client in f.clients:transfer(client.port,f.target)
    f.evidence['negative_control_rejected']=fault
