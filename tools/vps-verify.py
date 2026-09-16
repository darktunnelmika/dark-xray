#!/usr/bin/env python3
"""Read-only production-readiness gate for an installed DARK XRAY VPS.

This command never changes services, firewall state, configuration or the DB. It
returns exit 0 only when hard local readiness checks pass. Network throughput,
packet-flow proof and real client connectivity remain explicit manual/real-core
validation gates.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import socket
import sqlite3
import ssl
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))
from core import Config
from guard_bridge import BrokerClient


def run_text(args:list[str],timeout:float=10)->tuple[int,str]:
    try:
        cp=subprocess.run(args,capture_output=True,text=True,timeout=timeout,check=False)
        return cp.returncode,(cp.stdout or cp.stderr or '').strip()
    except (OSError,subprocess.TimeoutExpired) as ex:
        return 127,type(ex).__name__+': '+str(ex)


def sha256(path:Path)->str:
    h=hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda:f.read(1024*1024),b''):h.update(chunk)
    return h.hexdigest()


def panel_status(cfg:Config,path:str)->int:
    origin=urlsplit(cfg.public_origin)
    target='127.0.0.1' if cfg.bind_host in {'0.0.0.0','::'} else cfg.bind_host
    raw=socket.create_connection((target,cfg.bind_port),timeout=3)
    try:
        sock=raw
        if origin.scheme=='https':
            ctx=ssl.create_default_context();sock=ctx.wrap_socket(raw,server_hostname=origin.hostname)
        req=f'GET {path} HTTP/1.1\r\nHost: {origin.netloc}\r\nConnection: close\r\nUser-Agent: darkxray-vps-verify\r\n\r\n'.encode()
        sock.sendall(req);head=b''
        while b'\r\n' not in head and len(head)<4096:
            part=sock.recv(512)
            if not part:break
            head+=part
        first=head.split(b'\r\n',1)[0].decode('ascii','replace').split()
        return int(first[1]) if len(first)>=2 and first[1].isdigit() else 0
    finally:
        try:raw.close()
        except Exception:pass


def staged_runtime(cfg:Config,row)->bool:
    if not row:return False
    desired=json.loads(row[0])
    return any((
        int(desired.get('bind_port',cfg.bind_port))!=cfg.bind_port,
        str(desired.get('public_address',cfg.public_address))!=cfg.public_address,
        str(desired.get('panel_path',cfg.panel_path))!=cfg.panel_path,
        int(desired.get('poll_seconds',cfg.poll_seconds))!=cfg.poll_seconds,
        bool(desired.get('core_autostart',cfg.core_autostart))!=cfg.core_autostart,
    ))


def main()->None:
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--config',type=Path,required=True)
    ap.add_argument('--data',type=Path,required=True)
    ap.add_argument('--json-only',action='store_true')
    a=ap.parse_args()
    result={'version':(ROOT/'VERSION').read_text(encoding='utf-8').strip() if (ROOT/'VERSION').is_file() else 'unknown',
            'ready':False,'checks':{},'warnings':[],'failures':[],'changes_made':False}
    checks=result['checks'];warn=result['warnings'];fail=result['failures']

    def hard(name:str,ok:bool,detail):
        checks[name]={'ok':bool(ok),'detail':detail}
        if not ok:fail.append(name+': '+str(detail))

    def soft(name:str,ok:bool,detail):
        checks[name]={'ok':bool(ok),'detail':detail}
        if not ok:warn.append(name+': '+str(detail))

    try:
        cfg=Config.load(a.config);hard('configuration',True,'loaded with ownership/permission validation')
    except Exception as ex:
        hard('configuration',False,type(ex).__name__+': '+str(ex));cfg=None

    db_path=a.data/'dark.sqlite3';runtime_row=None;ipguard={'mode':'observe'}
    if cfg:
        try:
            safe=db_path.is_file() and not db_path.is_symlink()
            if not safe:raise RuntimeError('database missing or unsafe')
            with sqlite3.connect(db_path.resolve().as_uri()+'?mode=ro',uri=True,timeout=10) as db:
                quick=db.execute('PRAGMA quick_check').fetchone()[0]
                runtime_row=db.execute("SELECT body FROM core_sections WHERE name='runtime'").fetchone()
                row=db.execute("SELECT body FROM core_sections WHERE name='ipguard'").fetchone()
                if row:ipguard=json.loads(row[0])
                nodes={'total':0,'enabled':0,'fresh':0,'errors':0}
                table=db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='remote_nodes'").fetchone()
                if table:
                    now=__import__('time').time()
                    rows=db.execute('SELECT enabled,last_seen,last_error FROM remote_nodes').fetchall();nodes['total']=len(rows)
                    nodes['enabled']=sum(bool(r[0]) for r in rows)
                    nodes['fresh']=sum(bool(r[0] and r[1] and now-r[1]<180 and not r[2]) for r in rows)
                    nodes['errors']=sum(bool(r[0] and r[2]) for r in rows)
            hard('database',quick=='ok',{'quick_check':quick,'bytes':db_path.stat().st_size})
            soft('remote_nodes',nodes['errors']==0, nodes)
        except Exception as ex:hard('database',False,type(ex).__name__+': '+str(ex))

        try:
            core=Path(cfg.xray_binary);safe=core.is_file() and not core.is_symlink() and os.access(core,os.X_OK)
            if not safe:raise RuntimeError('Xray binary missing, symlinked or not executable')
            code,text=run_text([str(core),'version']);first=text.splitlines()[0] if text else ''
            valid=code==0 and first.lstrip().startswith('Xray ')
            hard('xray_core',valid,{'version':first[:250],'sha256':sha256(core) if valid else ''})
        except Exception as ex:hard('xray_core',False,type(ex).__name__+': '+str(ex))
        hard('production_mode',not cfg.test_engine,'test_engine must be false')
        hard('writes_enabled',cfg.writes_enabled,'writes_enabled must be true for normal production operation')

        try:
            base=(cfg.panel_path if cfg.panel_path!='/' else '')+'/'
            ui=panel_status(cfg,base);asset=panel_status(cfg,base+'assets/style.css')
            hard('panel_local_route',ui==200 and asset==200,{'ui_status':ui,'asset_status':asset,'url':cfg.public_origin+base})
            if cfg.panel_path!='/':
                root=panel_status(cfg,'/');soft('hidden_panel_root',root in {403,404},{'status':root})
        except Exception as ex:hard('panel_local_route',False,type(ex).__name__+': '+str(ex))

        try:soft('runtime_staged',not staged_runtime(cfg,runtime_row),'no unapplied runtime settings')
        except Exception as ex:hard('runtime_staged',False,'invalid saved runtime settings: '+str(ex))

        systemctl=shutil.which('systemctl')
        if systemctl:
            for unit,required in [('dark-xray.service',True),('dark-xray-guard.service',ipguard.get('mode')=='enforce')]:
                rc_active,active=run_text([systemctl,'is-active',unit],5)
                rc_enabled,enabled=run_text([systemctl,'is-enabled',unit],5)
                ok_active=rc_active==0 and active.strip()=='active';ok_enabled=rc_enabled==0 and enabled.startswith('enabled')
                if required:
                    hard(unit+'.active',ok_active,active or 'inactive')
                    hard(unit+'.enabled',ok_enabled,enabled or 'not enabled')
                else:
                    soft(unit+'.active',ok_active,active or 'inactive / optional while IP Guard observes')
                    soft(unit+'.enabled',ok_enabled,enabled or 'not enabled / optional while IP Guard observes')
        else:hard('systemd',False,'systemctl not found')

        nft=shutil.which('nft') is not None
        guard=None
        try:guard=BrokerClient(cfg.guard_socket).status()
        except Exception as ex:guard={'ok':False,'error':type(ex).__name__+': '+str(ex)}
        if ipguard.get('mode')=='enforce':
            hard('ipguard_nft',nft,'nft binary required for enforcement')
            hard('ipguard_broker',bool(guard and guard.get('ok') is True),guard)
            hard('direct_source_verified',bool(cfg.direct_source_verified and guard and guard.get('direct_source_verified')),guard)
        else:
            soft('ipguard_nft',nft,'nft binary recommended before switching to enforce')
            soft('ipguard_broker',bool(guard and guard.get('ok') is True),guard)
            checks['ipguard_mode']={'ok':True,'detail':ipguard.get('mode','observe')}

        timedate=shutil.which('timedatectl')
        if timedate:
            rc,val=run_text([timedate,'show','-p','NTPSynchronized','--value'],5)
            soft('ntp_sync',rc==0 and val.strip().lower()=='yes','NTPSynchronized='+val.strip())
        else:soft('ntp_sync',False,'timedatectl unavailable; verify server clock manually')

        origin=urlsplit(cfg.public_origin)
        if origin.scheme=='https':
            hard('tls_config',bool(cfg.secure_cookie and cfg.tls_certificate and cfg.tls_private_key),
                 {'secure_cookie':cfg.secure_cookie,'certificate':bool(cfg.tls_certificate),'private_key':bool(cfg.tls_private_key)})
        else:
            soft('tls_config',origin.hostname in {'127.0.0.1','::1','localhost'},'plain HTTP is acceptable only for loopback/SSH mode')

    result['ready']=not fail
    if not a.json_only:
        print('DARK XRAY VPS VERIFY',result['version'])
        print('READY' if result['ready'] else 'NOT READY')
        for name,item in checks.items():print((' [OK] ' if item['ok'] else ' [!!] ')+name+': '+str(item['detail']))
        for item in warn:print(' [WARN] '+item)
        for item in fail:print(' [FAIL] '+item)
        print('\nJSON RESULT')
    print(json.dumps(result,ensure_ascii=False,indent=2))
    raise SystemExit(0 if result['ready'] else 1)


if __name__=='__main__':main()
