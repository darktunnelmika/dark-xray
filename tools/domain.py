#!/usr/bin/env python3
"""Provision or renew DARK panel TLS with rollback-safe activation.

Certificate issuance uses Certbot HTTP-01 without stopping unrelated web servers.
The panel/guard configuration and the copied certificate pair are restored if the
new DARK service cannot be activated successfully.
"""
from __future__ import annotations

import argparse
import errno
import json
import os
import pwd
import re
import shutil
import socket
import sqlite3
import ssl
import subprocess
import tempfile
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


def copy_pair(lineage:Path):
    cert,key=lineage/'fullchain.pem',lineage/'privkey.pem'
    context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);context.load_cert_chain(cert,key)
    TLS_DIR.mkdir(mode=0o750,exist_ok=True);os.chmod(TLS_DIR,0o750);os.chown(TLS_DIR,0,pwd.getpwnam('darkxray').pw_gid)
    write(TLS_DIR/'cert.pem',cert.read_bytes());write(TLS_DIR/'key.pem',key.read_bytes())
    return str(TLS_DIR/'cert.pem'),str(TLS_DIR/'key.pem')


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


def _activate_tls(previous_config:dict,candidate:dict,previous_guard:dict|None,guard_candidate:dict|None,pair_snapshot:dict[str,bytes|None]):
    guard_existed=GUARD.exists();guard_was_active=_service_active('dark-xray-guard.service')
    try:
        if guard_candidate is not None:write_json(GUARD,guard_candidate,0o600)
        write_json(CONF/'config.json',candidate)
        _restart_checked('dark-xray.service')
        if guard_was_active:_restart_checked('dark-xray-guard.service')
    except Exception as ex:
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
    lineage=Path(state['lineage']);snapshot=_pair_snapshot()
    try:
        copy_pair(lineage);_restart_checked('dark-xray.service')
    except Exception as ex:
        try:_restore_pair(snapshot);_restart_checked('dark-xray.service')
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

    pair_snapshot=_pair_snapshot();previous_guard=json.loads(GUARD.read_text()) if GUARD.exists() else None
    try:
        cert,key=copy_pair(lineage)
        candidate=_activation_config(previous_config,domain,a.port,cert,key)
        guard_candidate=_activation_guard(previous_guard,candidate)
        _activate_tls(previous_config,candidate,previous_guard,guard_candidate,pair_snapshot)
    except BaseException:
        # _activate_tls performs runtime rollback. Failures before activation only
        # copied cert files, so restore the previous pair here too.
        if json.loads((CONF/'config.json').read_text())==previous_config:
            try:_restore_pair(pair_snapshot)
            except Exception:pass
        raise

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
