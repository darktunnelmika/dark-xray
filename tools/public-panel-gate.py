#!/usr/bin/env python3
"""Read-only external acceptance gate for a public DARK XRAY panel endpoint.

Run this from a machine outside the target VPS/provider. It uses ordinary public
DNS resolution and the OS public CA trust store. It does not log in, mutate the
panel, follow redirects, disable TLS verification, or change DNS/firewall state.
"""
from __future__ import annotations

import argparse
import ipaddress
import json
import math
import os
import re
import socket
import ssl
import tempfile
import time
from pathlib import Path
from typing import Any

DOMAIN_RE=re.compile(r'(?=.{1,253}$)(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,63}')


def atomic_report(path:Path,payload:dict[str,Any])->None:
    path=path.expanduser();path.parent.mkdir(parents=True,exist_ok=True)
    if path.is_symlink():raise RuntimeError('Refusing symlink report path')
    fd,name=tempfile.mkstemp(prefix='.public-panel-',suffix='.json',dir=path.parent);tmp=Path(name)
    try:
        with os.fdopen(fd,'w',encoding='utf-8') as f:
            json.dump(payload,f,ensure_ascii=False,indent=2);f.write('\n');f.flush();os.fsync(f.fileno())
        os.chmod(tmp,0o600);os.replace(tmp,path)
    finally:tmp.unlink(missing_ok=True)


def resolve_public(domain:str,port:int)->list[str]:
    values=set()
    for info in socket.getaddrinfo(domain,port,type=socket.SOCK_STREAM):
        value=str(info[4][0]).split('%',1)[0]
        ip=ipaddress.ip_address(value)
        if not ip.is_global:raise RuntimeError('DNS returned a non-public address')
        values.add(str(ip))
    if not values:raise RuntimeError('Public DNS returned no address')
    return sorted(values,key=lambda x:(ipaddress.ip_address(x).version,x))


def dns_evidence(resolved:list[str],expected:list[str],require_exact:bool)->dict[str,Any]:
    actual={str(ipaddress.ip_address(x)) for x in resolved}
    wanted={str(ipaddress.ip_address(x)) for x in expected}
    expected_public=all(ipaddress.ip_address(x).is_global for x in wanted)
    exact=bool(wanted and actual==wanted)
    if require_exact:ok=bool(wanted and expected_public and exact)
    else:ok=bool(actual and (not wanted or (expected_public and wanted.issubset(actual))))
    return {'ok':ok,'resolved':sorted(actual),'expected':sorted(wanted),
            'exact_match':exact,'exact_required':bool(require_exact),'expected_public':expected_public}


def _issuer(cert:dict)->str:
    parts=[]
    for group in cert.get('issuer',()):
        for key,value in group:
            if key in {'organizationName','commonName','countryName'}:parts.append(str(value))
    return ' / '.join(parts)[:300]


def parse_health_response(raw:bytes)->dict[str,Any]:
    if len(raw)>65536:raise ValueError('HTTP response too large')
    head,sep,body=raw.partition(b'\r\n\r\n')
    if not sep:raise ValueError('Incomplete HTTP response')
    lines=head.decode('iso-8859-1','strict').split('\r\n')
    pieces=lines[0].split()
    if len(pieces)<2 or not pieces[1].isdigit():raise ValueError('Invalid HTTP status')
    status=int(pieces[1]);headers={}
    for row in lines[1:]:
        if ':' not in row:continue
        k,v=row.split(':',1);headers[k.strip().lower()]=v.strip()
    if status!=200:raise ValueError('Public health status is not 200')
    if len(body)>8192:raise ValueError('Public health body too large')
    value=json.loads(body)
    if not isinstance(value,dict) or value.get('service')!='DARK XRAY' or value.get('mode')!='standalone':
        raise ValueError('Public endpoint is not DARK XRAY standalone health')
    hsts=headers.get('strict-transport-security','')
    if not re.search(r'(?:^|;)\s*max-age=[1-9][0-9]*(?:;|$)',hsts,re.I):
        raise ValueError('Public HTTPS response lacks positive HSTS')
    return {'status':status,'hsts':hsts[:500],'service':'DARK XRAY','mode':'standalone'}


def probe(domain:str,port:int,address:str,timeout:float)->dict[str,Any]:
    raw=None
    started=time.monotonic()
    try:
        raw=socket.create_connection((address,port),timeout=timeout)
        context=ssl.create_default_context()
        with context.wrap_socket(raw,server_hostname=domain) as conn:
            raw=None;conn.settimeout(timeout)
            cert=conn.getpeercert()
            host=f'{domain}:{port}'
            conn.sendall(('GET /health HTTP/1.1\r\nHost: '+host+
                          '\r\nConnection: close\r\nUser-Agent: darkxray-public-gate\r\n\r\n').encode('ascii'))
            received=bytearray()
            while True:
                chunk=conn.recv(4096)
                if not chunk:break
                received.extend(chunk)
                if len(received)>65536:raise ValueError('HTTP response too large')
            health=parse_health_response(bytes(received))
            return {'ok':True,'address':address,'tls_version':conn.version(),
                    'certificate_verified':True,'certificate_not_after':cert.get('notAfter',''),
                    'certificate_issuer':_issuer(cert),'health':health,
                    'seconds':round(time.monotonic()-started,3)}
    except Exception as ex:
        return {'ok':False,'address':address,'certificate_verified':False,
                'error':type(ex).__name__+': '+str(ex)[:500],
                'seconds':round(time.monotonic()-started,3)}
    finally:
        if raw is not None:
            try:raw.close()
            except Exception:pass


def main()->None:
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--domain',required=True)
    ap.add_argument('--port',type=int,default=2087)
    ap.add_argument('--expected-ip',action='append',default=[])
    ap.add_argument('--require-exact-dns',action='store_true')
    ap.add_argument('--timeout',type=float,default=8.0)
    ap.add_argument('--report',type=Path,default=Path('qa/public-panel-gate.json'))
    ap.add_argument('--json-only',action='store_true')
    a=ap.parse_args()
    domain=a.domain.strip().lower().rstrip('.')
    if not DOMAIN_RE.fullmatch(domain):raise SystemExit('Use an ASCII public domain without protocol/path/wildcard')
    if not 1<=a.port<=65535:raise SystemExit('Invalid public panel port')
    if not math.isfinite(a.timeout) or not 1<=a.timeout<=20:raise SystemExit('Timeout must be 1..20 seconds')
    try:expected=[str(ipaddress.ip_address(x)) for x in a.expected_ip]
    except ValueError:raise SystemExit('--expected-ip must contain literal IPv4/IPv6 addresses')
    started=time.time()
    try:
        resolved=resolve_public(domain,a.port)
        dns=dns_evidence(resolved,expected,a.require_exact_dns)
        targets=sorted({*expected} if expected else set(resolved))
        probes=[probe(domain,a.port,address,a.timeout) for address in targets]
        passed=bool(dns['ok'] and probes and all(x['ok'] for x in probes))
        error=''
    except Exception as ex:
        resolved=[];dns={'ok':False,'resolved':[],'expected':expected,'exact_required':bool(a.require_exact_dns)}
        probes=[];passed=False;error=type(ex).__name__+': '+str(ex)[:500]
    result={'public_panel_gate_passed':passed,'domain':domain,'port':a.port,'dns':dns,'probes':probes,
            'started_at':started,'finished_at':time.time(),'error':error,
            'changes_made':False,
            'limitations':{'dns_vantage':'resolver used by this external machine','public_ca_trust_tested':True,
                           'login_tested':False,'provider_firewall_inferred_only_from_reachability':True,
                           'all_internet_vantages_tested':False}}
    atomic_report(a.report,result);result['report_path']=str(a.report)
    if not a.json_only:
        print('DARK XRAY PUBLIC PANEL GATE')
        print('PASS' if passed else 'FAIL','·',domain+':'+str(a.port))
        print(' DNS  :','PASS' if dns.get('ok') else 'FAIL',','.join(dns.get('resolved',[])))
        for item in probes:print(' HTTPS:',item['address'],'PASS' if item['ok'] else 'FAIL')
        print(' Report:',a.report)
        print('\nJSON RESULT')
    print(json.dumps(result,ensure_ascii=False,indent=2))
    raise SystemExit(0 if passed else 1)


if __name__=='__main__':main()
