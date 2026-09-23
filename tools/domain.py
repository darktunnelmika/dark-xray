#!/usr/bin/env python3
"""Provision or renew DARK panel TLS with rollback-safe activation.

Certificate issuance uses Certbot HTTP-01 without stopping unrelated web servers.
The panel/guard configuration and the copied certificate pair are restored if the
new DARK service cannot be activated successfully.
"""
from __future__ import annotations

import argparse
import errno
import http.client
import io
import ipaddress
import json
import os
import pwd
import re
import shutil
import socket
import sqlite3
import ssl
import stat
import subprocess
import tempfile
import time
from pathlib import Path
from urllib.parse import urlsplit

CONF=Path('/etc/dark-xray');SOURCE=CONF/'tls-source.json';GUARD=CONF/'guard.json';TLS_DIR=CONF/'tls'
DOMAIN_RE=re.compile(r'(?=.{1,253}$)(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,63}')
EMAIL_RE=re.compile(r'[^\s@]+@[^\s@]+\.[^\s@]+')


def write(path:Path,raw:bytes,mode:int=0o640):
    group=pwd.getpwnam('darkxray').pw_gid;path.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent,delete=False) as f:
        tmp=Path(f.name);f.write(raw);f.flush();os.fsync(f.fileno())
    try:
        os.chmod(tmp,mode);os.chown(tmp,0,group);os.replace(tmp,path)
    finally:
        if tmp.exists():tmp.unlink(missing_ok=True)


def write_json(path:Path,value:dict,mode:int=0o640):
    write(path,(json.dumps(value,indent=2)+'\n').encode(),mode)


def _port_available(port:int)->bool:
    attempts=0
    for family,address in ((socket.AF_INET,'0.0.0.0'),(socket.AF_INET6,'::')):
        s=None
        try:
            s=socket.socket(family,socket.SOCK_STREAM);attempts+=1
            if family==socket.AF_INET6:
                try:s.setsockopt(socket.IPPROTO_IPV6,socket.IPV6_V6ONLY,1)
                except OSError:pass
            s.bind((address,port))
        except OSError as ex:
            if ex.errno in (errno.EADDRINUSE,errno.EACCES):return False
            if family==socket.AF_INET6 and ex.errno in (errno.EAFNOSUPPORT,errno.EADDRNOTAVAIL):continue
            raise
        finally:
            if s is not None:s.close()
    return attempts>0


def _service_active(name:str)->bool:
    return subprocess.run(['systemctl','is-active','--quiet',name],check=False).returncode==0


def _restart_checked(name:str):
    subprocess.run(['systemctl','restart',name],check=True)
    subprocess.run(['systemctl','is-active','--quiet',name],check=True)


def _pair_snapshot()->dict[str,bytes|None]:
    return {name:(TLS_DIR/name).read_bytes() if (TLS_DIR/name).is_file() else None for name in ('cert.pem','key.pem')}


def _restore_pair(snapshot:dict[str,bytes|None]):
    TLS_DIR.mkdir(mode=0o750,parents=True,exist_ok=True);os.chmod(TLS_DIR,0o750);os.chown(TLS_DIR,0,pwd.getpwnam('darkxray').pw_gid)
    for name,raw in snapshot.items():
        path=TLS_DIR/name
        if raw is None:path.unlink(missing_ok=True)
        else:write(path,raw,0o640)


def _client_tls_context()->ssl.SSLContext:
    # System trust, date/chain validation and SAN hostname matching; no insecure
    # fallback or trust of the supplied leaf as a CA. Tests use a private CA only.
    context=ssl.create_default_context()
    context.hostname_checks_common_name=False
    return context


def _read_tls_source(path:Path,limit:int)->bytes:
    # Follow Certbot live symlinks, but never block on a FIFO or read a device.
    fd=os.open(path,os.O_RDONLY|os.O_NONBLOCK)
    with os.fdopen(fd,'rb') as f:
        if not stat.S_ISREG(os.fstat(f.fileno()).st_mode):raise ValueError('TLS source must be a regular file')
        raw=f.read(limit+1)
    if len(raw)>limit:raise ValueError('TLS source file is too large')
    return raw


def _prepare_pair(lineage:Path,domain:str)->tuple[bytes,bytes,bytes]:
    """Verify the exact source snapshot in memory BEFORE changing active files.

    A full client/server handshake also checks the key, chain, purpose and time.
    Certbot's live/ symlinks are intentionally supported. No public connection is
    needed: MemoryBIO carries only the two local OpenSSL engines' TLS records.
    """
    if not DOMAIN_RE.fullmatch(domain):raise ValueError('Invalid TLS domain')
    cert=_read_tls_source(lineage/'fullchain.pem',1024*1024)
    key=_read_tls_source(lineage/'privkey.pem',65536)
    server_context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    with tempfile.TemporaryDirectory(prefix='dark-tls-validate-') as tmp:
        cert_path=Path(tmp)/'cert.pem';key_path=Path(tmp)/'key.pem'
        for path,data in ((cert_path,cert),(key_path,key)):
            fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
            with os.fdopen(fd,'wb') as f:f.write(data)
        # An empty password callback rejects an encrypted key without prompting.
        server_context.load_cert_chain(cert_path,key_path,password=lambda:'')
    incoming,outgoing=ssl.MemoryBIO(),ssl.MemoryBIO()
    server_in,server_out=ssl.MemoryBIO(),ssl.MemoryBIO()
    client=_client_tls_context().wrap_bio(incoming,outgoing,server_hostname=domain)
    server=server_context.wrap_bio(server_in,server_out,server_side=True)
    client_done=server_done=False
    for _ in range(32):
        if not client_done:
            try:client.do_handshake();client_done=True
            except ssl.SSLWantReadError:pass
        server_in.write(outgoing.read())
        if not server_done:
            try:server.do_handshake();server_done=True
            except ssl.SSLWantReadError:pass
        incoming.write(server_out.read())
        if client_done and server_done:
            return cert,key,client.getpeercert(binary_form=True)
    raise ssl.SSLError('Local certificate verification did not complete')


def _copy_pair_bytes(pair:tuple[bytes,bytes,bytes]):
    cert,key,_=pair
    TLS_DIR.mkdir(mode=0o750,parents=True,exist_ok=True)
    os.chmod(TLS_DIR,0o750);os.chown(TLS_DIR,0,pwd.getpwnam('darkxray').pw_gid)
    write(TLS_DIR/'cert.pem',cert);write(TLS_DIR/'key.pem',key)
    return str(TLS_DIR/'cert.pem'),str(TLS_DIR/'key.pem')


def copy_pair(lineage:Path,domain:str):
    pair=_prepare_pair(lineage,domain);snapshot=_pair_snapshot()
    try:return _copy_pair_bytes(pair)
    except BaseException:
        _restore_pair(snapshot)
        raise


def _leaf_der(raw:bytes)->bytes:
    end=b'-----END CERTIFICATE-----'
    return ssl.PEM_cert_to_DER_cert(raw[:raw.index(end)+len(end)].decode('ascii'))


class _BufferedResponse:
    def __init__(self,raw:bytes):self.raw=raw
    def makefile(self,*args,**kwargs):return io.BytesIO(self.raw)


def _verify_local_panel(config:dict,expected_der:bytes|None=None,timeout:float=10.0):
    """Observe the local listener with public SNI/Host, NOT provider DNS/WAN.

    A service-active flag or an unrelated/stale listener cannot substitute for
    valid TLS, the exact candidate leaf and the DARK health response. All socket
    reads share one deadline and a bounded response size. No redirects/proxies.
    """
    if not 0<timeout<=30:raise ValueError('Local verification timeout must be within 0..30 seconds')
    origin=urlsplit(str(config.get('public_origin','')))
    port=int(config.get('bind_port',2087));scheme=origin.scheme
    if (scheme not in ('http','https') or not origin.hostname or origin.username
        or origin.password or origin.path not in ('','/') or origin.query or origin.fragment
        or not 1024<=port<=65535 or any(c in origin.netloc for c in ('\r','\n'))):
        raise ValueError('Invalid local panel origin')
    bind=ipaddress.ip_address(str(config.get('bind_host','127.0.0.1')))
    target=('::1' if bind.version==6 else '127.0.0.1') if bind.is_unspecified else str(bind)
    context=None
    has_cert=bool(config.get('tls_certificate'));has_key=bool(config.get('tls_private_key'))
    if has_cert!=has_key or (expected_der is not None and not has_cert):
        raise ValueError('Expected TLS listener is not fully configured')
    # A previous reverse-proxied panel can have an HTTP local listener while its
    # public origin is HTTPS. Only rollback may probe that configured local HTTP;
    # candidate activation always supplies expected_der and requires direct TLS.
    if has_cert:
        if scheme!='https':raise ValueError('TLS listener requires HTTPS origin')
        if not DOMAIN_RE.fullmatch(origin.hostname):raise ValueError('Invalid TLS domain')
        if expected_der is None:
            expected_der=_leaf_der(Path(config['tls_certificate']).read_bytes())
        context=_client_tls_context()
    deadline=time.monotonic()+timeout;last_error='not_ready'
    while time.monotonic()<deadline:
        try:
            with socket.create_connection((target,port),timeout=max(.001,deadline-time.monotonic())) as raw:
                conn=context.wrap_socket(raw,server_hostname=origin.hostname) if context else raw
                try:
                    if context and conn.getpeercert(binary_form=True)!=expected_der:
                        raise ValueError('Listener still serves a different certificate')
                    conn.settimeout(max(.001,deadline-time.monotonic()))
                    conn.sendall(('GET /health HTTP/1.1\r\nHost: '+origin.netloc+
                                  '\r\nConnection: close\r\nUser-Agent: darkxray-tls-check\r\n\r\n').encode('ascii'))
                    received=bytearray()
                    while True:
                        left=deadline-time.monotonic()
                        if left<=0:raise TimeoutError('Local HTTPS deadline exceeded')
                        conn.settimeout(left);chunk=conn.recv(4096)
                        if not chunk:break
                        received.extend(chunk)
                        if len(received)>65536:raise ValueError('Local health response too large')
                    response=http.client.HTTPResponse(_BufferedResponse(bytes(received)))
                    response.begin()
                    if response.status!=200:raise ValueError('Local health status is not successful')
                    payload=response.read()  # already bounded by the raw response limit
                    if len(payload)>8192:raise ValueError('Local health body too large')
                    body=json.loads(payload)
                    if not isinstance(body,dict) or body.get('service')!='DARK XRAY' or body.get('mode')!='standalone':
                        raise ValueError('Local listener is not the DARK panel')
                    if scheme=='https' and config.get('secure_cookie'):
                        hsts=response.getheader('Strict-Transport-Security','')
                        if not re.search(r'(?:^|;)\s*max-age=[1-9][0-9]*(?:;|$)',hsts,re.I):
                            raise ValueError('Local HTTPS response lacks HSTS')
                    return
                finally:
                    if conn is not raw:conn.close()
        except (OSError,ValueError,http.client.HTTPException) as ex:
            last_error=type(ex).__name__
        left=deadline-time.monotonic()
        if left>0:time.sleep(min(.1,left))
    raise RuntimeError('Local panel verification failed: '+last_error)


def _activation_config(previous:dict,domain:str,port:int,cert:str,key:str)->dict:
    candidate=dict(previous);old_port=int(previous.get('bind_port',2087));xray_api=int(previous.get('xray_api_port',10085))
    candidate.update(public_origin=f'https://{domain}:{port}',bind_host='0.0.0.0',bind_port=port,
                     secure_cookie=True,tls_certificate=cert,tls_private_key=key)
    protected=set(map(int,candidate.get('protected_ports',[])));protected.discard(old_port);protected.update({22,port,xray_api})
    candidate['protected_ports']=sorted(protected);return candidate


def _activation_guard(previous_guard:dict|None,candidate:dict)->dict|None:
    if previous_guard is None:return None
    value=dict(previous_guard);protected=set(map(int,candidate.get('protected_ports',[])))
    value['protected_ports']=sorted(protected)
    value['allowed_ports']=[int(x) for x in value.get('allowed_ports',[]) if int(x) not in protected]
    if not value['allowed_ports']:raise SystemExit('TLS obtained, but activation refused: IP Guard would have no approved data ports')
    return value


def _restore_runtime(previous_config:dict,previous_guard:dict|None,guard_existed:bool,pair_snapshot:dict[str,bytes|None],guard_was_active:bool):
    write_json(CONF/'config.json',previous_config)
    if guard_existed and previous_guard is not None:write_json(GUARD,previous_guard,0o600)
    elif not guard_existed:GUARD.unlink(missing_ok=True)
    _restore_pair(pair_snapshot)
    _restart_checked('dark-xray.service')
    if guard_was_active:_restart_checked('dark-xray-guard.service')
    _verify_local_panel(previous_config)


def _activate_tls(previous_config:dict,candidate:dict,previous_guard:dict|None,guard_candidate:dict|None,pair_snapshot:dict[str,bytes|None],pair:tuple[bytes,bytes,bytes]):
    guard_existed=GUARD.exists();guard_was_active=_service_active('dark-xray-guard.service')
    try:
        _copy_pair_bytes(pair)
        if guard_candidate is not None:write_json(GUARD,guard_candidate,0o600)
        write_json(CONF/'config.json',candidate)
        _restart_checked('dark-xray.service')
        if guard_was_active:_restart_checked('dark-xray-guard.service')
        _verify_local_panel(candidate,pair[2])
    except BaseException as ex:
        try:_restore_runtime(previous_config,previous_guard,guard_existed,pair_snapshot,guard_was_active)
        except Exception as rollback_ex:
            raise SystemExit('CRITICAL: TLS activation failed and the previous DARK service could not be reactivated: '+type(rollback_ex).__name__) from ex
        raise SystemExit('TLS activation failed; previous DARK config, guard state and certificate pair were restored') from ex


def _renewal_source_active(state:dict,config:dict)->bool:
    try:origin=urlsplit(str(config.get('public_origin','')))
    except Exception:return False
    return bool(origin.scheme=='https' and origin.hostname==state.get('domain') and
                str(config.get('tls_certificate',''))==str(TLS_DIR/'cert.pem') and
                str(config.get('tls_private_key',''))==str(TLS_DIR/'key.pem'))


def _renew():
    if not SOURCE.is_file() or SOURCE.is_symlink():raise SystemExit('No trusted active DARK TLS renewal source is configured')
    state=json.loads(SOURCE.read_text());config=json.loads((CONF/'config.json').read_text())
    if not _renewal_source_active(state,config):raise SystemExit('Stored TLS renewal source is not the active DARK panel TLS domain')
    lineage=Path(state['lineage'])
    # Invalid new material must not overwrite files OR restart a working panel.
    pair=_prepare_pair(lineage,str(state['domain']));snapshot=_pair_snapshot()
    try:
        _copy_pair_bytes(pair);_restart_checked('dark-xray.service')
        _verify_local_panel(config,pair[2])
    except BaseException as ex:
        try:
            _restore_pair(snapshot);_restart_checked('dark-xray.service')
            _verify_local_panel(config)
        except Exception as rollback_ex:raise SystemExit('CRITICAL: renewed TLS pair failed and old pair could not be reactivated') from rollback_ex
        raise SystemExit('Renewed certificate could not activate; previous certificate pair was restored') from ex


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--domain');p.add_argument('--email');p.add_argument('--port',type=int,default=2087)
    p.add_argument('--agree-tos',action='store_true');p.add_argument('--renew',action='store_true');a=p.parse_args()
    if os.geteuid()!=0:raise SystemExit('Root is required for certificate provisioning, not normal panel operation')
    if a.renew:_renew();return
    domain=str(a.domain or '').strip().lower();email=str(a.email or '').strip()
    if not DOMAIN_RE.fullmatch(domain):raise SystemExit('Use a valid ASCII domain without protocol, wildcard, path or port')
    if not EMAIL_RE.fullmatch(email) or not a.agree_tos:raise SystemExit('--email and explicit --agree-tos are required for Certbot')
    if not 1024<=a.port<=65535:raise SystemExit('Use a panel port from 1024 to 65535; data port 443 stays available')
    certbot=shutil.which('certbot')
    if not certbot:raise SystemExit('Install certbot first. This command does not install packages silently.')
    previous_config=json.loads((CONF/'config.json').read_text());old_port=int(previous_config.get('bind_port',2087))
    blocked=set(map(int,previous_config.get('protected_ports',[])))-{old_port}
    if a.port in blocked|{22,int(previous_config.get('xray_api_port',10085))}:raise SystemExit('Panel/core/SSH collision')
    if a.port!=old_port and not _port_available(a.port):raise SystemExit(f'Panel port {a.port} is already in use on this host')
    if not _port_available(80):raise SystemExit('Port 80 is busy; Certbot standalone HTTP-01 cannot run without interrupting another service')
    if subprocess.run(['systemctl','cat','certbot.timer'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).returncode:
        raise SystemExit('Packaged certbot.timer is required; configure it before certificate provisioning')
    subprocess.run(['systemctl','enable','--now','certbot.timer'],check=True)
    with sqlite3.connect('file:/var/lib/dark-xray/dark.sqlite3?mode=ro',uri=True) as db:
        if any(json.loads(r[0])['port']==a.port for r in db.execute('SELECT body FROM core_inbounds')):
            raise SystemExit('Panel port is already assigned to a data inbound')
    certname='dark-xray-'+domain;lineage=Path('/etc/letsencrypt/live')/certname
    subprocess.run([certbot,'certonly','--non-interactive','--standalone','--preferred-challenges','http',
                    '--http-01-port','80','--agree-tos','--email',email,'--cert-name',certname,'-d',domain],check=True)

    pair=_prepare_pair(lineage,domain)
    pair_snapshot=_pair_snapshot();previous_guard=json.loads(GUARD.read_text()) if GUARD.exists() else None
    cert,key=str(TLS_DIR/'cert.pem'),str(TLS_DIR/'key.pem')
    candidate=_activation_config(previous_config,domain,a.port,cert,key)
    guard_candidate=_activation_guard(previous_guard,candidate)
    _activate_tls(previous_config,candidate,previous_guard,guard_candidate,pair_snapshot,pair)

    write_json(SOURCE,{'lineage':str(lineage),'domain':domain},0o600)
    hooks=Path('/etc/letsencrypt/renewal-hooks/deploy');hooks.mkdir(parents=True,exist_ok=True)
    hook=hooks/'dark-xray-panel'
    content=f'''#!/bin/sh
set -eu
if [ "${{RENEWED_LINEAGE:-}}" = "{lineage}" ]; then
 /opt/dark-xray/.venv/bin/python /opt/dark-xray/tools/domain.py --renew
fi
'''
    write(hook,content.encode(),0o750)
    panel_path=str(candidate.get('panel_path','/'));print('Panel TLS enabled:',candidate['public_origin']+(panel_path if panel_path!='/' else '')+'/')
    print('Renewal hook installed. Renewal restarts DARK and can interrupt Xray sessions.')

if __name__=='__main__':main()
