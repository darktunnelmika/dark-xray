#!/usr/bin/env python3
"""Isolated end-to-end test with a REAL administrator-provided Xray executable.

Creates its own temporary database, loopback API/listeners and HTTP target.
Never opens the installation's customer database or changes its firewall. Reports
success only after a real SOCKS -> VLESS -> HTTP request returned the marker.
No internet destination, external panel or Xray test double is used.
"""
from __future__ import annotations
import argparse,base64,hashlib,http.cookiejar,http.server,json,os,socket,struct,subprocess,sys,tempfile,threading,time
from pathlib import Path
from urllib.parse import urlsplit,parse_qs
from urllib.request import Request,build_opener,HTTPCookieProcessor,ProxyHandler
from urllib.error import HTTPError
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))
from core import Config,CoreEngine
from dark_policy import Store,Actor
from auth import Auth
from manager import Manager
from server import make_app
import uvicorn

VERSION=(ROOT/'VERSION').read_text(encoding='utf-8').strip() if (ROOT/'VERSION').is_file() else 'unknown'
MARKER=(f'DARK-XRAY-REAL-E2E-{VERSION}\n').encode()+b'x'*4096

class Target(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200);self.send_header('Content-Length',str(len(MARKER)));self.end_headers();self.wfile.write(MARKER)
    def log_message(self,*args):pass

def port():
    with socket.socket() as s:s.bind(('127.0.0.1',0));return s.getsockname()[1]

def exact(sock,n):
    chunks=b''
    while len(chunks)<n:
        raw=sock.recv(n-len(chunks))
        if not raw:raise RuntimeError('Unexpected EOF')
        chunks+=raw
    return chunks

def proxied_request(socks_port,target_port):
    with socket.create_connection(('127.0.0.1',socks_port),timeout=4) as s:
        s.settimeout(4);s.sendall(b'\x05\x01\x00');assert exact(s,2)==b'\x05\x00'
        s.sendall(b'\x05\x01\x00\x01'+socket.inet_aton('127.0.0.1')+struct.pack('!H',target_port))
        header=exact(s,4)
        if header[1]!=0:raise RuntimeError('SOCKS destination rejected')
        if header[3]==1:exact(s,6)
        elif header[3]==4:exact(s,18)
        elif header[3]==3:exact(s,exact(s,1)[0]+2)
        else:raise RuntimeError('Unexpected SOCKS reply')
        s.sendall(b'GET / HTTP/1.1\r\nHost: dark.test\r\nConnection: close\r\n\r\n')
        data=b''
        while len(data)<128*1024:
            raw=s.recv(16384)
            if not raw:break
            data+=raw
        return MARKER in data

def run(binary:Path,report_path:Path):
    report={'version':VERSION,'real_xray_binary_executed':False,'live_proxy_connection_tested':False,
            'live_packet_firewall_tested':False,'checks':[],'passed':False,'error':None}
    if not binary.is_file() or not os.access(binary,os.X_OK):
        raise RuntimeError('Supply a verified real Xray binary using --binary')
    version=subprocess.run([str(binary),'version'],capture_output=True,text=True,check=True,timeout=10).stdout
    if not version.startswith('Xray ') or any(x in version.upper() for x in ['TEST-DOUBLE','FAKE','MOCK']):
        raise RuntimeError('A real Xray release is required; test doubles are not accepted')
    report.update(real_xray_binary_executed=True,binary_version=version.splitlines()[0],binary_sha256=hashlib.sha256(binary.read_bytes()).hexdigest())
    with tempfile.TemporaryDirectory(prefix='dark-real-e2e-') as temp:
        tmp=Path(temp);api_port,data_port,core_api=port(),port(),port()
        cfg=Config(public_origin=f'http://127.0.0.1:{api_port}',bind_port=api_port,public_address='127.0.0.1',
                   xray_binary=str(binary.resolve()),xray_assets=str(binary.parent.resolve()),xray_api_port=core_api,poll_seconds=1)
        db=Store(tmp/'dark.sqlite3');engine=CoreEngine(cfg,db,tmp/'runtime');manager=Manager(db,engine);auth=Auth(db,tmp/'secret.key')
        auth.bootstrap('qa-owner','Temporary-QA-password-06')
        target=http.server.ThreadingHTTPServer(('127.0.0.1',0),Target);tt=threading.Thread(target=target.serve_forever,daemon=True);tt.start()
        server=uvicorn.Server(uvicorn.Config(make_app(manager,auth),host='127.0.0.1',port=api_port,log_level='error',access_log=False,ws='none'))
        worker=threading.Thread(target=server.run,daemon=True);worker.start();processes=[];logs=[]
        try:
            for _ in range(100):
                if server.started:break
                time.sleep(.05)
            opener=build_opener(ProxyHandler({}),HTTPCookieProcessor(http.cookiejar.CookieJar()));csrf=''
            def api(path,body=None,method=None):
                headers={'Content-Type':'application/json','X-Dark-CSRF':csrf}
                req=Request(cfg.public_origin+path,data=json.dumps(body).encode() if body is not None else None,headers=headers,method=method)
                with opener.open(req,timeout=20) as response:return json.load(response)
            def api_result(path,body=None,method=None):
                headers={'Content-Type':'application/json','X-Dark-CSRF':csrf}
                req=Request(cfg.public_origin+path,data=json.dumps(body).encode() if body is not None else None,headers=headers,method=method)
                try:
                    with opener.open(req,timeout=20) as response:return response.status,json.load(response)
                except HTTPError as ex:
                    try:doc=json.load(ex)
                    except Exception:doc={'detail':ex.read().decode(errors='replace')[:500]}
                    return ex.code,doc
            login=api('/api/auth/login',{'username':'qa-owner','password':'Temporary-QA-password-06'});csrf=login['csrf']
            report['checks'].append('real local HTTP login and CSRF-authenticated writes')
            ib=api('/api/inbounds',{'remark':'DARK REAL E2E','protocol':'vless','listen':'127.0.0.1','port':data_port,
                'enable':True,'tag':'real-test','settings':{'decryption':'none'},'streamSettings':{'network':'tcp','security':'none'},'sniffing':{}})
            for owner in ['alpha','beta']:
                api('/api/resellers/'+owner,{
                    'name':owner,'password':'Temporary-Rep-Password-082','enabled':True,
                    'allowed':[ib['id']],'volume_credit_bytes':0,'unlimited_credit':1,
                    'max_clients':10,'prefix':'','max_client_ips':0,'max_client_hwid':0
                },'PUT')
                api('/api/clients',{'owner':owner,'client':{'email':owner+'-client','limitIp':0,'totalGB':0},'inboundIds':[ib['id']]})
            api('/api/core/start',{})
            report['checks'].append('real Xray -test and owned process start')
            socks={}
            for owner in ['alpha','beta']:
                item=api('/api/clients/'+owner+'-client/links')
                with opener.open(item['subscription_url']+'?format=raw',timeout=10) as response:
                    link=response.read().decode().splitlines()[0]
                    assert response.headers.get('subscription-userinfo')
                uri=urlsplit(link);q=parse_qs(uri.query);sp=port();socks[owner]=sp
                stream={'network':q.get('type',['tcp'])[0],'security':'none'}
                if stream['network']=='grpc':stream['grpcSettings']={'serviceName':q.get('serviceName',[''])[0]}
                conf={'log':{'loglevel':'warning'},'inbounds':[{'listen':'127.0.0.1','port':sp,'protocol':'socks','settings':{'auth':'noauth'}}],
                      'outbounds':[{'protocol':'vless','settings':{'vnext':[{'address':uri.hostname,'port':uri.port,'users':[{'id':uri.username,'encryption':'none'}]}]},'streamSettings':stream}]}
                path=tmp/(owner+'-client.json');path.write_text(json.dumps(conf));log=(tmp/(owner+'.log')).open('wb');logs.append(log)
                process=subprocess.Popen([str(binary),'run','-config',str(path)],stdout=log,stderr=log);processes.append(process)
                for _ in range(50):
                    if process.poll() is not None:raise RuntimeError('Real Xray client failed to start')
                    try:
                        with socket.create_connection(('127.0.0.1',sp),timeout=.1):break
                    except OSError:time.sleep(.1)
                assert proxied_request(sp,target.server_port),owner+' proxy failed'
            report.update(live_proxy_connection_tested=True)
            report['checks'].append('two real VLESS clients on one inbound using generated DARK subscription credentials')
            api('/api/sync',{})
            alpha_stats=next(x for x in api('/api/owners') if x['id']=='alpha')
            used=alpha_stats['used_bytes']
            assert used>0,'No user traffic was metered from real Xray'
            assert alpha_stats['unlimited_credit_remaining']==0,'Existing unlimited service did not reserve its credit'
            assert proxied_request(socks['alpha'],target.server_port),'Observed traffic incorrectly disabled an exhausted-credit service'
            report['checks'].append('real traffic is metered without spending or disabling reserved resource credit')

            code,doc=api_result('/api/clients',{
                'owner':'alpha','client':{'email':'alpha-extra','limitIp':0,'totalGB':0},'inboundIds':[ib['id']]},'POST')
            assert code==400 and 'unlimited credit' in str(doc).lower(),f'Expected exhausted Unlimited Credit rejection, got {code}: {doc}'
            assert proxied_request(socks['beta'],target.server_port),'Other representative was interrupted by alpha credit exhaustion'
            report['checks'].append('exhausted Unlimited Credit blocks only new allocation, not existing shared-inbound traffic')

            topup=api('/api/resellers/alpha/credits',{
                'volume_bytes':0,'unlimited_units':1,'event_id':'real-core-alpha-credit-0001'},'POST')
            assert topup['recorded'] is True
            api('/api/clients',{'owner':'alpha','client':{'email':'alpha-extra','limitIp':0,'totalGB':0},'inboundIds':[ib['id']]})
            alpha_stats=next(x for x in api('/api/owners') if x['id']=='alpha')
            assert alpha_stats['allocated_unlimited']==2 and alpha_stats['unlimited_credit_remaining']==0
            report['checks'].append('Unlimited Credit top-up permits a new service allocation')

            api('/api/clients/alpha-client/action',{'action':'disable'})
            api('/api/resellers/alpha/credits',{
                'volume_bytes':0,'unlimited_units':1,'event_id':'real-core-alpha-credit-0002'},'POST')
            assert 'client_manual' in api('/api/clients/alpha-client')['block_reasons']
            report['checks'].append('resource-credit top-up preserves manual client disable')
            before=next(x for x in api('/api/owners') if x['id']=='alpha')['used_bytes']
            api('/api/clients/alpha-client/action',{'action':'reset'})
            api('/api/clients/alpha-client/action',{'action':'delete'})
            after=next(x for x in api('/api/owners') if x['id']=='alpha')['used_bytes']
            assert after>=before
            report['checks'].append('reset and delete never refund historical traffic')
            api('/api/core/stop',{});assert not api('/api/core/state')['running']
            report['checks'].append('owned Xray process stops cleanly')
            report['passed']=True
        except Exception as ex:
            report['error']=type(ex).__name__+': '+str(ex)[:1500]
            report['core_error_log']=(engine.runtime/'process.log').read_text(errors='replace')[-3000:] if (engine.runtime/'process.log').exists() else ''
        finally:
            for process in processes:
                if process.poll() is None:
                    process.terminate()
                    try:process.wait(timeout=5)
                    except subprocess.TimeoutExpired:process.kill();process.wait()
            for log in logs:log.close()
            server.should_exit=True;worker.join(timeout=15);manager.close();engine.close();db.close();target.shutdown();target.server_close()
    report_path.parent.mkdir(parents=True,exist_ok=True);report_path.write_text(json.dumps(report,indent=2))
    return report

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--binary',type=Path,required=True);p.add_argument('--report',type=Path,default=Path('DARK-real-e2e.json'));a=p.parse_args()
    try:
        result=run(a.binary,a.report);print(json.dumps(result,indent=2));raise SystemExit(0 if result['passed'] else 1)
    except Exception as ex:
        result={'version':VERSION,'passed':False,'real_xray_binary_executed':False,'live_proxy_connection_tested':False,'error':str(ex)}
        a.report.parent.mkdir(parents=True,exist_ok=True);a.report.write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2));raise SystemExit(1)
