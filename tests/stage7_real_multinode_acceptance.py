#!/usr/bin/env python3
"""Stage 7.4 real-process, real-TLS, multi-Node Smart Routing acceptance.

Disposable only: all state lives below a temporary directory. Xray is the explicit
fake fixture; Hub/Node Agent processes, HTTPS sockets, auth, desired-state, rollout
threads and process restart/outage behavior are real.
"""
from __future__ import annotations

import argparse
import ipaddress
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from datetime import datetime,timedelta,timezone
from pathlib import Path

import httpx
from cryptography import x509
from cryptography.hazmat.primitives import hashes,serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID,NameOID

ROOT=Path(__file__).resolve().parents[1]
RUNNER=ROOT/'tests/stage7_real_process.py'
FAKE=ROOT/'tests/fixtures/fake_xray.py'
PASSWORD='Stage7!Acceptance2026'
NODES=[
    ('node-us','USA','node-us.stage7.test'),
    ('node-de','Germany','node-de.stage7.test'),
    ('node-fr','France','node-fr.stage7.test'),
    ('node-uk','UK','node-uk.stage7.test'),
]


def free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1',0));return s.getsockname()[1]


def write_json(path:Path,value):
    path.write_text(json.dumps(value,indent=2,sort_keys=True)+'\n');path.chmod(0o600)


def make_ca(root:Path):
    now=datetime.now(timezone.utc)
    key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
    name=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'DARK Stage7.4 disposable CA')])
    cert=(x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
          .serial_number(x509.random_serial_number()).not_valid_before(now-timedelta(days=1))
          .not_valid_after(now+timedelta(days=2)).add_extension(x509.BasicConstraints(ca=True,path_length=0),True)
          .add_extension(x509.KeyUsage(False,False,False,False,False,True,True,None,None),True)
          .sign(key,hashes.SHA256()))
    path=root/'ca.pem';path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return key,cert,path


def make_cert(root:Path,ca_key,ca_cert,name:str,*,dns:str|None=None,ip:str|None=None):
    now=datetime.now(timezone.utc);key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
    sans=[]
    if dns:sans.append(x509.DNSName(dns))
    if ip:sans.append(x509.IPAddress(ipaddress.ip_address(ip)))
    cert=(x509.CertificateBuilder().subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,name)]))
          .issuer_name(ca_cert.subject).public_key(key.public_key()).serial_number(x509.random_serial_number())
          .not_valid_before(now-timedelta(days=1)).not_valid_after(now+timedelta(days=1))
          .add_extension(x509.SubjectAlternativeName(sans),False)
          .add_extension(x509.BasicConstraints(ca=False,path_length=None),True)
          .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]),False)
          .sign(ca_key,hashes.SHA256()))
    cert_path=root/(name+'.pem');key_path=root/(name+'.key')
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,
                                            serialization.NoEncryption()));key_path.chmod(0o600)
    return cert_path,key_path


class Process:
    def __init__(self,cmd,log):
        self.log_path=Path(log);self.log=self.log_path.open('wb')
        self.p=subprocess.Popen([str(x) for x in cmd],cwd=ROOT,stdout=self.log,stderr=subprocess.STDOUT,
                                env={**os.environ,'PYTHONUNBUFFERED':'1'})
    @property
    def pid(self):return self.p.pid
    def stop(self):
        if self.p.poll() is None:
            self.p.terminate()
            try:self.p.wait(8)
            except subprocess.TimeoutExpired:
                self.p.kill();self.p.wait(5)
        self.log.flush();self.log.close()
    def assert_alive(self):
        if self.p.poll() is not None:
            try:text=self.log_path.read_text(errors='replace')[-5000:]
            except Exception:text=''
            raise AssertionError(f'process exited {self.p.returncode}: {text}')


def wait_https(url,ca,headers=None,timeout=12):
    deadline=time.time()+timeout;last=None
    while time.time()<deadline:
        try:
            with httpx.Client(verify=str(ca),trust_env=False,timeout=1.5) as client:
                r=client.get(url,headers=headers or {})
            if r.status_code<500:return r
            last=f'{r.status_code} {r.text[:200]}'
        except Exception as exc:last=repr(exc)
        time.sleep(.1)
    raise AssertionError(f'HTTPS readiness failed {url}: {last}')


def config(public_origin,public_address,port,cert,key,fake,api_port,guard):
    return {
        'public_origin':public_origin,'panel_path':'/','xray_binary':str(fake),'xray_assets':str(fake.parent),
        'xray_api_port':api_port,'public_address':public_address,'writes_enabled':True,'secure_cookie':True,
        'poll_seconds':5,'core_autostart':True,'ip_window_seconds':120,'direct_source_verified':False,
        'protected_ports':[22,port,api_port],'test_engine':False,'bind_host':'0.0.0.0','bind_port':port,
        'tls_certificate':str(cert),'tls_private_key':str(key),'guard_socket':str(guard),
        'ip_ban_seconds':1800,'ip_exempt_ips':[]
    }


def login(origin,ca):
    client=httpx.Client(base_url=origin,verify=str(ca),trust_env=False,timeout=10)
    r=client.post('/api/auth/login',json={'username':'dark','password':PASSWORD})
    assert r.status_code==200,r.text
    client.headers['X-Dark-CSRF']=r.json()['csrf']
    return client


def api(client,method,path,**kwargs):
    r=client.request(method,path,**kwargs)
    assert r.status_code<400,f'{method} {path}: {r.status_code} {r.text[:1000]}'
    return r.json() if r.content else {}


def wait_rollout(client,rid,pred,timeout=35):
    deadline=time.time()+timeout;doc=None
    while time.time()<deadline:
        doc=api(client,'GET','/api/smart-routing/rollout/'+rid)
        if pred(doc):return doc
        time.sleep(.15)
    raise AssertionError('rollout timeout: '+json.dumps(doc,default=str)[:2000])


def review_and_safety(client):
    body={'warpAi':True,'adblock':True,'warpOutboundTags':['warp-us','warp-de'],
          'warpNodeIds':['node-us','node-de'],'adblockNodeIds':['node-fr','node-uk']}
    valid=api(client,'POST','/api/smart-routing/validate',json=body)
    reviewed=api(client,'POST','/api/smart-routing/review',json={**body,
        'baselineHash':valid['baselineHash'],'candidateHash':valid['candidateHash'],
        'confirmation':'REVIEW SMART ROUTING'})
    safe=api(client,'POST','/api/smart-routing/safety-check',json={
        'revisionId':reviewed['revisionId'],'attempts':2,'timeoutSeconds':3,
        'maxLossPercent':20,'maxLatencyMs':1200,'maxJitterMs':350})
    assert safe['safetyPassed'] is True,safe
    return reviewed['revisionId']


def start_rollout(client,revision,seconds=5):
    return api(client,'POST','/api/smart-routing/rollout/start',json={
        'revisionId':revision,'confirmation':'START STAGED ROLLOUT','observationSeconds':seconds})['rolloutId']


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--report',type=Path,required=True)
    args=ap.parse_args();args.report.parent.mkdir(parents=True,exist_ok=True)
    root=Path(tempfile.mkdtemp(prefix='dark-stage74.'))
    processes={};hub=None;client=None
    evidence={'schema':1,'stage':'7.4','name':'Real multi-Node Smart Routing acceptance',
              'productionMutation':False,'productionPathsTouched':[],'passed':False,'scenarios':[]}
    try:
        ca_key,ca_cert,ca=make_ca(root)
        fake=root/'fake-xray';shutil.copy2(FAKE,fake);fake.chmod(0o755)
        node_meta={}
        for idx,(node_id,region,host) in enumerate(NODES,1):
            nroot=root/node_id;nroot.mkdir()
            cert,key=make_cert(nroot,ca_key,ca_cert,node_id,dns=host,ip='127.0.0.1')
            port=free_port();api_port=free_port();token='dkn_'+(chr(64+idx)*60)
            data=nroot/'data';data.mkdir();token_file=data/'token';token_file.write_text(token+'\n');token_file.chmod(0o600)
            node_id_file=data/'node-id';node_id_file.write_text(node_id+'\n');node_id_file.chmod(0o600)
            control=nroot/'warp.json';write_json(control,{'mode':'healthy'})
            cfg=nroot/'config.json';write_json(cfg,config(f'https://{host}:{port}',host,port,cert,key,fake,api_port,nroot/'guard.sock'))
            node_meta[node_id]={'id':node_id,'region':region,'host':host,'port':port,'token':token,'root':nroot,
                                'config':cfg,'data':data,'token_file':token_file,'node_id_file':node_id_file,
                                'control':control,'origin':f'https://{host}:{port}'}

        def start_node(node_id):
            m=node_meta[node_id]
            p=Process([sys.executable,RUNNER,'node','--config',m['config'],'--data',m['data'],
                       '--token-file',m['token_file'],'--node-id-file',m['node_id_file'],
                       '--port',m['port'],'--warp-control',m['control']],m['root']/'agent.log')
            processes[node_id]=p
            r=wait_https(f"https://127.0.0.1:{m['port']}/node/api/health",ca,
                         {'Authorization':'Bearer '+m['token'],'Host':m['host']+':'+str(m['port'])})
            assert r.status_code==200 and r.json()['service']=='DARK XRAY NODE',r.text
            return p

        for node_id,_,_ in NODES:start_node(node_id)

        hroot=root/'hub';hroot.mkdir();hdata=hroot/'data';hdata.mkdir()
        hcert,hkey=make_cert(hroot,ca_key,ca_cert,'hub',ip='127.0.0.1')
        hport=free_port();hapi=free_port();hcfg=hroot/'config.json'
        write_json(hcfg,config(f'https://127.0.0.1:{hport}','127.0.0.1',hport,hcert,hkey,fake,hapi,hroot/'guard.sock'))
        hosts=hroot/'hosts.json';write_json(hosts,[x[2] for x in NODES])
        init=subprocess.run([sys.executable,ROOT/'backend/server.py','--config',hcfg,'--data',hdata,'init',
                             '--username','dark','--password-stdin'],cwd=ROOT,input=PASSWORD+'\n'+PASSWORD+'\n',
                            text=True,capture_output=True,timeout=20)
        assert init.returncode==0,init.stdout+init.stderr

        def start_hub():
            nonlocal hub
            hub=Process([sys.executable,RUNNER,'hub','--config',hcfg,'--data',hdata,'--port',hport,
                         '--ca',ca,'--hosts',hosts],hroot/'hub.log')
            wait_https(f'https://127.0.0.1:{hport}/health',ca)
            return hub
        start_hub();origin=f'https://127.0.0.1:{hport}';client=login(origin,ca)

        outbounds=[
            {'tag':'direct','protocol':'freedom','settings':{}},
            {'tag':'block','protocol':'blackhole','settings':{}},
            {'tag':'warp-us','protocol':'wireguard','settings':{'secretKey':'fixture-us','address':['172.16.10.2/32'],
                'peers':[{'publicKey':'peer-us','endpoint':'162.159.192.1:2408'}]}},
            {'tag':'warp-de','protocol':'wireguard','settings':{'secretKey':'fixture-de','address':['172.16.20.2/32'],
                'peers':[{'publicKey':'peer-de','endpoint':'162.159.192.2:2408'}]}},
        ]
        api(client,'PUT','/api/settings/outbounds',json={'value':outbounds})
        for node_id,region,host in NODES:
            m=node_meta[node_id]
            api(client,'POST','/api/nodes',json={'id':node_id,'name':region,'origin':m['origin'],'token':m['token'],
                'enabled':True,'dataAddress':'','priority':100,'failoverEnabled':True,'inboundIds':[]})
            api(client,'POST',f'/api/nodes/{node_id}/probe')
            api(client,'POST',f'/api/nodes/{node_id}/sync')
            health=api(client,'POST',f'/api/nodes/{node_id}/probe')
            assert health['health']['core']['state']=='running',health

        evidence['processes']={'hub':hub.pid,**{k:v.pid for k,v in processes.items()}}
        assert len(set(evidence['processes'].values()))==5

        # 1) Pause -> real Hub process restart -> Resume -> complete.
        revision=review_and_safety(client);rid=start_rollout(client,revision,6)
        wait_rollout(client,rid,lambda d:any(x['state']=='verifying' for x in d['items']))
        api(client,'POST',f'/api/smart-routing/rollout/{rid}/pause',json={'confirmation':'PAUSE STAGED ROLLOUT'})
        paused=wait_rollout(client,rid,lambda d:d['controlState']=='paused')
        client.close();client=None;old_hub_pid=hub.pid;hub.stop();start_hub()
        client=login(origin,ca)
        after_restart=api(client,'GET',f'/api/smart-routing/rollout/{rid}')
        assert after_restart['state']=='running' and after_restart['controlState']=='paused',after_restart
        api(client,'POST',f'/api/smart-routing/rollout/{rid}/resume',json={'confirmation':'RESUME STAGED ROLLOUT'})
        done=wait_rollout(client,rid,lambda d:d['state']!='running',timeout=40)
        assert done['state']=='completed',done
        evidence['scenarios'].append({'name':'pause_hub_restart_resume','passed':True,
            'oldHubPid':old_hub_pid,'newHubPid':hub.pid,'timelineKinds':[x['kind'] for x in done['timeline']]})

        # 2) WARP degradation after candidate apply -> automatic rollback, Hub baseline unchanged.
        baseline=api(client,'GET','/api/settings/routing')['value']
        write_json(node_meta['node-us']['control'],{'mode':'degraded'})
        revision=review_and_safety(client);rid=start_rollout(client,revision,3)
        failed=wait_rollout(client,rid,lambda d:d['state']!='running',timeout=25)
        assert failed['state']=='rolled_back',failed
        assert api(client,'GET','/api/settings/routing')['value']==baseline
        assert any(x['kind']=='warp_probe' and x['metrics'].get('passed') is False for x in failed['timeline'])
        evidence['scenarios'].append({'name':'post_apply_warp_degradation','passed':True,
            'state':failed['state'],'detail':failed['detail']})
        write_json(node_meta['node-us']['control'],{'mode':'healthy'})

        # 3) Owner Abort during real observation -> rollback and Hub unchanged.
        baseline=api(client,'GET','/api/settings/routing')['value']
        revision=review_and_safety(client);rid=start_rollout(client,revision,8)
        wait_rollout(client,rid,lambda d:any(x['state']=='verifying' for x in d['items']))
        api(client,'POST',f'/api/smart-routing/rollout/{rid}/abort',json={'confirmation':'ABORT STAGED ROLLOUT'})
        aborted=wait_rollout(client,rid,lambda d:d['state']!='running',timeout=20)
        assert aborted['state']=='aborted',aborted
        assert api(client,'GET','/api/settings/routing')['value']==baseline
        evidence['scenarios'].append({'name':'owner_abort_during_observation','passed':True,
            'state':aborted['state'],'rollback':[x['detail'] for x in aborted['timeline'] if x['kind']=='rollback_complete'][-1]})

        # 4) Real Canary Agent process outage -> rollout fails closed, restart + baseline re-sync recovers Node.
        baseline=api(client,'GET','/api/settings/routing')['value']
        revision=review_and_safety(client);rid=start_rollout(client,revision,8)
        wait_rollout(client,rid,lambda d:any(x['node_id']=='node-us' and x['state']=='verifying' for x in d['items']))
        old_node_pid=processes['node-us'].pid;processes['node-us'].stop()
        outage=wait_rollout(client,rid,lambda d:d['state']!='running',timeout=25)
        assert outage['state']=='failed',outage
        assert api(client,'GET','/api/settings/routing')['value']==baseline
        start_node('node-us')
        api(client,'POST','/api/nodes/node-us/probe')
        api(client,'POST','/api/nodes/node-us/sync')
        recovered=api(client,'POST','/api/nodes/node-us/probe')
        assert recovered['health']['core']['state']=='running',recovered
        evidence['scenarios'].append({'name':'real_canary_process_outage_and_recovery','passed':True,
            'rolloutState':outage['state'],'oldNodePid':old_node_pid,'newNodePid':processes['node-us'].pid,
            'recoveredCore':recovered['health']['core']['state']})

        evidence['tlsVerified']=True
        evidence['separateProcesses']=True
        evidence['network']='real HTTPS sockets; loopback-only disposable fixture'
        evidence['warpProbe']='test adapter controls health result; Node API/TLS/process path is real'
        evidence['passed']=all(x['passed'] for x in evidence['scenarios'])
        evidence['generatedAt']=time.time()
        args.report.write_text(json.dumps(evidence,indent=2,sort_keys=True)+'\n')
        print(json.dumps(evidence,indent=2,sort_keys=True))
        return 0
    except Exception as exc:
        evidence['passed']=False;evidence['generatedAt']=time.time();evidence['error']=type(exc).__name__+': '+str(exc)[:1500]
        tails={}
        for log in root.rglob('*.log'):
            try:tails[str(log.relative_to(root))]=log.read_text(errors='replace')[-3000:]
            except Exception:pass
        evidence['diagnosticLogTails']=tails
        try:args.report.write_text(json.dumps(evidence,indent=2,sort_keys=True)+'\n')
        except Exception:pass
        print(json.dumps(evidence,indent=2,sort_keys=True))
        raise
    finally:
        if client is not None:
            try:client.close()
            except Exception:pass
        if hub is not None:
            try:hub.stop()
            except Exception:pass
        for p in list(processes.values()):
            try:p.stop()
            except Exception:pass
        shutil.rmtree(root,ignore_errors=True)


if __name__=='__main__':
    raise SystemExit(main())
