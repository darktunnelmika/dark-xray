#!/usr/bin/env python3
"""Root-owned DARK XRAY update broker.

The unprivileged web panel can request only three fixed operations over a Unix
socket authenticated with Linux peer credentials: status, check and start.
Candidate refs are resolved to immutable commit SHAs before an update can start.
No arbitrary command, path or environment value crosses this boundary.
"""
from __future__ import annotations
import json,os,pwd,re,shutil,socket,socketserver,sqlite3,struct,subprocess,tempfile,threading,time,uuid
from pathlib import Path
from urllib.request import Request,urlopen
from urllib.parse import urlencode

APP=Path('/opt/dark-xray');DATA=Path('/var/lib/dark-xray');DB=DATA/'dark.sqlite3'
SOCKET=Path('/run/dark-xray-update/control.sock')
STATE=DATA/'update-state.json';LOG=DATA/'update.log'
REPO='https://github.com/darktunnelmika/dark-xray.git'
API='https://api.github.com/repos/darktunnelmika/dark-xray'
MAX_MESSAGE=65536
REF_RE=re.compile(r'[A-Za-z0-9][A-Za-z0-9._/-]{0,127}')
SHA_RE=re.compile(r'[0-9a-f]{40}')
VERSION_RE=re.compile(r'\d+\.\d+\.\d+(?:-[A-Za-z0-9.-]+)?')

class UpdateError(RuntimeError):pass

def atomic_json(path:Path,value:dict):
    path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_name('.'+path.name+'.tmp-'+uuid.uuid4().hex)
    tmp.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    os.chmod(tmp,0o640)
    try:
        group=pwd.getpwnam('darkxray').pw_gid;os.chown(tmp,0,group)
    except KeyError:pass
    os.replace(tmp,path)

def read_json(path:Path,default:dict)->dict:
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size>1024*1024:return dict(default)
        value=json.loads(path.read_text(encoding='utf-8'))
        return value if isinstance(value,dict) else dict(default)
    except (OSError,ValueError):return dict(default)

def current_version()->str:
    try:return (APP/'VERSION').read_text(encoding='utf-8').strip()
    except OSError:return 'unknown'

def current_source()->dict:
    value=read_json(DATA/'installed-source.json',{})
    return {'version':current_version(),'commit':str(value.get('commit') or ''),'ref':str(value.get('ref') or '')}

def run(args,*,timeout=20,cwd=None)->subprocess.CompletedProcess:
    try:return subprocess.run([str(x) for x in args],cwd=cwd,text=True,capture_output=True,check=False,timeout=timeout,
                              env={k:v for k,v in os.environ.items() if k not in {'PYTHONHOME','PYTHONPATH'}})
    except subprocess.TimeoutExpired as ex:raise UpdateError('Command timed out') from ex

def valid_ref(value:str)->str:
    value=(value or '').strip()
    if not REF_RE.fullmatch(value) or '..' in value or '@{' in value or '\\' in value or value.startswith('-'):
        raise UpdateError('Invalid Git ref/tag/commit')
    return value

def ver_key(tag:str):
    m=re.fullmatch(r'v?(\d+)\.(\d+)\.(\d+)(?:-rc(\d+))?',str(tag or ''))
    if not m:return None
    major,minor,patch,rc=m.groups()
    # Stable of the same X.Y.Z sorts after every RC.
    return (int(major),int(minor),int(patch),1 if rc is None else 0,int(rc or 0))

def older_than(candidate:str,current:str)->bool:
    a=ver_key(candidate);b=ver_key(current)
    return bool(a and b and a<b)

def resolve_channel(channel:str,exact:str)->str:
    if channel=='main':return 'main'
    if channel=='exact':return valid_ref(exact)
    if channel not in {'stable','rc'}:raise UpdateError('Unknown update channel')
    cp=run(['git','ls-remote','--tags','--refs',REPO],timeout=20)
    if cp.returncode:raise UpdateError('Cannot read DARK release tags: '+(cp.stderr or 'git error')[-250:])
    tags=[]
    for line in cp.stdout.splitlines():
        parts=line.split()
        if len(parts)!=2 or not parts[1].startswith('refs/tags/'):continue
        tag=parts[1][10:]
        if channel=='stable' and re.fullmatch(r'v\d+\.\d+\.\d+',tag):tags.append(tag)
        if channel=='rc' and re.fullmatch(r'v\d+\.\d+\.\d+-rc\d+',tag):tags.append(tag)
    if not tags:raise UpdateError('No '+channel+' DARK release tag is available yet')
    return max(tags,key=lambda x:ver_key(x) or (0,0,0,0,0))

def inspect_ref(ref:str)->dict:
    ref=valid_ref(ref)
    with tempfile.TemporaryDirectory(prefix='dark-update-check.') as td:
        root=Path(td)
        cp=run(['git','init','-q',root],timeout=10)
        if cp.returncode:raise UpdateError('Cannot initialize update inspection')
        cp=run(['git','-C',root,'remote','add','origin',REPO],timeout=10)
        if cp.returncode:raise UpdateError('Cannot configure update source')
        cp=run(['git','-C',root,'fetch','-q','--depth','1','origin',ref],timeout=35)
        if cp.returncode:raise UpdateError('Update ref cannot be fetched: '+(cp.stderr or 'git error')[-300:])
        cp=run(['git','-C',root,'rev-parse','FETCH_HEAD'],timeout=5)
        commit=cp.stdout.strip().lower()
        if cp.returncode or not SHA_RE.fullmatch(commit):raise UpdateError('Cannot resolve candidate commit')
        def show(path:str,limit:int)->str:
            x=run(['git','-C',root,'show','FETCH_HEAD:'+path],timeout=8)
            if x.returncode:raise UpdateError('Candidate is missing '+path)
            return x.stdout[:limit]
        version=show('VERSION',200).strip()
        if not VERSION_RE.fullmatch(version):raise UpdateError('Candidate VERSION is malformed')
        changelog=show('CHANGELOG.fa.md',40000)
        header='## '+version
        pos=changelog.find(header)
        if pos<0:
            header='## '+version.replace('-rc',' RC')
            pos=changelog.find(header)
        if pos>=0:
            end=changelog.find('\n## ',pos+4)
            notes=changelog[pos:end if end>=0 else len(changelog)].strip()[:7000]
        else:notes='No version-specific changelog section was found.'
        return {'ref':ref,'commit':commit,'version':version,'notes':notes}

def ci_status(commit:str)->dict:
    try:
        query=urlencode({'head_sha':commit,'per_page':20})
        req=Request(API+'/actions/runs?'+query,headers={'Accept':'application/vnd.github+json','User-Agent':'dark-xray-update-broker'})
        with urlopen(req,timeout=10) as response:doc=json.loads(response.read(1024*1024))
        runs=[r for r in doc.get('workflow_runs',[]) if r.get('name')=='Source checks and isolated tests']
        if not runs:return {'state':'unknown','verified':False,'detail':'No matching CI run found for this commit'}
        runs.sort(key=lambda r:str(r.get('created_at','')),reverse=True);r=runs[0]
        status=str(r.get('status') or 'unknown');conclusion=r.get('conclusion')
        if status=='completed' and conclusion=='success':return {'state':'success','verified':True,'run_id':r.get('id')}
        if status=='completed':return {'state':str(conclusion or 'failed'),'verified':False,'run_id':r.get('id')}
        return {'state':'pending','verified':False,'run_id':r.get('id')}
    except Exception as ex:return {'state':'unknown','verified':False,'detail':'GitHub CI lookup unavailable: '+type(ex).__name__}

def local_preflight()->dict:
    checks={}
    try:
        with sqlite3.connect(DB.resolve().as_uri()+'?mode=ro',uri=True,timeout=8) as db:
            checks['database']=db.execute('PRAGMA quick_check').fetchone()[0]=='ok'
    except Exception:checks['database']=False
    cp=run(['systemctl','is-active','--quiet','dark-xray.service'],timeout=5)
    checks['panel_service']=cp.returncode==0
    try:checks['free_bytes']=shutil.disk_usage(DATA).free
    except OSError:checks['free_bytes']=0
    checks['disk']=checks['free_bytes']>=160*1024*1024
    checks['git']=shutil.which('git') is not None
    checks['ready']=all(bool(checks[k]) for k in ('database','panel_service','disk','git'))
    return checks

def log_tail()->list[str]:
    try:
        if LOG.is_symlink() or not LOG.is_file():return []
        data=LOG.read_text(encoding='utf-8',errors='replace')[-30000:]
        return [line[-500:] for line in data.splitlines()[-40:]]
    except OSError:return []

class UpdateController:
    def __init__(self,allowed_uid:int):
        self.allowed_uid=allowed_uid;self.lock=threading.RLock();self.worker=None;self.candidate=None
        existing=read_json(STATE,{})
        if existing.get('state') in {'checking','queued','running','restarting','rolling_back'}:
            existing.update(state='failed',phase='failed',percent=100,
                            message='Previous update job was interrupted when the root update broker restarted',
                            finished_at=time.time(),broker_interrupted=True)
            atomic_json(STATE,existing)
        elif not STATE.exists():
            atomic_json(STATE,{'state':'idle','phase':'idle','percent':0,'message':'No update job has run yet','current':current_source()})
    def state(self):
        out=read_json(STATE,{'state':'idle','phase':'idle','percent':0,'message':'No update state'})
        out['current']=current_source();out['broker_ready']=True;out['log_tail']=log_tail();return out
    def check(self,channel:str,ref:str):
        with self.lock:
            st=self.state()
            if st.get('state') in {'queued','running','restarting','rolling_back'}:raise UpdateError('An update is already running')
            atomic_json(STATE,{'state':'checking','phase':'resolve','percent':5,'message':'Resolving immutable DARK candidate','current':current_source(),'checked_at':time.time()})
            resolved=resolve_channel(channel,ref)
            candidate=inspect_ref(resolved);candidate['channel']=channel
            ci=ci_status(candidate['commit']);preflight=local_preflight()
            current=current_source()
            candidate['ci']=ci
            downgrade=older_than(candidate.get('version',''),current.get('version',''))
            candidate['downgrade_blocked']=downgrade
            candidate['ready']=bool(ci.get('verified')) and bool(preflight.get('ready')) and not downgrade
            candidate['update_available']=candidate['commit']!=current.get('commit') if current.get('commit') else candidate['version']!=current.get('version')
            warnings=[]
            if downgrade:warnings.append('Candidate version is older than the installed DARK version; web downgrade is refused. Use rollback snapshots for recovery.')
            if not ci.get('verified'):warnings.append('Candidate CI is not green; web update is locked.')
            if not preflight.get('ready'):warnings.append('Local preflight is not ready.')
            self.candidate=candidate
            state={'state':'ready' if candidate['ready'] else 'blocked','phase':'preflight','percent':15,
                   'message':'Candidate verified and ready' if candidate['ready'] else 'Candidate is not safe to install from Web',
                   'current':current,'candidate':candidate,'preflight':preflight,'warnings':warnings,'checked_at':time.time()}
            atomic_json(STATE,state);return self.state()
    def start(self,commit:str):
        commit=(commit or '').strip().lower()
        with self.lock:
            if not SHA_RE.fullmatch(commit):raise UpdateError('Start requires an immutable 40-character commit SHA')
            if self.worker and self.worker.is_alive():raise UpdateError('An update is already running')
            if not self.candidate or self.candidate.get('commit')!=commit or not self.candidate.get('ready'):
                raise UpdateError('Run Check first and install only the verified candidate shown by DARK')
            job=uuid.uuid4().hex
            state={'state':'queued','phase':'queued','percent':18,'message':'Verified update queued','job_id':job,
                   'current':current_source(),'candidate':self.candidate,'started_at':time.time()}
            atomic_json(STATE,state)
            self.worker=threading.Thread(target=self._run,args=(commit,job),daemon=True);self.worker.start()
            return self.state()
    def _run(self,commit:str,job:str):
        LOG.parent.mkdir(parents=True,exist_ok=True)
        with LOG.open('a',encoding='utf-8') as log:
            log.write('\n===== DARK UPDATE '+time.strftime('%Y-%m-%d %H:%M:%S')+' '+job+' '+commit+' =====\n');log.flush()
            cmd=[APP/'.venv/bin/python',APP/'tools/update.py','--ref',commit,'--non-interactive','--status-file',STATE,'--job-id',job]
            env={k:v for k,v in os.environ.items() if k not in {'PYTHONHOME','PYTHONPATH'}}
            cp=subprocess.run([str(x) for x in cmd],stdout=log,stderr=subprocess.STDOUT,text=True,check=False,env=env)
            log.write('update.py exit='+str(cp.returncode)+'\n');log.flush()
        with self.lock:
            state=read_json(STATE,{})
            if cp.returncode==0 and state.get('state')!='success':
                state.update(state='success',phase='complete',percent=100,message='DARK XRAY updated successfully',finished_at=time.time())
                atomic_json(STATE,state)
            elif cp.returncode!=0 and state.get('state') not in {'failed','rolled_back'}:
                state.update(state='failed',phase='failed',message='Update failed; inspect the rollback result and log',finished_at=time.time())
                atomic_json(STATE,state)

class BrokerServer(socketserver.UnixStreamServer):
    allow_reuse_address=False
    def __init__(self,path,controller):self.controller=controller;super().__init__(path,BrokerHandler)

class BrokerHandler(socketserver.StreamRequestHandler):
    def handle(self):
        self.connection.settimeout(15)
        try:
            raw=self.connection.getsockopt(socket.SOL_SOCKET,socket.SO_PEERCRED,struct.calcsize('3i'));_,uid,_=struct.unpack('3i',raw)
            data=self.rfile.readline(MAX_MESSAGE+1)
            if len(data)>MAX_MESSAGE or not data.endswith(b'\n'):raise UpdateError('Incomplete or oversized request')
            msg=json.loads(data)
            if not isinstance(msg,dict):raise UpdateError('Request must be an object')
            op=msg.get('operation')
            expected={'status':{'operation'},'check':{'operation','channel','ref'},'start':{'operation','commit'}}
            if op not in expected or set(msg)!=expected[op]:raise UpdateError('Unknown update operation or fields')
            if uid not in {0,self.server.controller.allowed_uid}:raise UpdateError('Unix peer UID is not authorized')
            if uid==0 and op!='status':raise UpdateError('Root peer is read-only; web update mutations require the DARK service UID')
            if op=='status':result=self.server.controller.state()
            elif op=='check':result=self.server.controller.check(str(msg['channel']),str(msg['ref']))
            else:result=self.server.controller.start(str(msg['commit']))
            result={'ok':True,**result}
        except (UpdateError,ValueError,OSError,json.JSONDecodeError,subprocess.SubprocessError) as ex:
            result={'ok':False,'error':str(ex)[:700]}
        try:self.wfile.write(json.dumps(result,ensure_ascii=False).encode()+b'\n')
        except OSError:pass

def main():
    if os.geteuid()!=0:raise SystemExit('DARK update broker must run as root')
    try:account=pwd.getpwnam('darkxray')
    except KeyError:raise SystemExit('darkxray service account is missing')
    parent=SOCKET.parent
    if not parent.is_dir() or parent.is_symlink():raise SystemExit('Update RuntimeDirectory is missing or unsafe')
    st=parent.stat()
    if st.st_uid!=0 or st.st_mode&0o022:raise SystemExit('Update socket directory must be root-owned and not group/world writable')
    SOCKET.unlink(missing_ok=True)
    controller=UpdateController(account.pw_uid)
    with BrokerServer(str(SOCKET),controller) as server:
        os.chmod(SOCKET,0o660);os.chown(SOCKET,0,account.pw_gid)
        try:server.serve_forever(poll_interval=.5)
        finally:SOCKET.unlink(missing_ok=True)

if __name__=='__main__':main()
