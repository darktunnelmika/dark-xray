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
LE_STAGING='https://acme-staging-v02.api.letsencrypt.org/directory'
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


def _service_pid(unit:str='dark-xray.service')->int:
    try:
        cp=_child(['systemctl','show','-p','MainPID','--value',unit],8)
        value=cp.stdout.strip()
        return int(value) if cp.returncode==0 and value.isdigit() else 0
    except (OSError,subprocess.TimeoutExpired,ValueError):
        return 0


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


def renewal_evidence(config:Path)->dict[str,Any]:
    try:cfg=Config.load(config)
    except Exception as ex:return {'ok':False,'configured':False,'renewal_rehearsed':False,'error':type(ex).__name__+': '+str(ex)}
    origin=urlsplit(cfg.public_origin)
    if origin.scheme!='https':
        return {'ok':True,'configured':False,'renewal_rehearsed':False,'detail':'not applicable without HTTPS'}
    state=safe_json(TLS_SOURCE);lineage=Path(str(state.get('lineage') or ''))
    try:lineage_parent=lineage.parent.resolve()
    except Exception:lineage_parent=Path()
    source_ok=bool(state and str(state.get('domain') or '').lower()==str(origin.hostname or '').lower() and
                   lineage.is_dir() and lineage_parent==LE_LIVE.resolve())
    hook_ok=TLS_HOOK.is_file() and not TLS_HOOK.is_symlink() and os.access(TLS_HOOK,os.X_OK)
    timer_enabled=_systemctl_check('is-enabled','certbot.timer')
    timer_active=_systemctl_check('is-active','certbot.timer')
    return {'ok':bool(source_ok and hook_ok and timer_enabled and timer_active),'configured':True,
            'source_ok':source_ok,'hook_ok':hook_ok,'certbot_timer_enabled':timer_enabled,
            'certbot_timer_active':timer_active,'domain':state.get('domain',''),'lineage':str(lineage) if state else '',
            'renewal_rehearsed':False,'detail':'renewal plumbing verified; a real/dry-run renewal is a separate target test'}


def public_renewal_rehearsal(config:Path,requested:bool,allow_restart:bool,timeout:float)->dict[str,Any]:
    """Exercise public Let's Encrypt staging HTTP-01 and the real deploy hook.

    The dry-run contacts the public Let's Encrypt staging ACME service and must
    complete external HTTP-01 validation. --run-deploy-hooks intentionally
    restarts DARK through the installed hook, so this is opt-in and belongs on a
    disposable Stage 4 target. The production certificate is not replaced.
    """
    if not requested:
        return {'passed':True,'skipped':True,'requested':False,'performed':False,
                'renewal_rehearsed':False,'public_acme_staging':False,
                'external_http01_validation_tested':False,'production_certificate_replaced':False}
    if not allow_restart:
        return {'passed':False,'skipped':False,'requested':True,'performed':False,
                'renewal_rehearsed':False,'error':'service_restart_not_explicitly_allowed',
                'production_certificate_replaced':False}
    try:cfg=Config.load(config)
    except Exception:
        return {'passed':False,'skipped':False,'requested':True,'performed':False,
                'renewal_rehearsed':False,'error':'config_load_failed','production_certificate_replaced':False}
    origin=urlsplit(cfg.public_origin)
    state=safe_json(TLS_SOURCE);lineage=Path(str(state.get('lineage') or ''))
    try:lineage_parent=lineage.parent.resolve()
    except Exception:lineage_parent=Path()
    if (origin.scheme!='https' or not origin.hostname or str(state.get('domain') or '').lower()!=origin.hostname.lower()
        or not lineage.is_dir() or lineage_parent!=LE_LIVE.resolve()):
        return {'passed':False,'skipped':False,'requested':True,'performed':False,
                'renewal_rehearsed':False,'error':'active_certbot_lineage_unverified',
                'production_certificate_replaced':False}
    certbot=shutil.which('certbot')
    if not certbot:
        return {'passed':False,'skipped':False,'requested':True,'performed':False,
                'renewal_rehearsed':False,'error':'certbot_missing','production_certificate_replaced':False}
    before=https_evidence(config);pid_before=_service_pid()
    args=[certbot,'renew','--cert-name',lineage.name,'--dry-run','--run-deploy-hooks',
          '--no-random-sleep-on-renew','--server',LE_STAGING]
    try:cp=_child(args,timeout)
    except subprocess.TimeoutExpired:
        return {'passed':False,'skipped':False,'requested':True,'performed':True,
                'renewal_rehearsed':False,'exit_code':124,'error':'public_renewal_timeout',
                'public_acme_server':LE_STAGING,'production_certificate_replaced':False}
    except OSError:
        return {'passed':False,'skipped':False,'requested':True,'performed':False,
                'renewal_rehearsed':False,'error':'certbot_launch_failed',
                'production_certificate_replaced':False}
    pid_after=_service_pid();after=https_evidence(config)
    restarted=bool(pid_before>0 and pid_after>0 and pid_before!=pid_after)
    passed=bool(cp.returncode==0 and before.get('ok') is True and after.get('ok') is True and restarted)
    return {'passed':passed,'skipped':False,'requested':True,'performed':True,
            'renewal_rehearsed':passed,'exit_code':cp.returncode,'stderr_present':bool(cp.stderr.strip()),
            'public_acme_server':LE_STAGING,'public_acme_staging':cp.returncode==0,
            'public_dns_tested':cp.returncode==0,'external_http01_validation_tested':cp.returncode==0,
            'deploy_hook_restart_observed':restarted,'panel_https_before':before.get('ok') is True,
            'panel_https_after':after.get('ok') is True,'production_certificate_replaced':False,
            'detail':'public staging renewal rehearsal with external HTTP-01 and real deploy hook'}


def load_acceptance(data:Path,requested:bool,timeout:float)->dict[str,Any]:
    """Run and retain three independent 1000-client/12-worker contention reports."""
    if not requested:
        return {'passed':True,'skipped':True,'requested':False,'runs_required':3,'runs_completed':0}
    tool=ROOT/'tools/load-scale-gate.py'
    if not tool.is_file():
        return {'passed':False,'skipped':False,'requested':True,'runs_required':3,'runs_completed':0,
                'error':'installed_load_gate_missing'}
    batch=data/'qa'/'stage4-load'/(str(int(time.time()))+'-'+str(os.getpid()))
    try:
        batch.mkdir(parents=True,mode=0o700)
        os.chmod(batch,0o700)
    except OSError:
        return {'passed':False,'skipped':False,'requested':True,'runs_required':3,'runs_completed':0,
                'error':'load_evidence_directory_failed'}
    runs=[]
    for index in range(1,4):
        report=batch/f'run-{index}.json';exit_code=1;timed_out=False;stderr_present=False
        try:
            cp=_child([sys.executable,tool,'--clients','1000','--concurrency','12','--report',report],timeout)
            exit_code=cp.returncode;stderr_present=bool(cp.stderr.strip())
        except subprocess.TimeoutExpired:
            exit_code=124;timed_out=True
        except OSError:
            exit_code=126
        doc=safe_json(report)
        if report.is_file() and not report.is_symlink():
            try:os.chmod(report,0o600)
            except OSError:pass
        patch=doc.get('patch_requests') if isinstance(doc.get('patch_requests'),dict) else {}
        valid=bool(exit_code==0 and doc.get('passed') is True and doc.get('phase')=='completed'
                   and doc.get('clients_requested')==1000 and doc.get('concurrency')==12
                   and patch.get('finished')==100 and patch.get('accepted')==100
                   and doc.get('errors')==[])
        runs.append({'run':index,'passed':valid,'exit_code':exit_code,'timed_out':timed_out,
                     'stderr_present':stderr_present,'phase':str(doc.get('phase') or ''),
                     'patch_finished':patch.get('finished'),'patch_accepted':patch.get('accepted'),
                     'patch_max_seconds':patch.get('max_seconds'),'report':str(report)})
    passed=len(runs)==3 and all(r['passed'] for r in runs)
    summary={'passed':passed,'skipped':False,'requested':True,'runs_required':3,'runs_completed':len(runs),
             'clients_per_run':1000,'concurrency':12,'runs':runs,'evidence_directory':str(batch),
             'historical_failure_erased_by_retry':False,
             'detail':'all three retained runs must pass; any failed run blocks this acceptance batch'}
    try:atomic_report(batch/'summary.json',summary,'.stage4-load-summary-')
    except Exception:summary['passed']=False;summary['report_error']='summary_write_failed'
    return summary


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


def node_phase(config:Path,data:Path,min_nodes:int,timeout:float,watch_seconds:float,expected_outages:list[str],
               expected_sources:list[str],source_ip_max_age:float)->dict[str,Any]:
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
    for item in expected_sources:args.extend(['--expect-source-ip',item])
    args.extend(['--source-ip-max-age',str(source_ip_max_age)])
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
                  expected_outages:list[str],expected_sources:list[str],source_ip_max_age:float)->dict[str,Any]:
    source=source_evidence(data,expected_commit)
    production=production_phase(config,data,production_timeout)
    https=https_evidence(config);renewal=renewal_evidence(config)
    tls_ok=bool(https.get('ok') and renewal.get('ok') and (https.get('https') or not require_domain_tls))
    nodes=node_phase(config,data,min_nodes,node_timeout,node_watch_seconds,expected_outages,expected_sources,source_ip_max_age)
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
    ap.add_argument('--production-timeout',type=float,default=360.0)
    ap.add_argument('--min-nodes',type=int,default=0)
    ap.add_argument('--node-timeout',type=float,default=8.0)
    ap.add_argument('--node-watch-seconds',type=float,default=0.0)
    ap.add_argument('--expect-outage',action='append',default=[],metavar='NODE_ID')
    ap.add_argument('--expect-source-ip',action='append',default=[],metavar='NODE_ID,EMAIL,IP')
    ap.add_argument('--source-ip-max-age',type=float,default=180.0)
    ap.add_argument('--rehearse-public-renewal',action='store_true',
                    help='Run public Let\'s Encrypt staging HTTP-01 renewal rehearsal')
    ap.add_argument('--allow-service-restart',action='store_true',
                    help='Explicitly allow the renewal deploy hook to restart DARK')
    ap.add_argument('--renewal-timeout',type=float,default=420.0)
    ap.add_argument('--run-load-acceptance',action='store_true',
                    help='Run three retained 1000-client/12-worker load gates')
    ap.add_argument('--load-timeout',type=float,default=900.0)
    ap.add_argument('--stage4',action='store_true',
                    help='Require the complete independent-VPS Stage 4 profile')
    ap.add_argument('--json-only',action='store_true')
    a=ap.parse_args()
    if os.geteuid()!=0:raise SystemExit('Target VPS gate requires root so service/TLS evidence is complete')
    if (not all(math.isfinite(x) for x in (a.production_timeout,a.node_timeout,a.node_watch_seconds,
                                            a.source_ip_max_age,a.renewal_timeout,a.load_timeout))
        or a.production_timeout<=0 or not .2<=a.node_timeout<=30 or a.node_watch_seconds<0
        or not 1<=a.source_ip_max_age<=3600 or not 30<=a.renewal_timeout<=1800
        or not 60<=a.load_timeout<=3600):
        raise SystemExit('Invalid finite timeout/watch values')
    if a.min_nodes<0:raise SystemExit('--min-nodes must be >= 0')
    expected=a.expect_source_commit.strip().lower()
    if expected and not SHA_RE.fullmatch(expected):raise SystemExit('--expect-source-commit must be an immutable 40-character SHA')
    if a.expect_outage and (a.min_nodes<1 or a.node_watch_seconds<=0):
        raise SystemExit('--expect-outage requires --min-nodes >= 1 and --node-watch-seconds > 0')
    if a.phase=='pre-reboot' and (a.rehearse_public_renewal or a.run_load_acceptance):
        raise SystemExit('Active renewal/load checks belong to single or post-reboot phase, not pre-reboot')
    if a.rehearse_public_renewal and not a.allow_service_restart:
        raise SystemExit('--rehearse-public-renewal requires --allow-service-restart')
    if a.stage4:
        missing=[]
        if a.phase!='post-reboot':missing.append('--phase post-reboot')
        if not expected:missing.append('--expect-source-commit <40-char SHA>')
        if not a.require_domain_tls:missing.append('--require-domain-tls')
        if not a.rehearse_public_renewal:missing.append('--rehearse-public-renewal')
        if not a.allow_service_restart:missing.append('--allow-service-restart')
        if not a.run_load_acceptance:missing.append('--run-load-acceptance')
        if a.min_nodes<2:missing.append('--min-nodes 2+')
        if not a.expect_outage:missing.append('--expect-outage NODE_ID')
        if a.node_watch_seconds<=0:missing.append('--node-watch-seconds > 0')
        if not a.expect_source_ip:missing.append('--expect-source-ip NODE_ID,EMAIL,IP')
        if missing:raise SystemExit('Stage 4 profile requires: '+', '.join(missing))

    report_path=a.report or (a.data/'qa/target-vps-gate.json')
    state_path=a.state_file or (a.data/'qa/target-vps-pre-reboot.json')
    started=time.time();boot=boot_identity();source=installed_source(a.data);cfg_sha=config_sha256(a.config)
    base=base_evidence(a.config,a.data,expected_commit=expected,require_domain_tls=a.require_domain_tls,
                       production_timeout=a.production_timeout,min_nodes=a.min_nodes,node_timeout=a.node_timeout,
                       node_watch_seconds=a.node_watch_seconds,expected_outages=a.expect_outage,
                       expected_sources=a.expect_source_ip,source_ip_max_age=a.source_ip_max_age)
    if base['passed']:
        public_renewal=public_renewal_rehearsal(a.config,a.rehearse_public_renewal,a.allow_service_restart,a.renewal_timeout)
        load=load_acceptance(a.data,a.run_load_acceptance,a.load_timeout)
    else:
        public_renewal={'passed':not a.rehearse_public_renewal,'skipped':True,'requested':a.rehearse_public_renewal,
                        'performed':False,'renewal_rehearsed':False,'detail':'base gate failed; active renewal not started',
                        'production_certificate_replaced':False}
        load={'passed':not a.run_load_acceptance,'skipped':True,'requested':a.run_load_acceptance,
              'runs_required':3,'runs_completed':0,'detail':'base gate failed; load acceptance not started'}
    active_passed=bool(public_renewal.get('passed') is True and load.get('passed') is True)
    reboot={'ok':False,'required':a.phase=='post-reboot','detail':'reboot proof not requested in single phase'}
    passed=bool(base['passed'] and active_passed)

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
    result={'version':VERSION,'phase':a.phase,'stage4_profile':bool(a.stage4),
            'target_vps_phase_passed':phase_passed,'target_vps_gate_passed':full_gate_passed,
            'started_at':started,'finished_at':time.time(),'boot':boot,'reboot':reboot,
            'config_sha256':cfg_sha,'base':base,'public_renewal':public_renewal,'load_acceptance':load,
            'changes_made':'private validation reports; optional node health metadata; opt-in renewal deploy-hook restart; temporary isolated load fixtures',
            'limitations':{'certificate_issuance_performed':False,
                           'certificate_renewal_rehearsed':public_renewal.get('renewal_rehearsed') is True,
                           'production_certificate_replaced':False,
                           'reboot_injected_by_gate':False,'node_outage_injected_by_gate':False,
                           'load_is_capacity_regression_not_sla':True}}
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
        print(' Public ACME:','SKIP' if public_renewal.get('skipped') else ('PASS' if public_renewal.get('passed') else 'FAIL'))
        print(' Load x3    :','SKIP' if load.get('skipped') else ('PASS' if load.get('passed') else 'FAIL'))
        if a.phase in {'pre-reboot','post-reboot'}:print(' Reboot     :','PASS' if reboot.get('ok') else ('BASELINE' if a.phase=='pre-reboot' else 'FAIL'))
        print(' Report     :',result.get('report_path',report_path))
        if a.phase=='pre-reboot' and base['passed']:
            print(' Next       : reboot the VPS, then run the same command with --phase post-reboot')
        print('\nJSON RESULT')
    print(json.dumps(result,ensure_ascii=False,indent=2))
    raise SystemExit(0 if result['target_vps_phase_passed'] else 1)


if __name__=='__main__':main()
