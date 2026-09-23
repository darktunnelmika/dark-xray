#!/usr/bin/env python3
"""Stateful validation gate for a real DARK XRAY target VPS.

This gate builds on production-gate.py and records the extra evidence that CI
cannot prove: exact installed source identity, local HTTPS/HSTS behavior,
certificate-renewal plumbing, optional real-WAN node health, and an actual
machine reboot observed across two invocations.

Typical reboot proof:
  sudo darkxray target-vps-gate --phase pre-reboot --expect-source-commit <sha>
  sudo reboot
  sudo darkxray target-vps-gate --phase post-reboot --expect-source-commit <sha>

The gate does not reboot the machine, issue/renew certificates, alter firewall
rules, or inject node outages. Optional node checks can update normal node health
metadata through the existing node-wan-gate.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))
from core import Config
from node_gate_budget import enabled_node_count, node_budget

VERSION=(ROOT/'VERSION').read_text(encoding='utf-8').strip() if (ROOT/'VERSION').is_file() else 'unknown'
BOOT_ID_PATH=Path('/proc/sys/kernel/random/boot_id')
TLS_SOURCE=Path('/etc/dark-xray/tls-source.json')
TLS_HOOK=Path('/etc/letsencrypt/renewal-hooks/deploy/dark-xray-panel')
LE_LIVE=Path('/etc/letsencrypt/live')
LE_RENEWAL=Path('/etc/letsencrypt/renewal')
LE_PRODUCTION_DIRECTORY='https://acme-v02.api.letsencrypt.org/directory'
SHA_RE=re.compile(r'[0-9a-f]{40}')


def _child(args:list[str|Path],timeout:float)->subprocess.CompletedProcess[str]:
    return subprocess.run([str(x) for x in args],capture_output=True,text=True,check=False,timeout=timeout)


def _json_text(text:str)->dict[str,Any]:
    value=json.loads(text)
    if not isinstance(value,dict):raise ValueError('child result must be a JSON object')
    return value


def atomic_report(path:Path,payload:dict[str,Any],prefix:str='.target-vps-')->None:
    path=path.expanduser();path.parent.mkdir(parents=True,exist_ok=True)
    if path.is_symlink():raise RuntimeError('Refusing to replace a symlink report path')
    fd,name=tempfile.mkstemp(prefix=prefix,suffix='.json',dir=path.parent);tmp=Path(name)
    try:
        with os.fdopen(fd,'w',encoding='utf-8') as f:
            json.dump(payload,f,ensure_ascii=False,indent=2);f.write('\n');f.flush();os.fsync(f.fileno())
        os.chmod(tmp,0o600);os.replace(tmp,path)
        dfd=os.open(path.parent,os.O_RDONLY)
        try:os.fsync(dfd)
        finally:os.close(dfd)
    finally:tmp.unlink(missing_ok=True)


def safe_json(path:Path)->dict[str,Any]:
    if path.is_symlink() or not path.is_file() or path.stat().st_size>1024*1024:return {}
    try:value=json.loads(path.read_text(encoding='utf-8'))
    except Exception:return {}
    return value if isinstance(value,dict) else {}


def boot_identity()->dict[str,Any]:
    value=BOOT_ID_PATH.read_text(encoding='utf-8').strip() if BOOT_ID_PATH.is_file() else ''
    return {'boot_id':value,'uptime_seconds':round(float(Path('/proc/uptime').read_text().split()[0]),3) if Path('/proc/uptime').is_file() else None}


def config_sha256(path:Path)->str:
    if path.is_symlink() or not path.is_file():return ''
    h=hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda:f.read(1024*1024),b''):h.update(chunk)
    return h.hexdigest()


def installed_source(data:Path)->dict[str,Any]:
    value=safe_json(data/'installed-source.json')
    return {
        'present':bool(value),
        'commit':str(value.get('commit') or ''),
        'version':str(value.get('version') or VERSION),
        'ref':str(value.get('ref') or ''),
        'installed_at':value.get('installed_at'),
    }


def source_evidence(data:Path,expected_commit:str)->dict[str,Any]:
    current=installed_source(data);expected=(expected_commit or '').strip().lower()
    if expected:
        ok=bool(SHA_RE.fullmatch(expected) and current['present'] and current['commit'].lower()==expected)
        detail='exact installed source commit matches' if ok else 'installed source does not match expected immutable commit'
    else:
        ok=True;detail='installed source recorded' if current['present'] else 'no immutable installed-source record; version-only evidence'
    return {'ok':ok,'expected_commit':expected,'detail':detail,**current}


def _systemctl_check(action:str,unit:str)->bool:
    try:return _child(['systemctl',action,'--quiet',unit],8).returncode==0
    except (OSError,subprocess.TimeoutExpired):return False


def https_evidence(config:Path)->dict[str,Any]:
    try:cfg=Config.load(config)
    except Exception as ex:return {'ok':False,'https':False,'error':'config load failed: '+type(ex).__name__+': '+str(ex)}
    origin=urlsplit(cfg.public_origin)
    if origin.scheme!='https':
        return {'ok':True,'https':False,'public_origin':cfg.public_origin,'detail':'panel is not configured for HTTPS'}
    target='127.0.0.1' if cfg.bind_host in {'0.0.0.0','::'} else cfg.bind_host
    path=(cfg.panel_path if cfg.panel_path!='/' else '')+'/'
    raw=None
    try:
        raw=socket.create_connection((target,cfg.bind_port),timeout=5)
        ctx=ssl.create_default_context()
        with ctx.wrap_socket(raw,server_hostname=origin.hostname) as sock:
            raw=None
            cert=sock.getpeercert()
            req=f'GET {path} HTTP/1.1\r\nHost: {origin.netloc}\r\nConnection: close\r\nUser-Agent: darkxray-target-vps-gate\r\n\r\n'.encode()
            sock.sendall(req);head=b''
            while b'\r\n\r\n' not in head and len(head)<65536:
                part=sock.recv(2048)
                if not part:break
                head+=part
        lines=head.decode('iso-8859-1','replace').split('\r\n')
        status=int(lines[0].split()[1]) if lines and len(lines[0].split())>=2 and lines[0].split()[1].isdigit() else 0
        headers={}
        for row in lines[1:]:
            if ':' not in row:continue
            k,v=row.split(':',1);headers[k.strip().lower()]=v.strip()
        hsts=headers.get('strict-transport-security','')
        ok=bool(status==200 and cfg.secure_cookie and cfg.tls_certificate and cfg.tls_private_key and hsts)
        return {'ok':ok,'https':True,'public_origin':cfg.public_origin,'status':status,
                'secure_cookie':bool(cfg.secure_cookie),'hsts':hsts,'certificate_verified':True,
                'certificate_not_after':cert.get('notAfter',''),'certificate_subject_alt_name':cert.get('subjectAltName',[])}
    except Exception as ex:
        return {'ok':False,'https':True,'public_origin':cfg.public_origin,'certificate_verified':False,
                'error':type(ex).__name__+': '+str(ex)}
    finally:
        if raw is not None:
            try:raw.close()
            except Exception:pass


def _renewal_server(path:Path)->str:
    if path.is_symlink() or not path.is_file() or path.stat().st_size>1024*1024:return ''
    try:text=path.read_text(encoding='utf-8')
    except Exception:return ''
    matches=re.findall(r'(?m)^\s*server\s*=\s*(\S+)\s*$',text)
    return matches[-1].rstrip('/') if matches else ''


def _active_tls_digests(cfg)->dict[str,str]:
    result={}
    for key in ('tls_certificate','tls_private_key'):
        path=Path(str(getattr(cfg,key,'')))
        result[key]=config_sha256(path)
    return result


def renewal_evidence(config:Path,*,rehearse:bool=False,require_letsencrypt:bool=False,timeout:float=300.0)->dict[str,Any]:
    try:cfg=Config.load(config)
    except Exception as ex:return {'ok':False,'configured':False,'renewal_rehearsed':False,'error':type(ex).__name__+': '+str(ex)}
    origin=urlsplit(cfg.public_origin)
    if origin.scheme!='https':
        requested=bool(rehearse or require_letsencrypt)
        return {'ok':not requested,'configured':False,'renewal_rehearsed':False,
                'detail':'HTTPS is required for requested renewal acceptance' if requested else 'not applicable without HTTPS'}
    state=safe_json(TLS_SOURCE);lineage=Path(str(state.get('lineage') or ''))
    try:lineage_parent=lineage.parent.resolve()
    except Exception:lineage_parent=Path()
    source_ok=bool(state and str(state.get('domain') or '').lower()==str(origin.hostname or '').lower() and
                   lineage.is_dir() and lineage_parent==LE_LIVE.resolve())
    hook_ok=TLS_HOOK.is_file() and not TLS_HOOK.is_symlink() and os.access(TLS_HOOK,os.X_OK)
    timer_enabled=_systemctl_check('is-enabled','certbot.timer')
    timer_active=_systemctl_check('is-active','certbot.timer')
    renewal_conf=LE_RENEWAL/(lineage.name+'.conf') if lineage.name else LE_RENEWAL/'invalid.conf'
    acme_server=_renewal_server(renewal_conf)
    letsencrypt_production=acme_server==LE_PRODUCTION_DIRECTORY.rstrip('/')
    rehearsal={'requested':bool(rehearse),'performed':False,'ok':not rehearse,
               'active_pair_unchanged':None,'exit_code':None,'stderr_present':False}
    if rehearse:
        certbot=shutil.which('certbot')
        if not certbot:
            rehearsal.update(ok=False,error='certbot_not_found')
        elif not (source_ok and hook_ok and timer_enabled and timer_active):
            rehearsal.update(ok=False,error='renewal_plumbing_not_ready')
        else:
            before=_active_tls_digests(cfg)
            try:
                cp=_child([certbot,'renew','--dry-run','--cert-name',lineage.name,
                           '--no-random-sleep-on-renew','--no-directory-hooks'],timeout)
                after=_active_tls_digests(cfg)
                unchanged=bool(before and before==after and all(before.values()))
                rehearsal.update(performed=True,ok=bool(cp.returncode==0 and unchanged),
                                 active_pair_unchanged=unchanged,exit_code=cp.returncode,
                                 stderr_present=bool(cp.stderr.strip()))
            except subprocess.TimeoutExpired:
                rehearsal.update(performed=True,ok=False,active_pair_unchanged=_active_tls_digests(cfg)==before,
                                 exit_code=124,error='certbot_dry_run_timed_out')
            except OSError:
                rehearsal.update(performed=True,ok=False,active_pair_unchanged=_active_tls_digests(cfg)==before,
                                 error='certbot_dry_run_launch_failed')
    base_ok=bool(source_ok and hook_ok and timer_enabled and timer_active)
    server_ok=bool(letsencrypt_production or not require_letsencrypt)
    overall=bool(base_ok and server_ok and rehearsal.get('ok') is True)
    return {'ok':overall,'configured':True,'source_ok':source_ok,'hook_ok':hook_ok,
            'certbot_timer_enabled':timer_enabled,'certbot_timer_active':timer_active,
            'domain':state.get('domain',''),'lineage':str(lineage) if state else '',
            'renewal_config':str(renewal_conf),'acme_server':acme_server,
            'letsencrypt_production':letsencrypt_production,
            'letsencrypt_required':bool(require_letsencrypt),
            'renewal_rehearsed':bool(rehearsal.get('performed') and rehearsal.get('ok')),
            'rehearsal':rehearsal,
            'detail':'renewal plumbing and requested rehearsal verified' if overall else 'renewal acceptance incomplete'}


def production_phase(config:Path,data:Path,timeout:float)->dict[str,Any]:
    report=data/'qa/target-production-gate.json'
    try:cp=_child([sys.executable,ROOT/'tools/production-gate.py','--config',config,'--data',data,'--report',report,'--json-only'],timeout)
    except subprocess.TimeoutExpired:return {'production_gate_passed':False,'exit_code':124,'error':'production gate timed out'}
    try:result=_json_text(cp.stdout)
    except Exception as ex:result={'production_gate_passed':False,'error':'invalid production gate JSON: '+type(ex).__name__}
    result['exit_code']=cp.returncode
    if cp.stderr.strip():result['stderr']=cp.stderr.strip()[-3000:]
    if cp.returncode!=0:result['production_gate_passed']=False
    return result


def node_phase(config:Path,data:Path,min_nodes:int,timeout:float,watch_seconds:float,expected_outages:list[str])->dict[str,Any]:
    if min_nodes<=0:return {'passed':True,'skipped':True,'minimum_nodes':0,'detail':'node WAN gate not requested'}
    try:
        count=enabled_node_count(data)
        budget=node_budget(count,timeout,watch_seconds)
    except Exception:
        return {'passed':False,'skipped':False,'observation_complete':False,
                'error':'node_budget_unavailable'}
    args=[sys.executable,ROOT/'tools/node-wan-gate.py','--config',config,'--data',data,'--min-nodes',str(min_nodes),
          '--timeout',str(timeout),'--watch-seconds',str(watch_seconds),'--expected-node-count',str(count),
          '--report',data/'qa/target-node-wan-gate.json','--json-only']
    for node_id in expected_outages:args.extend(['--expect-outage',node_id])
    try:cp=_child(args,budget['subprocess_timeout_seconds'])
    except subprocess.TimeoutExpired:
        # run() kills and reaps the child. Never trust partial stdout or a report
        # left by an earlier invocation, even if it says passed=true.
        return {'passed':False,'skipped':False,'observation_complete':False,'exit_code':124,
                'error':'node WAN gate timed out','timing_budget':budget}
    except OSError:
        return {'passed':False,'skipped':False,'observation_complete':False,
                'error':'node_gate_launch_failed','timing_budget':budget}
    try:
        result=_json_text(cp.stdout)
        if (type(result.get('passed')) is not bool or type(result.get('configured_enabled_nodes')) is not int
                or result['configured_enabled_nodes']!=count or result.get('observation_complete') is not True):
            raise ValueError('incomplete_or_changed_node_observation')
    except Exception:
        result={'passed':False,'observation_complete':False,'error':'invalid_or_incomplete_node_gate_result'}
    result.update(exit_code=cp.returncode,skipped=False,timing_budget=budget)
    # A child traceback can contain credentials or config text; only record its presence.
    result['stderr_present']=bool(cp.stderr.strip())
    if cp.returncode!=0:result['passed']=False
    return result


def base_evidence(config:Path,data:Path,*,expected_commit:str,require_domain_tls:bool,
                  production_timeout:float,min_nodes:int,node_timeout:float,node_watch_seconds:float,
                  expected_outages:list[str],rehearse_renewal:bool=False,require_letsencrypt:bool=False,
                  renewal_timeout:float=300.0)->dict[str,Any]:
    source=source_evidence(data,expected_commit)
    production=production_phase(config,data,production_timeout)
    https=https_evidence(config);renewal=renewal_evidence(config,rehearse=rehearse_renewal,
        require_letsencrypt=require_letsencrypt,timeout=renewal_timeout)
    tls_ok=bool(https.get('ok') and renewal.get('ok') and (https.get('https') or not require_domain_tls))
    nodes=node_phase(config,data,min_nodes,node_timeout,node_watch_seconds,expected_outages)
    passed=bool(production.get('production_gate_passed') is True and source.get('ok') is True and tls_ok and nodes.get('passed') is True)
    return {'passed':passed,'production':production,'source':source,
            'tls':{'passed':tls_ok,'domain_tls_required':require_domain_tls,'https':https,'renewal':renewal},
            'nodes':nodes}


def reboot_evidence(pre:dict[str,Any],current_boot:dict[str,Any],current_source:dict[str,Any],current_config_sha:str)->dict[str,Any]:
    old_boot=str((pre.get('boot') or {}).get('boot_id') or '');new_boot=str(current_boot.get('boot_id') or '')
    old_source=pre.get('source') or {}
    old_commit=str(old_source.get('commit') or '');new_commit=str(current_source.get('commit') or '')
    old_version=str(old_source.get('version') or '');new_version=str(current_source.get('version') or '')
    if old_commit or new_commit:
        same_source=bool(old_commit and new_commit and old_commit.lower()==new_commit.lower())
        source_basis='commit'
    else:
        same_source=bool(old_version and new_version and old_version==new_version);source_basis='version'
    same_config=bool(pre.get('config_sha256') and pre.get('config_sha256')==current_config_sha)
    changed=bool(old_boot and new_boot and old_boot!=new_boot)
    return {'ok':bool(changed and same_source and same_config),'boot_changed':changed,'previous_boot_id':old_boot,'current_boot_id':new_boot,
            'same_source':same_source,'source_basis':source_basis,'same_config':same_config}


def main()->None:
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--config',type=Path,required=True)
    ap.add_argument('--data',type=Path,required=True)
    ap.add_argument('--phase',choices=['single','pre-reboot','post-reboot'],default='single')
    ap.add_argument('--report',type=Path)
    ap.add_argument('--state-file',type=Path)
    ap.add_argument('--expect-source-commit',default='')
    ap.add_argument('--require-domain-tls',action='store_true')
    ap.add_argument('--require-letsencrypt',action='store_true',help='require the active Certbot lineage to use the public Let\'s Encrypt production directory')
    ap.add_argument('--rehearse-renewal',action='store_true',help='explicitly run a real Certbot dry-run; may contact the public ACME staging service and requires external HTTP-01 reachability')
    ap.add_argument('--renewal-timeout',type=float,default=300.0)
    ap.add_argument('--production-timeout',type=float,default=360.0)
    ap.add_argument('--min-nodes',type=int,default=0)
    ap.add_argument('--node-timeout',type=float,default=8.0)
    ap.add_argument('--node-watch-seconds',type=float,default=0.0)
    ap.add_argument('--expect-outage',action='append',default=[],metavar='NODE_ID')
    ap.add_argument('--json-only',action='store_true')
    a=ap.parse_args()
    if os.geteuid()!=0:raise SystemExit('Target VPS gate requires root so service/TLS evidence is complete')
    if (not all(math.isfinite(x) for x in (a.production_timeout,a.node_timeout,a.node_watch_seconds,a.renewal_timeout))
        or a.production_timeout<=0 or not .2<=a.node_timeout<=30 or a.node_watch_seconds<0
        or not 30<=a.renewal_timeout<=900):
        raise SystemExit('Invalid finite timeout/watch values')
    if a.min_nodes<0:raise SystemExit('--min-nodes must be >= 0')
    expected=a.expect_source_commit.strip().lower()
    if expected and not SHA_RE.fullmatch(expected):raise SystemExit('--expect-source-commit must be an immutable 40-character SHA')
    if a.expect_outage and (a.min_nodes<1 or a.node_watch_seconds<=0):
        raise SystemExit('--expect-outage requires --min-nodes >= 1 and --node-watch-seconds > 0')
    if (a.rehearse_renewal or a.require_letsencrypt) and not a.require_domain_tls:
        raise SystemExit('--rehearse-renewal/--require-letsencrypt require --require-domain-tls')

    report_path=a.report or (a.data/'qa/target-vps-gate.json')
    state_path=a.state_file or (a.data/'qa/target-vps-pre-reboot.json')
    started=time.time();boot=boot_identity();source=installed_source(a.data);cfg_sha=config_sha256(a.config)
    base=base_evidence(a.config,a.data,expected_commit=expected,require_domain_tls=a.require_domain_tls,
                       production_timeout=a.production_timeout,min_nodes=a.min_nodes,node_timeout=a.node_timeout,
                       node_watch_seconds=a.node_watch_seconds,expected_outages=a.expect_outage,
                       rehearse_renewal=a.rehearse_renewal,require_letsencrypt=a.require_letsencrypt,
                       renewal_timeout=a.renewal_timeout)
    reboot={'ok':False,'required':a.phase=='post-reboot','detail':'reboot proof not requested in single phase'}
    passed=base['passed']

    if a.phase=='pre-reboot':
        reboot={'ok':False,'required':True,'detail':'baseline recorded; reboot has not been proven yet'}
        if base['passed']:
            atomic_report(state_path,{'version':VERSION,'created_at':time.time(),'boot':boot,'source':source,
                                      'config_sha256':cfg_sha,'expected_source_commit':expected},'.target-vps-state-')
    elif a.phase=='post-reboot':
        pre=safe_json(state_path)
        if not pre:
            reboot={'ok':False,'required':True,'detail':'pre-reboot state is missing or invalid'}
        else:
            reboot=reboot_evidence(pre,boot,source,cfg_sha)|{'required':True}
        passed=bool(base['passed'] and reboot.get('ok') is True)

    phase_passed=bool(passed)
    full_gate_passed=bool(a.phase=='post-reboot' and phase_passed and reboot.get('ok') is True)
    result={'version':VERSION,'phase':a.phase,'target_vps_phase_passed':phase_passed,'target_vps_gate_passed':full_gate_passed,'started_at':started,'finished_at':time.time(),
            'boot':boot,'reboot':reboot,'config_sha256':cfg_sha,'base':base,
            'changes_made':'private validation reports only; optional node gate may update normal node health metadata',
            'limitations':{'certificate_issuance_performed':False,
                           'certificate_renewal_rehearsed':bool(base['tls']['renewal'].get('renewal_rehearsed')),
                           'public_acme_contact_requested':bool(a.rehearse_renewal),
                           'reboot_injected_by_gate':False,'node_outage_injected_by_gate':False}}
    if a.phase=='pre-reboot':result['state_file']=str(state_path)
    try:
        atomic_report(report_path,result);result['report_path']=str(report_path)
    except Exception as ex:
        result['target_vps_phase_passed']=False;result['target_vps_gate_passed']=False;result['report_error']=type(ex).__name__+': '+str(ex)
    if not a.json_only:
        print('DARK XRAY TARGET VPS GATE',VERSION)
        print('PASS' if result['target_vps_phase_passed'] else 'FAIL','·',a.phase)
        print(' Production :','PASS' if base['production'].get('production_gate_passed') is True else 'FAIL')
        print(' Source     :','PASS' if base['source'].get('ok') is True else 'FAIL')
        print(' TLS        :','PASS' if base['tls']['passed'] else 'FAIL')
        print(' Nodes      :','SKIP' if base['nodes'].get('skipped') else ('PASS' if base['nodes'].get('passed') else 'FAIL'))
        if a.phase in {'pre-reboot','post-reboot'}:print(' Reboot     :','PASS' if reboot.get('ok') else ('BASELINE' if a.phase=='pre-reboot' else 'FAIL'))
        print(' Report     :',result.get('report_path',report_path))
        if a.phase=='pre-reboot' and base['passed']:
            print(' Next       : reboot the VPS, then run the same command with --phase post-reboot')
        print('\nJSON RESULT')
    print(json.dumps(result,ensure_ascii=False,indent=2))
    raise SystemExit(0 if result['target_vps_phase_passed'] else 1)


if __name__=='__main__':main()
