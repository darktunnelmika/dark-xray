#!/usr/bin/env python3
"""Apply root-owned DARK XRAY runtime settings staged from the web UI.

The web process only stages validated intent in SQLite. This root boundary performs
an additional host-level preflight, updates config/guard files atomically, and
rolls back the previous files if service activation fails.
"""
from __future__ import annotations

import argparse
import errno
import json
import os
import pwd
import re
import socket
import sqlite3
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

DOMAIN_RE = re.compile(r'(?=.{1,253}$)(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,63}')
EMAIL_RE = re.compile(r'[^\s@]+@[^\s@]+\.[^\s@]+')
RUNTIME_KEYS = {'access_mode','bind_port','public_address','panel_path','poll_seconds','core_autostart','domain','acme_email'}
GUARD_PATH = Path('/etc/dark-xray/guard.json')


def _runtime_row(db_path: Path, current: dict) -> dict:
    with sqlite3.connect(f'file:{db_path}?mode=ro', uri=True) as db:
        row = db.execute("SELECT body FROM core_sections WHERE name='runtime'").fetchone()
    if not row:
        raise SystemExit('No staged runtime settings found. Save Network / Domain settings in the panel first.')
    value = json.loads(row[0])
    if not isinstance(value, dict) or set(value) - RUNTIME_KEYS:
        raise SystemExit('Staged runtime settings have an invalid shape')
    value.setdefault('panel_path',str(current.get('panel_path','/')))
    return value


def _inbound_ports(db_path: Path) -> set[int]:
    out: set[int] = set()
    with sqlite3.connect(f'file:{db_path}?mode=ro', uri=True) as db:
        for (raw,) in db.execute('SELECT body FROM core_inbounds'):
            try: port = int(json.loads(raw).get('port'))
            except Exception: continue
            if 1 <= port <= 65535: out.add(port)
    return out


def validate_desired(value: dict, current: dict, inbound_ports: set[int]) -> dict:
    v = dict(value)
    if v.get('access_mode') not in ('ssh','domain_tls'):
        raise ValueError('access_mode must be ssh or domain_tls')
    for key, low, high in [('bind_port',1024,65535),('poll_seconds',1,3600)]:
        if type(v.get(key)) is not int or not low <= v[key] <= high: raise ValueError('Invalid '+key)
    if type(v.get('core_autostart')) is not bool: raise ValueError('core_autostart must be boolean')
    panel_path=str(v.get('panel_path','/')).strip()
    if panel_path!='/' and panel_path.endswith('/'): panel_path=panel_path.rstrip('/')
    if panel_path!='/' and not re.fullmatch(r'/(?:[A-Za-z0-9_-]{1,64})(?:/[A-Za-z0-9_-]{1,64})*',panel_path):
        raise ValueError('Invalid panel URI path')
    if len(panel_path)>200: raise ValueError('Panel URI path is too long')
    first_segment=panel_path.strip('/').split('/',1)[0].lower() if panel_path!='/' else ''
    if first_segment in {'api','assets','sub','node','health'}: raise ValueError('Panel URI path conflicts with a reserved DARK endpoint')
    v['panel_path']=panel_path
    address = v.get('public_address','')
    if not isinstance(address,str) or not address or len(address)>253 or any(c in address for c in '/?#@ \r\n\t'):
        raise ValueError('public_address must be a plain IP or DNS name')
    port = v['bind_port'];xray_api = int(current.get('xray_api_port',10085))
    if port in {22,xray_api} or port in inbound_ports: raise ValueError('Panel port collides with SSH, Xray API, or a data inbound')
    domain = str(v.get('domain','')).strip().lower();email = str(v.get('acme_email','')).strip()
    if v['access_mode']=='domain_tls':
        if not DOMAIN_RE.fullmatch(domain): raise ValueError('Domain + TLS mode requires a valid ASCII domain')
        if not EMAIL_RE.fullmatch(email): raise ValueError('Domain + TLS mode requires a valid ACME email')
    else: domain='';email=''
    v['domain']=domain;v['acme_email']=email
    return v


def build_plan(current: dict, desired: dict) -> dict:
    origin = urlsplit(str(current.get('public_origin','http://127.0.0.1:2087')))
    current_domain = origin.hostname or ''
    cert = Path(str(current.get('tls_certificate') or ''));key = Path(str(current.get('tls_private_key') or ''))
    tls_ready = origin.scheme=='https' and current_domain==desired.get('domain','') and cert.is_file() and key.is_file()
    actual_mode = 'domain_tls' if origin.scheme=='https' else 'ssh'
    actual = {
        'access_mode':actual_mode,'bind_port':int(current.get('bind_port',2087)),
        'public_address':str(current.get('public_address','')),'panel_path':str(current.get('panel_path','/')),
        'poll_seconds':int(current.get('poll_seconds',5)),'core_autostart':bool(current.get('core_autostart',False)),
        'domain':current_domain if actual_mode=='domain_tls' else '',
    }
    pending = {k:{'from':actual.get(k),'to':desired.get(k)} for k in actual if actual.get(k)!=desired.get(k)}
    return {'actual':actual,'desired':desired,'pending':pending,'requires_acme':desired['access_mode']=='domain_tls' and not tls_ready,'tls_ready':tls_ready}


def _atomic_json(path: Path, value: dict, mode: int | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try: group = pwd.getpwnam('darkxray').pw_gid
    except KeyError: group = 0
    fd, name = tempfile.mkstemp(prefix='.dark-settings-', dir=path.parent)
    try:
        with os.fdopen(fd,'w') as f:
            json.dump(value,f,indent=2);f.write('\n');f.flush();os.fsync(f.fileno())
        if mode is None: mode = (path.stat().st_mode & 0o777) if path.exists() else 0o640
        os.chmod(name,mode);os.chown(name,0,group);os.replace(name,path)
    finally:
        if os.path.exists(name): os.unlink(name)


def _port_available(port:int) -> bool:
    """Best-effort host listener preflight for a *new* nonprivileged panel port."""
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
            if ex.errno in (errno.EADDRINUSE,errno.EACCES): return False
            if family==socket.AF_INET6 and ex.errno in (errno.EAFNOSUPPORT,errno.EADDRNOTAVAIL): continue
            raise
        finally:
            if s is not None:s.close()
    return attempts>0


def _guard_candidate(config:dict,old_panel_port:int,new_panel_port:int,guard_path:Path=GUARD_PATH)->dict|None:
    protected=set(map(int,config.get('protected_ports',[])));protected.discard(old_panel_port)
    protected.update({22,new_panel_port,int(config.get('xray_api_port',10085))});config['protected_ports']=sorted(protected)
    if not guard_path.exists(): return None
    value=json.loads(guard_path.read_text());value['protected_ports']=config['protected_ports']
    value['allowed_ports']=[int(x) for x in value.get('allowed_ports',[]) if int(x) not in protected]
    if not value['allowed_ports']: raise SystemExit('Refusing apply: IP Guard would have no approved data ports after panel-port change')
    return value


def _service_active(name:str)->bool:
    return subprocess.run(['systemctl','is-active','--quiet',name],check=False).returncode==0


def _restart_checked(name:str)->None:
    subprocess.run(['systemctl','restart',name],check=True)
    subprocess.run(['systemctl','is-active','--quiet',name],check=True)


def _restore_runtime(config_path:Path,previous:dict,guard_previous:dict|None,guard_existed:bool,guard_was_active:bool)->None:
    _atomic_json(config_path,previous)
    if guard_existed and guard_previous is not None:_atomic_json(GUARD_PATH,guard_previous,0o600)
    elif not guard_existed and GUARD_PATH.exists():GUARD_PATH.unlink()
    _restart_checked('dark-xray.service')
    if guard_was_active:_restart_checked('dark-xray-guard.service')


def _activate_candidate(config_path:Path,previous:dict,candidate:dict,guard_candidate:dict|None)->None:
    guard_existed=GUARD_PATH.exists();guard_previous=json.loads(GUARD_PATH.read_text()) if guard_existed else None
    guard_was_active=_service_active('dark-xray-guard.service')
    try:
        _atomic_json(config_path,candidate)
        if guard_candidate is not None:_atomic_json(GUARD_PATH,guard_candidate,0o600)
        _restart_checked('dark-xray.service')
        if guard_was_active:_restart_checked('dark-xray-guard.service')
    except Exception as ex:
        try:_restore_runtime(config_path,previous,guard_previous,guard_existed,guard_was_active)
        except Exception as rollback_ex:
            raise SystemExit('CRITICAL: runtime apply failed and rollback could not reactivate the previous service: '+type(rollback_ex).__name__) from ex
        raise SystemExit('Runtime apply failed; previous DARK configuration was restored and reactivated') from ex


def _candidate_from_desired(current:dict,desired:dict,plan:dict)->tuple[dict,dict|None]:
    candidate=dict(current);old_port=int(current.get('bind_port',2087));new_port=desired['bind_port']
    candidate.update(public_address=desired['public_address'],panel_path=desired['panel_path'],poll_seconds=desired['poll_seconds'],core_autostart=desired['core_autostart'])
    if desired['access_mode']=='ssh':
        candidate.update(public_origin=f'http://127.0.0.1:{new_port}',bind_host='127.0.0.1',bind_port=new_port,
                         secure_cookie=False,tls_certificate='',tls_private_key='')
    elif plan['tls_ready']:
        candidate.update(public_origin=f"https://{desired['domain']}:{new_port}",bind_host='0.0.0.0',bind_port=new_port,secure_cookie=True)
    else:
        raise ValueError('A certificate must be provisioned before building the local TLS activation candidate')
    return candidate,_guard_candidate(candidate,old_port,new_port)


def apply_settings(config_path: Path, db_path: Path, desired: dict, plan: dict) -> None:
    current=json.loads(config_path.read_text());old_port=int(current.get('bind_port',2087));new_port=desired['bind_port']
    if new_port!=old_port and not _port_available(new_port):
        raise SystemExit(f'Refusing apply: panel port {new_port} is already in use on this host')
    if desired['access_mode']=='ssh' or plan['tls_ready']:
        candidate,guard_candidate=_candidate_from_desired(current,desired,plan)
        _activate_candidate(config_path,current,candidate,guard_candidate);return

    # First-time TLS issuance is delegated to the certbot boundary. Do not pre-write
    # staged values: if ACME fails the current on-disk/runtime panel remains intact.
    tool=Path('/opt/dark-xray/tools/domain.py')
    subprocess.run(['/opt/dark-xray/.venv/bin/python',str(tool),'--domain',desired['domain'],'--email',desired['acme_email'],
                    '--port',str(new_port),'--agree-tos'],check=True)
    # domain.py has safely activated TLS. Apply the remaining staged values in a
    # second rollback-safe activation; on failure the newly working TLS config is kept.
    tls_current=json.loads(config_path.read_text())
    tls_plan=build_plan(tls_current,desired);tls_plan['tls_ready']=True
    candidate,guard_candidate=_candidate_from_desired(tls_current,desired,tls_plan)
    _activate_candidate(config_path,tls_current,candidate,guard_candidate)


def main() -> None:
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--config',type=Path,default=Path('/etc/dark-xray/config.json'))
    p.add_argument('--data',type=Path,default=Path('/var/lib/dark-xray'))
    p.add_argument('--dry-run',action='store_true')
    args=p.parse_args();db_path=args.data/'dark.sqlite3';current=json.loads(args.config.read_text())
    desired=validate_desired(_runtime_row(db_path,current),current,_inbound_ports(db_path));plan=build_plan(current,desired)
    print(json.dumps(plan,indent=2))
    if args.dry_run:return
    if os.geteuid()!=0:raise SystemExit('Root is required to apply staged runtime settings')
    if not plan['pending']:print('No privileged runtime changes are pending.');return
    apply_settings(args.config,db_path,desired,plan)
    print('Staged DARK runtime settings applied successfully.')

if __name__=='__main__':main()
