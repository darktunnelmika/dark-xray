#!/usr/bin/env python3
"""Unified production gate for an installed DARK XRAY VPS.

The gate has two independent phases:
1) read-only verification of the installed configuration/service/database state;
2) an isolated temporary data-plane lab using the exact Xray binary configured by
   the installed instance.

The second phase never opens the installed customer database and never changes
installed firewall rules or service state. It creates only temporary loopback
listeners/processes and a private JSON report. Overall success requires both
phases to pass.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))
from core import Config

VERSION=(ROOT/'VERSION').read_text(encoding='utf-8').strip() if (ROOT/'VERSION').is_file() else 'unknown'


def _child(args:list[str|Path],timeout:float)->subprocess.CompletedProcess[str]:
    return subprocess.run([str(x) for x in args],capture_output=True,text=True,check=False,timeout=timeout)


def _json_text(text:str)->dict[str,Any]:
    value=json.loads(text)
    if not isinstance(value,dict):raise ValueError('child result must be a JSON object')
    return value


def combine(readiness:dict[str,Any],data_plane:dict[str,Any],*,started_at:float,finished_at:float)->dict[str,Any]:
    ready=readiness.get('ready') is True
    live=data_plane.get('passed') is True
    return {
        'version':VERSION,
        'production_gate_passed':bool(ready and live),
        'installed_state_modified':False,
        'installed_customer_database_modified':False,
        'installed_firewall_modified':False,
        'isolated_data_plane_lab_used':data_plane.get('skipped') is not True,
        'started_at':started_at,
        'finished_at':finished_at,
        'duration_seconds':round(max(0.0,finished_at-started_at),3),
        'readiness':readiness,
        'data_plane':data_plane,
    }


def atomic_report(path:Path,payload:dict[str,Any])->None:
    path=path.expanduser()
    path.parent.mkdir(parents=True,exist_ok=True)
    if path.is_symlink():raise RuntimeError('Refusing to replace a symlink report path')
    fd,name=tempfile.mkstemp(prefix='.production-gate-',suffix='.json',dir=path.parent)
    tmp=Path(name)
    try:
        with os.fdopen(fd,'w',encoding='utf-8') as f:
            json.dump(payload,f,ensure_ascii=False,indent=2)
            f.write('\n');f.flush();os.fsync(f.fileno())
        os.chmod(tmp,0o600)
        os.replace(tmp,path)
        dfd=os.open(path.parent,os.O_RDONLY)
        try:os.fsync(dfd)
        finally:os.close(dfd)
    finally:
        tmp.unlink(missing_ok=True)


def readiness_phase(config:Path,data:Path,timeout:float)->dict[str,Any]:
    tool=ROOT/'tools/vps-verify.py'
    try:
        cp=_child([sys.executable,tool,'--config',config,'--data',data,'--json-only'],timeout)
    except subprocess.TimeoutExpired:
        return {'ready':False,'exit_code':124,'error':'vps-verify timed out'}
    try:result=_json_text(cp.stdout)
    except Exception as ex:
        result={'ready':False,'error':'invalid vps-verify JSON: '+type(ex).__name__}
    result['exit_code']=cp.returncode
    if cp.stderr.strip():result['stderr']=cp.stderr.strip()[-2000:]
    if cp.returncode!=0:result['ready']=False
    return result


def data_plane_phase(config:Path,timeout:float)->dict[str,Any]:
    try:
        cfg=Config.load(config)
    except Exception as ex:
        return {'passed':False,'skipped':True,'reason':'configuration unavailable: '+type(ex).__name__+': '+str(ex)}
    binary=Path(cfg.xray_binary)
    result:dict[str,Any]
    with tempfile.TemporaryDirectory(prefix='dark-production-gate-') as td:
        report=Path(td)/'data-plane.json'
        tool=ROOT/'tools/smoke-real.py'
        try:
            cp=_child([sys.executable,tool,'--binary',binary,'--report',report],timeout)
        except subprocess.TimeoutExpired:
            return {'passed':False,'skipped':False,'exit_code':124,'binary':str(binary),'error':'real data-plane smoke timed out'}
        if report.is_file():
            try:result=_json_text(report.read_text(encoding='utf-8'))
            except Exception as ex:result={'passed':False,'error':'invalid data-plane report: '+type(ex).__name__}
        else:
            try:result=_json_text(cp.stdout)
            except Exception:result={'passed':False,'error':'data-plane report was not produced'}
        result['exit_code']=cp.returncode
        result['binary']=str(binary)
        if cp.stderr.strip():result['stderr']=cp.stderr.strip()[-3000:]
        if cp.returncode!=0:result['passed']=False
        return result


def main()->None:
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--config',type=Path,required=True)
    ap.add_argument('--data',type=Path,required=True)
    ap.add_argument('--report',type=Path,help='Private combined JSON report (default: DATA/qa/production-gate.json)')
    ap.add_argument('--readiness-timeout',type=float,default=60.0)
    ap.add_argument('--data-plane-timeout',type=float,default=240.0)
    ap.add_argument('--json-only',action='store_true')
    a=ap.parse_args()
    if a.readiness_timeout<=0 or a.data_plane_timeout<=0:raise SystemExit('Timeouts must be positive')
    report_path=a.report or (a.data/'qa/production-gate.json')
    started=time.time()
    readiness=readiness_phase(a.config,a.data,a.readiness_timeout)
    data_plane=data_plane_phase(a.config,a.data_plane_timeout)
    result=combine(readiness,data_plane,started_at=started,finished_at=time.time())
    try:
        atomic_report(report_path,result)
        result['report_path']=str(report_path)
    except Exception as ex:
        result['production_gate_passed']=False
        result['report_error']=type(ex).__name__+': '+str(ex)
    if not a.json_only:
        print('DARK XRAY PRODUCTION GATE',VERSION)
        print('PASS' if result['production_gate_passed'] else 'FAIL')
        print(' Readiness :', 'PASS' if readiness.get('ready') is True else 'FAIL')
        if data_plane.get('skipped') is True:
            print(' Data plane:', 'SKIPPED - '+str(data_plane.get('reason','unknown reason')))
        else:
            print(' Data plane:', 'PASS' if data_plane.get('passed') is True else 'FAIL')
        print(' Installed customer DB modified: no')
        print(' Installed firewall modified   : no')
        print(' Report:',result.get('report_path',str(report_path)))
        print('\nJSON RESULT')
    print(json.dumps(result,ensure_ascii=False,indent=2))
    raise SystemExit(0 if result['production_gate_passed'] else 1)


if __name__=='__main__':main()
