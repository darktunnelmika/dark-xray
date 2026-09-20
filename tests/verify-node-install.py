#!/usr/bin/env python3
"""Read-only checks for the disposable CI Node installation.

Run as root: the private state directory intentionally remains mode 0700.
Never print token/pair-code contents or relax permissions to make a test pass.
The CI certificate is explicitly trusted; TLS verification is never disabled.
"""
from __future__ import annotations

import argparse
import http.client
import json
import os
import pwd
import re
import socket
import ssl
import stat
import subprocess
import time
from pathlib import Path

APP=Path('/opt/dark-xray-node')
DATA=Path('/var/lib/dark-xray-node')
CONF=Path('/etc/dark-xray-node')
UNITS=('dark-xray-node.service','dark-xray-node-guard.service','dark-xray-node-update.service')


def require(condition:bool,message:str)->None:
    if not condition:raise RuntimeError(message)


def regular(path:Path,mode:int|None=None,uid:int|None=None)->None:
    info=path.lstat()
    require(stat.S_ISREG(info.st_mode),'Expected regular file: '+str(path))
    if mode is not None:require(stat.S_IMODE(info.st_mode)==mode,'Wrong file mode: '+str(path))
    if uid is not None:require(info.st_uid==uid,'Wrong file owner: '+str(path))


def filesystem(expected:str)->dict:
    account=pwd.getpwnam('darkxray')
    info=DATA.lstat()
    require(stat.S_ISDIR(info.st_mode) and stat.S_IMODE(info.st_mode)==0o700,'Node state must remain a private directory')
    require(info.st_uid==account.pw_uid,'Node state owner mismatch')
    for path in (APP/'web',APP/'backend/server.py',APP/'backend/auth.py',Path('/opt/dark-xray'),Path('/etc/dark-xray'),CONF/'pair.json'):
        require(not os.path.lexists(path),'Unexpected full-panel or legacy artifact: '+str(path))
    for name in ('token','node-id','pair.json'):
        regular(DATA/name,0o600,account.pw_uid)
    for name in ('node_agent.py','node_runtime.py','node_updated.py'):
        regular(APP/'backend'/name)
    regular(DATA/'installed-source.json')
    identity=(DATA/'node-id').read_text(encoding='utf-8').strip()
    pair=json.loads((DATA/'pair.json').read_text(encoding='utf-8'))
    source=json.loads((DATA/'installed-source.json').read_text(encoding='utf-8'))
    require(identity=='ci-node-001','Unexpected CI Node identity')
    require(pair.get('nodeId')==identity,'Pair identity mismatch')
    require(str(pair.get('pairCode','')).startswith('DXN1.'),'Pair code missing')
    require(source.get('role')=='node-agent' and source.get('commit')==expected,'Installed source identity mismatch')
    services={}
    for unit in UNITS:
        for action in ('is-enabled','is-active'):
            cp=subprocess.run(['systemctl',action,'--quiet',unit],capture_output=True,timeout=10,check=False)
            require(cp.returncode==0,unit+' failed '+action)
        services[unit]='enabled/active'
    return {'passed':True,'node_id':identity,'source_commit':expected,'private_state_mode':'0700',
            'secret_file_modes':'0600','agent_only':True,'services':services,'secrets_disclosed':False}


class FixtureHTTPS(http.client.HTTPSConnection):
    """Dial loopback but verify the certificate for the CI fixture hostname."""
    def connect(self):
        raw=socket.create_connection(('127.0.0.1',self.port),timeout=self.timeout)
        try:self.sock=self._context.wrap_socket(raw,server_hostname=self.host)
        except BaseException:
            raw.close();raise


def health(expected:str,ca:Path)->dict:
    regular(ca)
    token=(DATA/'token').read_text(encoding='utf-8').strip()
    context=ssl.create_default_context(cafile=str(ca))
    require(context.check_hostname and context.verify_mode==ssl.CERT_REQUIRED,'TLS verification must be enabled')

    def request(path:str,credential:str|None)->tuple[int,dict,bytes]:
        connection=FixtureHTTPS('node.example.test',9443,timeout=3,context=context)
        headers={'Connection':'close'}
        if credential is not None:headers['Authorization']='Bearer '+credential
        try:
            connection.request('GET',path,headers=headers)
            response=connection.getresponse();raw=response.read(1024*1024+1)
            require(len(raw)<=1024*1024,'Oversized Node health response')
            return response.status,dict(response.getheaders()),raw
        finally:connection.close()

    deadline=time.monotonic()+25
    while True:
        try:
            status,headers,raw=request('/node/api/health',token)
            require(status==200,'Authenticated Node health did not return HTTP 200')
            doc=json.loads(raw)
            require(doc.get('core',{}).get('state')=='running','Owned Xray process is not running')
            break
        except (OSError,ValueError,RuntimeError,http.client.HTTPException):
            if time.monotonic()>=deadline:raise
            time.sleep(.5)
    require(doc.get('service')=='DARK XRAY NODE' and doc.get('agent_only') is True,'Wrong service identity')
    require(doc.get('node_id')=='ci-node-001','Wrong authenticated Node identity')
    require(doc.get('installed_source',{}).get('commit')==expected,'Health reports the wrong installed source')
    require(doc.get('installed_source',{}).get('role')=='node-agent','Health reports the wrong role')
    lowered={k.lower():v for k,v in headers.items()}
    require(lowered.get('cache-control')=='no-store','Private Node responses must not be cached')
    for credential in (None,'dkn_'+('X'*60)):
        require(request('/node/api/health',credential)[0]==401,'Missing/invalid credential was not rejected')
    for path in ('/','/api/auth/login','/assets/live.js','/docs','/openapi.json'):
        require(request(path,token)[0]==404,'Unexpected panel surface on the lightweight Agent')
    require(request('/node/api/mirrors/security',token)[0]==200,'Authenticated security snapshot failed')
    require(request('/node/api/mirrors/traffic',token)[0]==200,'Authenticated traffic snapshot failed')
    return {'passed':True,'node_id':doc['node_id'],'source_commit':expected,'agent_only':True,
            'core':doc['core'],'tls_certificate_verified':True,'tls_fixture_only':True,
            'unauthorized_requests_rejected':True,'panel_routes_absent':True,'secrets_disclosed':False}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--phase',choices=('filesystem','health'),required=True)
    parser.add_argument('--expected-commit',required=True)
    parser.add_argument('--ca',type=Path,default=Path('/tmp/dark-node-cert.pem'))
    args=parser.parse_args()
    require(os.geteuid()==0,'Run the verification as root; do not relax Node state permissions')
    require(bool(re.fullmatch(r'[0-9a-f]{40}',args.expected_commit)),'Expected a full commit SHA')
    try:
        result=filesystem(args.expected_commit) if args.phase=='filesystem' else health(args.expected_commit,args.ca)
    except Exception as exc:
        # Errors deliberately omit response bodies and credential-bearing files.
        print(json.dumps({'passed':False,'phase':args.phase,'error_type':type(exc).__name__,
                          'error':str(exc)[:300]},indent=2))
        raise SystemExit(1)
    print(json.dumps(result,indent=2))


if __name__=='__main__':main()
