#!/usr/bin/env python3
"""Disposable CI-only Node deployment/update/rollback test with real Xray.

Never run against a customer Node. Requires the fixed ci-update-001 fixture,
an explicit GITHUB_ACTIONS=true flag, and an exact source SHA. The sole injected
fault is a test-local health-probe exception; product code has no fault flags.
"""
from __future__ import annotations
import argparse,hashlib,http.client,http.server,importlib.util,json,os,pwd,re,socket,ssl,sqlite3,subprocess,tempfile,threading,time,uuid
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
APP=Path('/opt/dark-xray-node');DATA=Path('/var/lib/dark-xray-node');CONF=Path('/etc/dark-xray-node')
NODE='ci-update-001';ORIGIN='https://node.example.test:9443'
CLIENT_ID='77777777-7777-4777-8777-777777777777'


def require(value,message):
    if not value:raise RuntimeError(message)


def port():
    with socket.socket() as s:s.bind(('127.0.0.1',0));return s.getsockname()[1]


class LocalTLS(http.client.HTTPSConnection):
    def connect(self):
        raw=socket.create_connection(('127.0.0.1',9443),timeout=3)
        try:self.sock=self._context.wrap_socket(raw,server_hostname='node.example.test')
        except BaseException:raw.close();raise


def agent_request(path,body=None):
    token=(DATA/'token').read_text().strip()
    c=LocalTLS('node.example.test',9443,timeout=15,context=ssl.create_default_context())
    try:
        headers={'Authorization':'Bearer '+token,'Content-Type':'application/json','Connection':'close'}
        c.request('POST' if body is not None else 'GET',path,body=json.dumps(body).encode() if body is not None else None,headers=headers)
        response=c.getresponse();raw=response.read(2*1024*1024)
        require(response.status==200,'Agent request failed: '+path+' HTTP '+str(response.status))
        return json.loads(raw)
    finally:c.close()


def wait_health(expected):
    deadline=time.monotonic()+25
    while True:
        try:
            d=agent_request('/node/api/health')
            require(d.get('node_id')==NODE and d.get('agent_only') is True,'Wrong Node identity')
            require(d.get('installed_source',{}).get('commit')==expected,'Wrong installed SHA')
            require(d.get('core',{}).get('state')=='running','Owned Xray is not running')
            return d
        except (OSError,ValueError,RuntimeError,http.client.HTTPException):
            if time.monotonic()>=deadline:raise
            time.sleep(.5)


class Target(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body=b'DARK-NODE-REAL-PROXY-OK'
        self.send_response(200);self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
    def log_message(self,*args):pass


def proxy_request(proxy_port,target_port):
    deadline=time.monotonic()+15
    while True:
        c=http.client.HTTPConnection('127.0.0.1',proxy_port,timeout=3)
        try:
            # Absolute URI sends the request through the local Xray HTTP proxy,
            # then the real Node VLESS inbound; no environment proxy bypass.
            c.request('GET',f'http://127.0.0.1:{target_port}/node-test',headers={'Connection':'close'})
            r=c.getresponse();body=r.read(4096)
            require(r.status==200 and body==b'DARK-NODE-REAL-PROXY-OK','VLESS data plane failed')
            return
        except (OSError,RuntimeError,http.client.HTTPException):
            if time.monotonic()>=deadline:raise
            time.sleep(.5)
        finally:c.close()


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--expected-commit',required=True)
    p.add_argument('--report',type=Path,default=ROOT/'qa/node-update-systemd.json')
    a=p.parse_args()
    require(os.geteuid()==0 and os.environ.get('GITHUB_ACTIONS')=='true','Only the disposable root CI fixture is allowed')
    require(bool(re.fullmatch(r'[0-9a-f]{40}',a.expected_commit)),'Exact expected source SHA required')
    cfg=json.loads((CONF/'config.json').read_text())
    source=json.loads((DATA/'installed-source.json').read_text())
    require(cfg.get('public_origin')==ORIGIN and (DATA/'node-id').read_text().strip()==NODE,'Refusing a non-CI Node')
    require(source.get('ref')=='ci-node-update' and source.get('commit')==a.expected_commit,'Refusing a non-CI source fixture')
    report={'passed':False,'commit':a.expected_commit,'real_xray':True,'real_systemd':True,
            'tls_fixture_only':True,'wan_tested':False,'checks':[]}
    proxy=None;target=None;thread=None
    try:
        wait_health(a.expected_commit)
        data_port=port();proxy_port=port()
        payload={'schema':1,'nodeId':NODE,'desiredRunning':True,'files':[],
          'sections':{'outbounds':[{'tag':'direct','protocol':'freedom','settings':{}},{'tag':'block','protocol':'blackhole','settings':{}}],
            'routing':{'domainStrategy':'AsIs','rules':[]},'dns':{'servers':['1.1.1.1']},'policy':{},'observatory':{},
            'ipguard':{'mode':'observe','window_seconds':120,'ban_seconds':1800,'exempt_ips':[]}},
          'assignments':[{'sourceInboundId':41,'inbound':{'remark':'CI NODE DATA','listen':'127.0.0.1','port':data_port,
            'protocol':'vless','enable':True,'tag':'ci-node-data','settings':{'decryption':'none'},
            'streamSettings':{'network':'tcp','security':'none'},'sniffing':{}},
            'clients':[{'sourceEmail':'ci-data','client':{'id':CLIENT_ID,'enable':True}}]}],
          'security':{'clients':[{'sourceEmail':'ci-data','limitIp':0,'limitHwid':0,'globalIpBlocked':False,'globalDeviceBlocked':False}]}}
        digest=hashlib.sha256(json.dumps(payload,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()
        applied=agent_request('/node/api/v1/state/apply',{'revision':1,'hash':digest,'payload':payload})
        require(applied.get('appliedHash')==digest and applied.get('appliedRevision')==1,'Wrong deployment acknowledgement')
        report['checks'].append('real Node desired-state apply')
        target=http.server.ThreadingHTTPServer(('127.0.0.1',0),Target)
        thread=threading.Thread(target=target.serve_forever,daemon=True);thread.start()
        with tempfile.TemporaryDirectory(prefix='dark-node-update-ci.') as td:
            tmp=Path(td)
            client_config={'log':{'loglevel':'warning'},'inbounds':[{'listen':'127.0.0.1','port':proxy_port,'protocol':'http','settings':{}}],
              'outbounds':[{'protocol':'vless','settings':{'vnext':[{'address':'127.0.0.1','port':data_port,
                'users':[{'id':CLIENT_ID,'encryption':'none'}]}]},'streamSettings':{'network':'tcp','security':'none'}}]}
            path=tmp/'client.json';path.write_text(json.dumps(client_config));path.chmod(0o600)
            with (tmp/'client.log').open('wb') as log:
                proxy=subprocess.Popen([cfg['xray_binary'],'run','-config',str(path)],stdout=log,stderr=subprocess.STDOUT)
                proxy_request(proxy_port,target.server_port)
                report['checks'].append('real VLESS traffic before update')
                spec=importlib.util.spec_from_file_location('node_updater_ci',ROOT/'tools/update_node.py')
                updater=importlib.util.module_from_spec(spec);spec.loader.exec_module(updater)
                checked=subprocess.run(['git','-c','safe.directory='+str(ROOT),'-C',str(ROOT),'rev-parse','HEAD'],
                                       check=True,capture_output=True,text=True,timeout=10)
                require(checked.stdout.strip()==a.expected_commit,'Checkout SHA changed')
                version=updater.validate_source(ROOT);venv=updater.build_candidate_venv(ROOT,tmp)
                rollback_root=APP/'.rollback';rollback_root.mkdir(mode=0o700,exist_ok=True)
                first=rollback_root/('ci-success-'+uuid.uuid4().hex);first.mkdir(mode=0o700)
                updater.activate_candidate(ROOT,venv,a.expected_commit,version,'ci-node-update',first)
                wait_health(a.expected_commit);proxy_request(proxy_port,target.server_port)
                report['checks'].append('real Node update and VLESS traffic after service restart')
                second=rollback_root/('ci-failure-'+uuid.uuid4().hex);second.mkdir(mode=0o700)
                real_probe=updater.health_probe;injected=False
                def fail_once(*args,**kwargs):
                    nonlocal injected
                    result=real_probe(*args,**kwargs)
                    if not injected:
                        injected=True
                        with sqlite3.connect(DATA/'node.sqlite3') as db:db.execute('CREATE TABLE ci_new_schema_probe(value TEXT)')
                        raise RuntimeError('CI injected post-start health failure')
                    return result
                updater.health_probe=fail_once
                try:
                    updater.activate_candidate(ROOT,venv,a.expected_commit,version,'ci-node-update',second)
                    raise RuntimeError('Injected failure was not propagated')
                except RuntimeError as exc:
                    require('previous source, environment and database restored' in str(exc),'Rollback did not complete')
                finally:updater.health_probe=real_probe
                require(injected,'Fault injection did not execute')
                wait_health(a.expected_commit);proxy_request(proxy_port,target.server_port)
                with sqlite3.connect(DATA/'node.sqlite3') as db:
                    require(db.execute('PRAGMA quick_check').fetchone()[0]=='ok','Rollback SQLite is corrupt')
                    require(not db.execute("SELECT 1 FROM sqlite_master WHERE name='ci_new_schema_probe'").fetchone(),'Candidate schema was not rolled back')
                state=agent_request('/node/api/v1/state')
                require(state.get('appliedRevision')==1 and state.get('appliedHash')==digest,'Deployment state was lost during update')
                metadata=(DATA/'installed-source.json').stat()
                require(metadata.st_gid==pwd.getpwnam('darkxray').pw_gid and metadata.st_mode&0o777==0o640,'Agent source readability was lost')
                report['checks'].extend(['injected failure restores source, environment and SQLite',
                                         'real VLESS traffic after rollback','revision and source metadata preserved'])
        report['passed']=True
    except Exception as exc:
        report['error']=type(exc).__name__+': '+str(exc)[:800]
        raise
    finally:
        if proxy is not None:
            proxy.terminate()
            try:proxy.wait(timeout=8)
            except subprocess.TimeoutExpired:proxy.kill();proxy.wait()
        if target is not None:target.shutdown();target.server_close()
        if thread is not None:thread.join(timeout=5)
        a.report.parent.mkdir(parents=True,exist_ok=True);a.report.write_text(json.dumps(report,indent=2)+'\n')
        print(json.dumps(report,indent=2))


if __name__=='__main__':main()
