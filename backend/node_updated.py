#!/usr/bin/env python3
"""Root-owned update broker for the lightweight DARK XRAY Node Agent."""
from __future__ import annotations
import json,os,pwd,re,socket,socketserver,struct,subprocess,tempfile,threading,time,uuid
from pathlib import Path
from urllib.request import Request,urlopen
from urllib.parse import urlencode

APP=Path('/opt/dark-xray-node');DATA=Path('/var/lib/dark-xray-node')
SOCKET=Path('/run/dark-xray-node-update/control.sock')
STATE=DATA/'update-state.json';LOG=DATA/'update.log'
REPO='https://github.com/darktunnelmika/dark-xray.git'
API='https://api.github.com/repos/darktunnelmika/dark-xray'
SHA_RE=re.compile(r'[0-9a-f]{40}');MAX=65536

class UpdateError(RuntimeError):pass

def atomic(path:Path,value:dict):
    path.parent.mkdir(parents=True,exist_ok=True);tmp=path.with_name('.'+path.name+'.'+uuid.uuid4().hex)
    tmp.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n');os.chmod(tmp,0o640)
    try:os.chown(tmp,0,pwd.getpwnam('darkxray').pw_gid)
    except Exception:pass
    os.replace(tmp,path)

def read(path:Path,default:dict):
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size>1024*1024:return dict(default)
        v=json.loads(path.read_text());return v if isinstance(v,dict) else dict(default)
    except Exception:return dict(default)

def current():
    src=read(DATA/'installed-source.json',{})
    try:version=(APP/'VERSION').read_text().strip()
    except OSError:version='unknown'
    return {'commit':str(src.get('commit') or ''),'version':version,'ref':str(src.get('ref') or ''),'role':'node-agent'}

def run(args,timeout=30,cwd=None):
    try:return subprocess.run([str(x) for x in args],cwd=cwd,capture_output=True,text=True,timeout=timeout,check=False,
                              env={k:v for k,v in os.environ.items() if k not in {'PYTHONHOME','PYTHONPATH'}})
    except subprocess.TimeoutExpired as ex:raise UpdateError('Command timed out') from ex

def inspect(commit:str):
    if not SHA_RE.fullmatch(commit):raise UpdateError('Exact commit SHA required')
    with tempfile.TemporaryDirectory(prefix='dark-node-check.') as td:
        root=Path(td);run(['git','init','-q',root],10);run(['git','-C',root,'remote','add','origin',REPO],10)
        cp=run(['git','-C',root,'fetch','-q','--depth','1','origin',commit],60)
        if cp.returncode:raise UpdateError('Cannot fetch Node candidate')
        cp=run(['git','-C',root,'rev-parse','FETCH_HEAD'],10);sha=cp.stdout.strip().lower()
        if sha!=commit:raise UpdateError('Candidate SHA mismatch')
        required=['VERSION','requirements-node.txt','install-node.sh','backend/node_agent.py','backend/node_runtime.py',
                  'backend/smart_routing.py','backend/smart_warp_probe.py','backend/node_updated.py','backend/update_bridge.py','tools/update_node.py',
                  'deploy/dark-xray-node.service','deploy/dark-xray-node-guard.service','deploy/dark-xray-node-update.service']
        for name in required:
            cp=run(['git','-C',root,'cat-file','-e','FETCH_HEAD:'+name],8)
            if cp.returncode:raise UpdateError('Candidate missing Node Agent file: '+name)
        cp=run(['git','-C',root,'show','FETCH_HEAD:VERSION'],8);version=cp.stdout.strip()
        if not re.fullmatch(r'\d+\.\d+\.\d+(?:-[A-Za-z0-9.-]+)?',version):raise UpdateError('Candidate VERSION malformed')
        return {'commit':commit,'version':version}

def ci(commit:str):
    try:
        req=Request(API+'/actions/runs?'+urlencode({'head_sha':commit,'per_page':20}),
                    headers={'Accept':'application/vnd.github+json','User-Agent':'dark-xray-node-updater'})
        with urlopen(req,timeout=10) as res:doc=json.loads(res.read(1024*1024))
        runs=[x for x in doc.get('workflow_runs',[]) if x.get('name')=='Source checks and isolated tests']
        runs.sort(key=lambda x:str(x.get('created_at','')),reverse=True)
        if not runs:return {'verified':False,'state':'unknown','detail':'No matching CI run'}
        r=runs[0];ok=r.get('status')=='completed' and r.get('conclusion')=='success'
        return {'verified':ok,'state':'success' if ok else str(r.get('conclusion') or r.get('status') or 'unknown'),'run_id':r.get('id')}
    except Exception as ex:return {'verified':False,'state':'unknown','detail':type(ex).__name__}

class Controller:
    def __init__(self,uid:int):
        self.uid=uid;self.lock=threading.RLock();self.worker=None;self.candidate=None
        old=read(STATE,{})
        if old.get('state') in {'checking','queued','running'}:
            old.update(state='failed',message='Previous Node update was interrupted',finished_at=time.time());atomic(STATE,old)
        elif not STATE.exists():atomic(STATE,{'state':'idle','current':current()})
    def status(self):
        out=read(STATE,{'state':'idle'});out['current']=current();out['broker_ready']=True;return out
    def check(self,commit:str):
        commit=str(commit or '').lower()
        with self.lock:
            cand=inspect(commit);verified=ci(commit);cand['ci']=verified
            cand['update_available']=commit!=current().get('commit');cand['ready']=bool(verified.get('verified'))
            self.candidate=cand
            atomic(STATE,{'state':'ready' if cand['ready'] else 'blocked','current':current(),'candidate':cand,
                          'message':'Node candidate verified' if cand['ready'] else 'Node candidate CI is not verified','checked_at':time.time()})
            return self.status()
    def start(self,commit:str):
        commit=str(commit or '').lower()
        with self.lock:
            if self.worker and self.worker.is_alive():raise UpdateError('Node update already running')
            if not self.candidate or self.candidate.get('commit')!=commit or not self.candidate.get('ready'):
                raise UpdateError('Check exact Hub commit before starting Node update')
            job=uuid.uuid4().hex;atomic(STATE,{'state':'queued','current':current(),'candidate':self.candidate,'job_id':job,'started_at':time.time()})
            self.worker=threading.Thread(target=self._work,args=(commit,job),daemon=True);self.worker.start();return self.status()
    def _work(self,commit:str,job:str):
        atomic(STATE,{'state':'running','current':current(),'candidate':self.candidate,'job_id':job,'started_at':time.time()})
        LOG.parent.mkdir(parents=True,exist_ok=True)
        with LOG.open('a') as log:
            log.write('\n=== NODE UPDATE '+job+' '+commit+' ===\n');log.flush()
            cp=subprocess.run([str(APP/'.venv/bin/python'),str(APP/'tools/update_node.py'),'--ref',commit],
                              stdout=log,stderr=subprocess.STDOUT,text=True,check=False,
                              env={k:v for k,v in os.environ.items() if k not in {'PYTHONHOME','PYTHONPATH'}})
        if cp.returncode==0:atomic(STATE,{'state':'success','current':current(),'candidate':self.candidate,'job_id':job,'finished_at':time.time()})
        else:atomic(STATE,{'state':'failed','current':current(),'candidate':self.candidate,'job_id':job,'finished_at':time.time(),'message':'Node update failed; rollback attempted'})

class Server(socketserver.UnixStreamServer):
    allow_reuse_address=False
    def __init__(self,path,c):self.controller=c;super().__init__(path,Handler)
class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        try:
            raw=self.connection.getsockopt(socket.SOL_SOCKET,socket.SO_PEERCRED,struct.calcsize('3i'));_,uid,_=struct.unpack('3i',raw)
            data=self.rfile.readline(MAX+1)
            if len(data)>MAX or not data.endswith(b'\n'):raise UpdateError('Invalid request')
            msg=json.loads(data);op=msg.get('operation') if isinstance(msg,dict) else None
            expected={'status':{'operation'},'check':{'operation','channel','ref'},'start':{'operation','commit'}}
            if op not in expected or set(msg)!=expected[op]:raise UpdateError('Unknown update operation')
            if uid not in {0,self.server.controller.uid}:raise UpdateError('Unauthorized Unix peer')
            if uid==0 and op!='status':raise UpdateError('Root peer is read-only')
            if op=='status':out=self.server.controller.status()
            elif op=='check':
                if msg.get('channel')!='exact':raise UpdateError('Node updater accepts exact Hub SHA only')
                out=self.server.controller.check(str(msg.get('ref') or ''))
            else:out=self.server.controller.start(str(msg.get('commit') or ''))
            result={'ok':True,**out}
        except Exception as ex:result={'ok':False,'error':str(ex)[:700]}
        try:self.wfile.write(json.dumps(result).encode()+b'\n')
        except OSError:pass

def main():
    if os.geteuid()!=0:raise SystemExit('Node update broker must run as root')
    user=pwd.getpwnam('darkxray');parent=SOCKET.parent
    if not parent.is_dir() or parent.is_symlink() or parent.stat().st_uid!=0 or parent.stat().st_mode&0o022:
        raise SystemExit('Unsafe Node update runtime directory')
    SOCKET.unlink(missing_ok=True);controller=Controller(user.pw_uid)
    with Server(str(SOCKET),controller) as server:
        os.chmod(SOCKET,0o660);os.chown(SOCKET,0,user.pw_gid)
        try:server.serve_forever(.5)
        finally:SOCKET.unlink(missing_ok=True)
if __name__=='__main__':main()
