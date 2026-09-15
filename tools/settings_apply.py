#!/usr/bin/env python3
"""Apply root-owned DARK XRAY runtime settings staged from the web UI.

The unprivileged panel can save a validated desired runtime profile in its own
SQLite database, but it cannot edit /etc/dark-xray/config.json or obtain TLS
certificates. This tool is the explicit root boundary that applies that staged
profile atomically and restarts services only when requested by an operator.
"""
from __future__ import annotations

import argparse
import ipaddress
import json
import os
import pwd
import re
import sqlite3
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

DOMAIN_RE = re.compile(r'(?=.{1,253}$)(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,63}')
EMAIL_RE = re.compile(r'[^\s@]+@[^\s@]+\.[^\s@]+')
RUNTIME_KEYS = {'access_mode','bind_port','public_address','panel_path','poll_seconds','core_autostart','domain','acme_email'}


def _runtime_row(db_path: Path, current: dict) -> dict:
    with sqlite3.connect(f'file:{db_path}?mode=ro', uri=True) as db:
        row = db.execute("SELECT body FROM core_sections WHERE name='runtime'").fetchone()
    if not row:
        raise SystemExit('No staged runtime settings found. Save Network / Domain settings in the panel first.')
    value = json.loads(row[0])
    if not isinstance(value, dict) or set(value) - RUNTIME_KEYS:
        raise SystemExit('Staged runtime settings have an invalid shape')
    # Backward-compatible hydration for installations that saved Settings V2 before
    # panel_path existed. The next save persists the full shape.
    value.setdefault('panel_path',str(current.get('panel_path','/')))
    return value


def _inbound_ports(db_path: Path) -> set[int]:
    out: set[int] = set()
    with sqlite3.connect(f'file:{db_path}?mode=ro', uri=True) as db:
        for (raw,) in db.execute('SELECT body FROM core_inbounds'):
            try:
                port = int(json.loads(raw).get('port'))
            except Exception:
                continue
            if 1 <= port <= 65535:
                out.add(port)
    return out


def validate_desired(value: dict, current: dict, inbound_ports: set[int]) -> dict:
    v = dict(value)
    if v.get('access_mode') not in ('ssh','domain_tls'):
        raise ValueError('access_mode must be ssh or domain_tls')
    for key, low, high in [('bind_port',1024,65535),('poll_seconds',1,3600)]:
        if type(v.get(key)) is not int or not low <= v[key] <= high:
            raise ValueError('Invalid '+key)
    if type(v.get('core_autostart')) is not bool:
        raise ValueError('core_autostart must be boolean')
    panel_path=str(v.get('panel_path','/')).strip()
    if panel_path!='/' and panel_path.endswith('/'):panel_path=panel_path.rstrip('/')
    if panel_path!='/' and not re.fullmatch(r'/(?:[A-Za-z0-9_-]{1,64})(?:/[A-Za-z0-9_-]{1,64})*',panel_path):
        raise ValueError('Invalid panel URI path')
    if len(panel_path)>200:raise ValueError('Panel URI path is too long')
    v['panel_path']=panel_path
    address = v.get('public_address','')
    if not isinstance(address,str) or not address or any(c in address for c in '/?#@ \r\n\t'):
        raise ValueError('public_address must be a plain IP or DNS name')
    port = v['bind_port']
    xray_api = int(current.get('xray_api_port',10085))
    if port in {22,xray_api} or port in inbound_ports:
        raise ValueError('Panel port collides with SSH, Xray API, or a data inbound')
    domain = str(v.get('domain','')).strip().lower()
    email = str(v.get('acme_email','')).strip()
    if v['access_mode']=='domain_tls':
        if not DOMAIN_RE.fullmatch(domain):
            raise ValueError('Domain + TLS mode requires a valid ASCII domain')
        if not EMAIL_RE.fullmatch(email):
            raise ValueError('Domain + TLS mode requires a valid ACME email')
    else:
        domain='';email=''
    v['domain']=domain;v['acme_email']=email
    return v


def build_plan(current: dict, desired: dict) -> dict:
    origin = urlsplit(str(current.get('public_origin','http://127.0.0.1:2087')))
    current_domain = origin.hostname or ''
    cert = Path(str(current.get('tls_certificate') or ''))
    key = Path(str(current.get('tls_private_key') or ''))
    tls_ready = origin.scheme=='https' and current_domain==desired.get('domain','') and cert.is_file() and key.is_file()
    actual_mode = 'domain_tls' if origin.scheme=='https' else 'ssh'
    actual = {
        'access_mode':actual_mode,
        'bind_port':int(current.get('bind_port',2087)),
        'public_address':str(current.get('public_address','')),
        'panel_path':str(current.get('panel_path','/')),
        'poll_seconds':int(current.get('poll_seconds',5)),
        'core_autostart':bool(current.get('core_autostart',False)),
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
        if mode is None:
            mode = (path.stat().st_mode & 0o777) if path.exists() else 0o640
        os.chmod(name,mode);os.chown(name,0,group);os.replace(name,path)
    finally:
        if os.path.exists(name):os.unlink(name)


def _sync_guard(config: dict, old_panel_port: int, new_panel_port: int) -> None:
    guard = Path('/etc/dark-xray/guard.json')
    protected = set(map(int,config.get('protected_ports',[])))
    protected.discard(old_panel_port)
    protected.update({22,new_panel_port,int(config.get('xray_api_port',10085))})
    config['protected_ports']=sorted(protected)
    if not guard.exists(): return
    value=json.loads(guard.read_text())
    value['protected_ports']=config['protected_ports']
    value['allowed_ports']=[int(x) for x in value.get('allowed_ports',[]) if int(x) not in protected]
    if not value['allowed_ports']:
        raise SystemExit('Refusing apply: IP Guard would have no approved data ports after panel-port change')
    _atomic_json(guard,value,0o600)


def apply_settings(config_path: Path, db_path: Path, desired: dict, plan: dict) -> None:
    current=json.loads(config_path.read_text())
    old_port=int(current.get('bind_port',2087));new_port=desired['bind_port']
    current.update(public_address=desired['public_address'],panel_path=desired['panel_path'],poll_seconds=desired['poll_seconds'],core_autostart=desired['core_autostart'])
    if desired['access_mode']=='ssh':
        current.update(public_origin=f'http://127.0.0.1:{new_port}',bind_host='127.0.0.1',bind_port=new_port,
                       secure_cookie=False,tls_certificate='',tls_private_key='')
        _sync_guard(current,old_port,new_port);_atomic_json(config_path,current)
        subprocess.run(['systemctl','restart','dark-xray.service'],check=True)
        if subprocess.run(['systemctl','is-active','--quiet','dark-xray-guard.service']).returncode==0:
            subprocess.run(['systemctl','restart','dark-xray-guard.service'],check=True)
        return
    if plan['tls_ready']:
        domain=desired['domain']
        current.update(public_origin=f'https://{domain}:{new_port}',bind_host='0.0.0.0',bind_port=new_port,secure_cookie=True)
        _sync_guard(current,old_port,new_port);_atomic_json(config_path,current)
        subprocess.run(['systemctl','restart','dark-xray.service'],check=True)
        if subprocess.run(['systemctl','is-active','--quiet','dark-xray-guard.service']).returncode==0:
            subprocess.run(['systemctl','restart','dark-xray-guard.service'],check=True)
        return
    _atomic_json(config_path,current)
    tool=Path('/opt/dark-xray/tools/domain.py')
    subprocess.run(['/opt/dark-xray/.venv/bin/python',str(tool),'--domain',desired['domain'],'--email',desired['acme_email'],
                    '--port',str(new_port),'--agree-tos'],check=True)


def main() -> None:
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--config',type=Path,default=Path('/etc/dark-xray/config.json'))
    p.add_argument('--data',type=Path,default=Path('/var/lib/dark-xray'))
    p.add_argument('--dry-run',action='store_true')
    args=p.parse_args();db_path=args.data/'dark.sqlite3'
    current=json.loads(args.config.read_text())
    desired=validate_desired(_runtime_row(db_path,current),current,_inbound_ports(db_path))
    plan=build_plan(current,desired);print(json.dumps(plan,indent=2))
    if args.dry_run:return
    if os.geteuid()!=0:raise SystemExit('Root is required to apply staged runtime settings')
    if not plan['pending']:
        print('No privileged runtime changes are pending.');return
    apply_settings(args.config,db_path,desired,plan)
    print('Staged DARK runtime settings applied successfully.')

if __name__=='__main__':main()
