"""Certificate/rollback acceptance using private CA and real local HTTPS.

No Certbot, systemd, public DNS, system trust store or installed service is touched.
Only UID/GID assignment in temporary directories and service restart commands
are replaced. Crypto, file replacement, TLS handshakes and Hub HTTP are real.
"""
from __future__ import annotations

import dataclasses
import importlib.util
import json
import os
import socket
import ssl
import subprocess
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest
import uvicorn
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

ROOT=Path(__file__).resolve().parents[1]
DOMAIN='panel.example.test'


def load_domain():
    spec=importlib.util.spec_from_file_location('domain_tls_subject',ROOT/'tools/domain.py')
    mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def certs(tmp_path):
    now=datetime.now(timezone.utc)
    root_key=ec.generate_private_key(ec.SECP256R1())
    name=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'DARK disposable root')])
    ca=(x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(root_key.public_key())
        .serial_number(x509.random_serial_number()).not_valid_before(now-timedelta(days=60))
        .not_valid_after(now+timedelta(days=365)).add_extension(x509.BasicConstraints(ca=True,path_length=1),True)
        .add_extension(x509.KeyUsage(True,False,False,False,False,True,True,False,False),True)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(root_key.public_key()),False)
        .sign(root_key,hashes.SHA256()))
    ca_file=tmp_path/'ca.pem';ca_file.write_bytes(ca.public_bytes(serialization.Encoding.PEM))
    def issue(label, *, dns=DOMAIN, san=True, start=-1, end=30, client_only=False):
        directory=tmp_path/label;directory.mkdir()
        key=ec.generate_private_key(ec.SECP256R1())
        builder=(x509.CertificateBuilder().subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,DOMAIN)]))
            .issuer_name(name).public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now+timedelta(days=start)).not_valid_after(now+timedelta(days=end))
            .add_extension(x509.BasicConstraints(ca=False,path_length=None),True)
            .add_extension(x509.KeyUsage(True,False,False,False,False,False,False,False,False),True)
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()),False)
            .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(root_key.public_key()),False)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.CLIENT_AUTH if client_only else ExtendedKeyUsageOID.SERVER_AUTH]),False))
        if san:builder=builder.add_extension(x509.SubjectAlternativeName([x509.DNSName(dns)]),False)
        leaf=builder.sign(root_key,hashes.SHA256())
        (directory/'fullchain.pem').write_bytes(leaf.public_bytes(serialization.Encoding.PEM))
        (directory/'privkey.pem').write_bytes(key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
        return directory
    return SimpleNamespace(ca=ca_file,issue=issue)


@pytest.fixture
def domain(tmp_path,monkeypatch,certs):
    mod=load_domain();conf=tmp_path/'conf';conf.mkdir()
    mod.CONF=conf;mod.TLS_DIR=conf/'tls';mod.SOURCE=conf/'tls-source.json';mod.GUARD=conf/'guard.json'
    mod.pwd=SimpleNamespace(getpwnam=lambda name:SimpleNamespace(pw_gid=os.getgid()))
    fake_os=SimpleNamespace(**{key:getattr(os,key) for key in dir(os)})
    def chown(path,*args,**kwargs):
        assert Path(path).resolve().is_relative_to(tmp_path.resolve())
    fake_os.chown=chown;mod.os=fake_os
    factory=mod._client_tls_context
    def context():
        value=factory();value.load_verify_locations(cafile=str(certs.ca));return value
    monkeypatch.setattr(mod,'_client_tls_context',context)
    mod._service_active=lambda name:False
    return mod


def snapshot(mod):
    return {p.name:p.read_bytes() for p in mod.TLS_DIR.iterdir()} if mod.TLS_DIR.exists() else {}


def test_client_context_requires_trust_and_san():
    context=load_domain()._client_tls_context()
    assert context.verify_mode==ssl.CERT_REQUIRED
    assert context.check_hostname is True
    assert context.hostname_checks_common_name is False


@pytest.mark.parametrize('options',[
    {},{'dns':'*.example.test'},{'dns':'PANEL.EXAMPLE.TEST'},
])
def test_valid_certificate_is_verified_then_copied(domain,certs,options):
    material=certs.issue('valid',**options)
    paths=domain.copy_pair(material,DOMAIN)
    assert paths==(str(domain.TLS_DIR/'cert.pem'),str(domain.TLS_DIR/'key.pem'))
    assert (domain.TLS_DIR/'cert.pem').read_bytes()==(material/'fullchain.pem').read_bytes()
    assert (domain.TLS_DIR/'key.pem').read_bytes()==(material/'privkey.pem').read_bytes()
    assert (domain.TLS_DIR/'key.pem').stat().st_mode&0o777==0o640


@pytest.mark.parametrize('options',[
    {'start':-30,'end':-1},{'start':1,'end':30},{'dns':'wrong.example.test'},
    {'san':False},{'dns':'*.other.test'},{'dns':'p*.example.test'},{'client_only':True},
])
def test_bad_certificate_never_changes_active_pair(domain,certs,monkeypatch,options):
    domain.copy_pair(certs.issue('old'),DOMAIN);before=snapshot(domain)
    bad=certs.issue('bad',**options)
    monkeypatch.setattr(domain,'write',lambda *a,**k:pytest.fail('write before certificate accepted'))
    with pytest.raises((ssl.SSLError,ValueError)):domain.copy_pair(bad,DOMAIN)
    assert snapshot(domain)==before


def test_wildcard_does_not_match_multiple_labels(domain,certs):
    material=certs.issue('wild',dns='*.example.test')
    with pytest.raises(ssl.SSLError):domain.copy_pair(material,'two.panel.example.test')
    assert not domain.TLS_DIR.exists()


def test_untrusted_chain_cannot_install(certs,tmp_path):
    mod=load_domain();mod.TLS_DIR=tmp_path/'must-not-exist'
    with pytest.raises(ssl.SSLError):mod.copy_pair(certs.issue('untrusted'),DOMAIN)
    assert not mod.TLS_DIR.exists()


@pytest.mark.parametrize('bad_file', ['fullchain.pem','privkey.pem','mismatched','oversized'])
def test_invalid_material_does_not_overwrite_files(domain,certs,bad_file):
    domain.copy_pair(certs.issue('old'),DOMAIN);before=snapshot(domain)
    bad=certs.issue('bad')
    if bad_file=='mismatched':
        (bad/'privkey.pem').write_bytes((certs.issue('other')/'privkey.pem').read_bytes())
    elif bad_file=='oversized':(bad/'fullchain.pem').write_bytes(b'A'*(1024*1024+1))
    else:(bad/bad_file).write_bytes(b'not valid PEM')
    with pytest.raises((ValueError,ssl.SSLError)):domain.copy_pair(bad,DOMAIN)
    assert snapshot(domain)==before


def test_certbot_style_symlinks_are_supported(domain,certs,tmp_path):
    archive=certs.issue('archive');live=tmp_path/'live';live.mkdir()
    for name in ('fullchain.pem','privkey.pem'):(live/name).symlink_to(archive/name)
    domain.copy_pair(live,DOMAIN)
    assert (domain.TLS_DIR/'cert.pem').read_bytes()==(archive/'fullchain.pem').read_bytes()


def test_installed_bytes_are_the_validated_snapshot(domain,certs,monkeypatch):
    good=certs.issue('good');prepared=domain._prepare_pair(good,DOMAIN)
    (good/'fullchain.pem').write_bytes(b'changed after verification')
    domain._copy_pair_bytes(prepared)
    assert (domain.TLS_DIR/'cert.pem').read_bytes()==prepared[0]


def test_partial_pair_write_rolls_back(domain,certs,monkeypatch):
    domain.copy_pair(certs.issue('old'),DOMAIN);before=snapshot(domain)
    new=certs.issue('new');original=domain.write;calls=0
    def write(path,*args,**kwargs):
        nonlocal calls
        calls+=1
        if calls==2:raise OSError('simulated second file failure')
        return original(path,*args,**kwargs)
    monkeypatch.setattr(domain,'write',write)
    with pytest.raises(OSError):domain.copy_pair(new,DOMAIN)
    assert snapshot(domain)==before


def config_for(mod,port,https=True):
    return {'public_origin':f'https://{DOMAIN}:{port}' if https else f'http://127.0.0.1:{port}',
        'bind_host':'127.0.0.1','bind_port':port,'secure_cookie':https,'panel_path':'/private-panel',
        'tls_certificate':str(mod.TLS_DIR/'cert.pem') if https else '',
        'tls_private_key':str(mod.TLS_DIR/'key.pem') if https else '',
        'xray_api_port':10085,'protected_ports':[22,port,10085]}


@contextmanager
def http_fixture(material=None, *, status=200, body=None, hsts=True, delay=0):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_GET(self):
            time.sleep(delay)
            payload=json.dumps(body if body is not None else {'service':'DARK XRAY','mode':'standalone'}).encode()
            try:
                self.send_response(status)
                if hsts:self.send_header('Strict-Transport-Security','max-age=31536000')
                self.send_header('Content-Length',str(len(payload)));self.end_headers();self.wfile.write(payload)
            except (BrokenPipeError,ConnectionResetError,ssl.SSLError):pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler);server.daemon_threads=True
    if material:
        ctx=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(material/'fullchain.pem',material/'privkey.pem')
        server.socket=ctx.wrap_socket(server.socket,server_side=True)
    worker=threading.Thread(target=server.serve_forever,kwargs={'poll_interval':.02});worker.start()
    try:yield server.server_port
    finally:
        server.shutdown();server.server_close();worker.join(3);assert not worker.is_alive()


def test_real_https_accepts_only_current_leaf_and_health(domain,certs):
    material=certs.issue('new');pair=domain._prepare_pair(material,DOMAIN)
    with http_fixture(material) as port:
        domain._verify_local_panel(config_for(domain,port),pair[2],timeout=1)


@pytest.mark.parametrize('mode',['expired','wrong-name','stale','wrong-service','bad-status','no-hsts','slow','plain'])
def test_bad_local_listener_never_reports_activation_success(domain,certs,mode):
    wanted=certs.issue('wanted');pair=domain._prepare_pair(wanted,DOMAIN);kwargs={};actual=wanted
    if mode=='expired':actual=certs.issue('expired',start=-30,end=-1)
    elif mode=='wrong-name':actual=certs.issue('wrong',dns='other.example.test')
    elif mode=='stale':actual=certs.issue('old')
    elif mode=='wrong-service':kwargs['body']={'service':'NOT DARK','mode':'standalone'}
    elif mode=='bad-status':kwargs['status']=503
    elif mode=='no-hsts':kwargs['hsts']=False
    elif mode=='slow':kwargs['delay']=.3
    elif mode=='plain':actual=None
    with http_fixture(actual,**kwargs) as port:
        with pytest.raises(RuntimeError):domain._verify_local_panel(config_for(domain,port),pair[2],timeout=.15)


def test_unreachable_loopback_is_failure(domain):
    sock=socket.socket();sock.bind(('127.0.0.1',0));port=sock.getsockname()[1];sock.close()
    with pytest.raises(RuntimeError):domain._verify_local_panel(config_for(domain,port,False),timeout=.1)


class PanelService:
    """Restart shim: full real DARK HTTP/TLS server, temporary database/identity."""
    def __init__(self,mod,tmp_path):
        self.mod=mod;self.root=tmp_path;self.current=None;self.calls=[];self.fail_next=False;self.no_listener_next=False
    def stop(self):
        if self.current:
            server,worker,store=self.current
            server.should_exit=True;worker.join(5)
            assert not worker.is_alive(),'test must not leave a Hub thread running'
            store.close();self.current=None
    def __call__(self,name):
        self.calls.append(name)
        if name!='dark-xray.service':return
        self.stop()
        if getattr(self,'interrupt_next',False):
            self.interrupt_next=False;raise KeyboardInterrupt('operator interrupted activation')
        if self.fail_next:
            self.fail_next=False;raise subprocess.CalledProcessError(1,['systemctl','restart',name])
        if self.no_listener_next:
            self.no_listener_next=False;return  # active flag alone is insufficient
        from auth import Auth
        from core import Config,CoreEngine
        from dark_policy import Store
        from manager import Manager
        from server import make_app
        values=json.loads((self.mod.CONF/'config.json').read_text())
        cfg=Config(**values,xray_binary=str(self.root/'unused-xray'),xray_assets=str(self.root),test_engine=True)
        store=Store(self.root/'hub.sqlite3');engine=CoreEngine(cfg,store,self.root/'runtime')
        manager=Manager(store,engine);auth=Auth(store,self.root/'secret.key')
        app=make_app(manager,auth,background=False)
        server=uvicorn.Server(uvicorn.Config(app,host=cfg.bind_host,port=cfg.bind_port,log_level='error',
            access_log=False,ws='none',ssl_certfile=cfg.tls_certificate or None,ssl_keyfile=cfg.tls_private_key or None))
        worker=threading.Thread(target=server.run,daemon=True);self.current=(server,worker,store);worker.start()
        until=time.monotonic()+3
        while not server.started and worker.is_alive() and time.monotonic()<until:time.sleep(.01)
        assert server.started


@pytest.fixture
def service(domain,tmp_path,monkeypatch):
    value=PanelService(domain,tmp_path);monkeypatch.setattr(domain,'_restart_checked',value)
    original=domain._verify_local_panel
    monkeypatch.setattr(domain,'_verify_local_panel',lambda config,expected_der=None:original(config,expected_der,timeout=.4))
    yield value
    value.stop()


def unused_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0));return sock.getsockname()[1]


@pytest.mark.parametrize('failure',['none','restart','health'])
def test_full_hub_tls_activation_and_http_rollback(domain,certs,service,failure):
    port=unused_port();previous=config_for(domain,port,False)
    domain.write_json(domain.CONF/'config.json',previous);service('dark-xray.service')
    domain._verify_local_panel(previous)
    pair=domain._prepare_pair(certs.issue('new'),DOMAIN)
    candidate=domain._activation_config(previous,DOMAIN,port,str(domain.TLS_DIR/'cert.pem'),str(domain.TLS_DIR/'key.pem'))
    # Same loopback fixture instead of exposing the disposable test on 0.0.0.0.
    candidate['bind_host']='127.0.0.1'
    old_guard={'allowed_ports':[2020],'protected_ports':[22,port,10085]}
    domain.write_json(domain.GUARD,old_guard,0o600)
    domain._service_active=lambda name:True
    if failure=='restart':service.fail_next=True
    if failure=='health':service.no_listener_next=True
    args=(previous,candidate,old_guard,old_guard,domain._pair_snapshot(),pair)
    if failure=='none':
        domain._activate_tls(*args)
        domain._verify_local_panel(candidate,pair[2])
        assert json.loads((domain.CONF/'config.json').read_text())==candidate
    else:
        with pytest.raises(SystemExit,match='restored'):domain._activate_tls(*args)
        assert json.loads((domain.CONF/'config.json').read_text())==previous
        assert json.loads(domain.GUARD.read_text())==old_guard
        assert snapshot(domain)=={}
        domain._verify_local_panel(previous)


@pytest.mark.parametrize('mode',['success','invalid','restart-failed','health-failed','interrupted'])
def test_renewal_preserves_old_service_or_serves_verified_new_pair(domain,certs,service,mode):
    old=certs.issue('old');domain.copy_pair(old,DOMAIN);before=snapshot(domain)
    cfg=config_for(domain,unused_port());domain.write_json(domain.CONF/'config.json',cfg)
    new=certs.issue('new',**({'start':-30,'end':-1} if mode=='invalid' else {}))
    state={'lineage':str(new),'domain':DOMAIN};domain.write_json(domain.SOURCE,state,0o600)
    source_before=domain.SOURCE.read_bytes();config_before=(domain.CONF/'config.json').read_bytes()
    service('dark-xray.service');domain._verify_local_panel(cfg);calls_before=len(service.calls)
    if mode=='restart-failed':service.fail_next=True
    if mode=='health-failed':service.no_listener_next=True
    if mode=='interrupted':service.interrupt_next=True
    if mode=='invalid':
        with pytest.raises(ssl.SSLError):domain._renew()
        assert len(service.calls)==calls_before
        assert snapshot(domain)==before
    elif mode!='success':
        with pytest.raises(SystemExit,match='previous certificate pair was restored'):domain._renew()
        assert snapshot(domain)==before
    else:
        domain._renew()
        assert (domain.TLS_DIR/'cert.pem').read_bytes()==(new/'fullchain.pem').read_bytes()
    assert domain.SOURCE.read_bytes()==source_before
    assert (domain.CONF/'config.json').read_bytes()==config_before
    domain._verify_local_panel(cfg)


def test_failed_rollback_is_critical_not_success(domain,certs,monkeypatch):
    domain.copy_pair(certs.issue('old'),DOMAIN)
    cfg=config_for(domain,unused_port());domain.write_json(domain.CONF/'config.json',cfg)
    new=certs.issue('new');domain.write_json(domain.SOURCE,{'lineage':str(new),'domain':DOMAIN},0o600)
    def fail(*args):raise OSError('simulated unavailable service')
    monkeypatch.setattr(domain,'_restart_checked',fail)
    with pytest.raises(SystemExit,match='CRITICAL'):domain._renew()


def test_wrong_renewal_source_never_reads_or_restarts(domain,certs,monkeypatch):
    cfg=config_for(domain,unused_port());domain.write_json(domain.CONF/'config.json',cfg)
    domain.write_json(domain.SOURCE,{'lineage':'/not-read','domain':'other.example.test'})
    monkeypatch.setattr(domain,'_prepare_pair',lambda *a:pytest.fail('read before source accepted'))
    with pytest.raises(SystemExit,match='not the active'):domain._renew()


def test_fifo_tls_source_rejected_without_blocking(domain,certs):
    material=certs.issue('fifo');(material/'fullchain.pem').unlink();os.mkfifo(material/'fullchain.pem')
    with pytest.raises(ValueError,match='regular'):domain.copy_pair(material,DOMAIN)
    assert not domain.TLS_DIR.exists()


@pytest.mark.parametrize('timeout',[0,-1,float('nan'),float('inf')])
def test_invalid_probe_budget_rejected_before_network(domain,monkeypatch,timeout):
    monkeypatch.setattr(socket,'create_connection',lambda *a,**kw:pytest.fail('invalid budget reached network'))
    with pytest.raises(ValueError):domain._verify_local_panel(config_for(domain,2443),b'leaf',timeout=timeout)


def test_expected_leaf_cannot_downgrade_to_plain_http(domain):
    with pytest.raises(ValueError,match='not fully configured'):
        domain._verify_local_panel(config_for(domain,2443,False),b'leaf',timeout=.1)


def test_rollback_probe_supports_previous_reverse_proxy_local_http(domain,service):
    cfg=config_for(domain,unused_port(),False)
    cfg.update(public_origin=f'https://{DOMAIN}',secure_cookie=True)
    domain.write_json(domain.CONF/'config.json',cfg);service('dark-xray.service')
    domain._verify_local_panel(cfg)


def test_repeated_renewal_failure_preserves_source_and_reports_critical(domain,certs,monkeypatch):
    domain.copy_pair(certs.issue('old'),DOMAIN)
    cfg=config_for(domain,unused_port());domain.write_json(domain.CONF/'config.json',cfg)
    new=certs.issue('new');domain.write_json(domain.SOURCE,{'lineage':str(new),'domain':DOMAIN})
    source=domain.SOURCE.read_bytes()
    monkeypatch.setattr(domain,'_restart_checked',lambda *a:None)
    monkeypatch.setattr(domain,'_verify_local_panel',lambda *a:(_ for _ in ()).throw(RuntimeError('no valid listener')))
    with pytest.raises(SystemExit,match='CRITICAL'):domain._renew()
    assert domain.SOURCE.read_bytes()==source


def test_interrupted_partial_copy_restores_old_bytes(domain,certs,monkeypatch):
    domain.copy_pair(certs.issue('old'),DOMAIN);before=snapshot(domain)
    new=certs.issue('new');write=domain.write;count=0
    def interrupted(path,*args,**kwargs):
        nonlocal count
        count+=1
        if count==2:raise KeyboardInterrupt('interrupted before key replacement')
        return write(path,*args,**kwargs)
    monkeypatch.setattr(domain,'write',interrupted)
    with pytest.raises(KeyboardInterrupt):domain.copy_pair(new,DOMAIN)
    assert snapshot(domain)==before
