#!/usr/bin/env python3
"""Exact-SHA Node updater with source, environment and SQLite rollback."""
from __future__ import annotations
import argparse,fcntl,http.client,json,os,re,shutil,socket,sqlite3,ssl,stat,subprocess,sys,tarfile,tempfile,time,uuid
from pathlib import Path
from urllib.parse import urlsplit

APP=Path('/opt/dark-xray-node');CONF=Path('/etc/dark-xray-node');DATA=Path('/var/lib/dark-xray-node')
REPO='https://github.com/darktunnelmika/dark-xray.git'
SHA_RE=re.compile(r'[0-9a-f]{40}')
BACKEND=('node_agent.py','node_runtime.py','node_recovery_protocol.py','core.py','dark_policy.py','guard_bridge.py','guardd.py','reality_scan.py','node_updated.py','update_bridge.py')
TOOLS=('fetch-core.py','import-core.py','update_node.py')
DEPLOY=('dark-xray-node.service','dark-xray-node-guard.service','dark-xray-node-update.service')
ROOT_FILES=('requirements-node.txt','VERSION','LICENSE','THIRD-PARTY-NOTICES.md')
SOURCE_FILES=tuple('backend/'+x for x in BACKEND)+tuple('tools/'+x for x in TOOLS)+tuple('deploy/'+x for x in DEPLOY)+ROOT_FILES


def run(args,*,timeout=90,cwd=None,check=True,stdout=None):
    options={'cwd':cwd,'text':True,'check':False,'timeout':timeout,
             'env':{k:v for k,v in os.environ.items() if k not in {'PYTHONHOME','PYTHONPATH'}}}
    if stdout is None:options['capture_output']=True
    else:options.update(stdout=stdout,stderr=subprocess.STDOUT)
    cp=subprocess.run([str(x) for x in args],**options)
    if check and cp.returncode:raise RuntimeError((cp.stderr or cp.stdout or 'command failed')[-800:])
    return cp


def version_key(value:str):
    m=re.fullmatch(r'v?(\d+)\.(\d+)\.(\d+)(?:-rc(\d+))?',str(value or ''))
    if not m:return None
    a,b,c,rc=m.groups();return (int(a),int(b),int(c),1 if rc is None else 0,int(rc or 0))


def validate_source(src:Path):
    for name in (*SOURCE_FILES,'install-node.sh','tools/provision_node.py'):
        path=src/name
        if not path.is_file() or path.is_symlink():raise RuntimeError('Candidate is missing Node Agent file: '+name)
    run([sys.executable,'-m','py_compile',*(src/'backend'/x for x in BACKEND),src/'tools/update_node.py',src/'tools/provision_node.py'])
    run(['bash','-n',src/'install-node.sh'])
    version=(src/'VERSION').read_text(encoding='utf-8').strip()
    if not re.fullmatch(r'\d+\.\d+\.\d+(?:-[A-Za-z0-9.-]+)?',version):raise RuntimeError('Candidate VERSION is malformed')
    return version


def source_commit(src:Path)->str:
    cp=run(['git','-C',src,'rev-parse','HEAD'],timeout=10);value=cp.stdout.strip().lower()
    if not SHA_RE.fullmatch(value):raise RuntimeError('Candidate source is not an immutable commit')
    return value


def current_source()->dict:
    try:v=json.loads((DATA/'installed-source.json').read_text(encoding='utf-8'))
    except Exception:v={}
    try:version=(APP/'VERSION').read_text(encoding='utf-8').strip()
    except OSError:version='unknown'
    return {'commit':str(v.get('commit') or ''),'version':version,'ref':str(v.get('ref') or '')}


def build_candidate_venv(src:Path,temp:Path)->Path:
    venv=temp/'venv'
    run([sys.executable,'-m','venv',venv],timeout=60)
    py=venv/'bin/python'
    run([py,'-m','pip','install','-q','--disable-pip-version-check','-r',src/'requirements-node.txt'],timeout=180)
    run([py,'-m','pip','check'],timeout=30)
    env={k:v for k,v in os.environ.items() if k not in {'PYTHONHOME','PYTHONPATH'}}
    env['PYTHONPATH']=str(src/'backend')
    cp=subprocess.run([str(py),'-c','import node_agent,node_runtime,guardd'],cwd=src/'backend',env=env,capture_output=True,text=True,timeout=30)
    if cp.returncode:raise RuntimeError('Candidate Node Agent import failed: '+(cp.stderr or '')[-500:])
    return venv


def regular(path:Path):
    info=path.lstat()
    if not stat.S_ISREG(info.st_mode):raise RuntimeError('Unsafe non-regular update file: '+str(path))
    return info


def atomic_bytes(path:Path,data:bytes,mode:int,uid:int,gid:int):
    if path.is_symlink():raise RuntimeError('Update destination symlink refused')
    fd,name=tempfile.mkstemp(prefix='.'+path.name+'.',dir=path.parent)
    temp=Path(name)
    try:
        with os.fdopen(fd,'wb') as out:
            os.fchmod(out.fileno(),mode);os.fchown(out.fileno(),uid,gid)
            out.write(data);out.flush();os.fsync(out.fileno())
        os.replace(temp,path)
        dfd=os.open(path.parent,os.O_RDONLY)
        try:os.fsync(dfd)
        finally:os.close(dfd)
    finally:temp.unlink(missing_ok=True)


def source_snapshot(path:Path):
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'wb') as output:
        with tarfile.open(fileobj=output,mode='w:gz') as archive:
            for name in SOURCE_FILES:
                source=APP/name
                if source.exists():
                    regular(source);archive.add(source,arcname=name,recursive=False)
        output.flush();os.fsync(output.fileno())


def restore_source(path:Path):
    with tarfile.open(path,'r:gz') as archive:
        members=archive.getmembers();seen=set()
        for member in members:
            if member.name not in SOURCE_FILES or member.name in seen or not member.isfile() or member.size>4*1024*1024:
                raise RuntimeError('Unsafe Node rollback archive member')
            seen.add(member.name)
        for member in members:
            target=APP/member.name
            if target.parent.is_symlink() or target.is_symlink():raise RuntimeError('Unsafe Node rollback destination')
            target.parent.mkdir(parents=True,exist_ok=True,mode=0o755)
            stream=archive.extractfile(member)
            if stream is None:raise RuntimeError('Unreadable Node rollback archive')
            atomic_bytes(target,stream.read(),0o644,os.geteuid(),os.getegid())


def copy_source(src:Path):
    for sub,names in [('backend',BACKEND),('tools',TOOLS),('deploy',DEPLOY)]:
        base=APP/sub
        if base.is_symlink():raise RuntimeError('Unsafe Node source directory')
        base.mkdir(parents=True,exist_ok=True,mode=0o755)
        cache=base/'__pycache__'
        if cache.is_dir() and not cache.is_symlink():shutil.rmtree(cache)
        for name in names:
            source=src/sub/name;regular(source)
            if (base/name).is_symlink():raise RuntimeError('Unsafe Node source destination')
            shutil.copy2(source,base/name);os.chmod(base/name,0o644)
    for name in ROOT_FILES:
        regular(src/name)
        if (APP/name).is_symlink():raise RuntimeError('Unsafe Node source destination')
        shutil.copy2(src/name,APP/name);os.chmod(APP/name,0o644)
    # Never recursively chmod APP: that corrupts the current/rollback venv.


def snapshot_database(path:Path):
    source=DATA/'node.sqlite3';info=regular(source)
    db=sqlite3.connect(source.resolve().as_uri()+'?mode=ro',uri=True,timeout=15)
    snapshot=sqlite3.connect(path)
    try:
        db.backup(snapshot)
        if snapshot.execute('PRAGMA quick_check').fetchone()[0]!='ok':raise RuntimeError('Node rollback database is corrupt')
    finally:snapshot.close();db.close()
    os.chmod(path,0o600)
    return info


def restore_database(path:Path,info):
    regular(path)
    for suffix in ('-wal','-shm'):
        side=DATA/('node.sqlite3'+suffix)
        if side.exists() or side.is_symlink():side.unlink()
    atomic_bytes(DATA/'node.sqlite3',path.read_bytes(),0o600,info.st_uid,info.st_gid)


def install_units():
    for name in DEPLOY:
        src=APP/'deploy'/name;dst=Path('/etc/systemd/system')/name
        regular(src)
        if dst.is_symlink():raise RuntimeError('Unsafe Node unit destination')
        shutil.copy2(src,dst);os.chmod(dst,0o644)
    run(['systemctl','daemon-reload'],timeout=20)


def health_probe(timeout:float=20.0,expected_version:str|None=None):
    cfg=json.loads((CONF/'config.json').read_text(encoding='utf-8'));origin=urlsplit(cfg['public_origin'])
    token=(DATA/'token').read_text().strip();deadline=time.monotonic()+timeout;last=''
    class LocalHTTPS(http.client.HTTPSConnection):
        def connect(self):
            raw=socket.create_connection(('127.0.0.1',self.port),timeout=self.timeout)
            try:self.sock=self._context.wrap_socket(raw,server_hostname=self.host)
            except BaseException:raw.close();raise
    while time.monotonic()<deadline:
        connection=LocalHTTPS(origin.hostname,origin.port or 443,timeout=3,context=ssl.create_default_context())
        try:
            connection.request('GET','/node/api/health',headers={'Authorization':'Bearer '+token,'Connection':'close'})
            response=connection.getresponse();body=response.read(1024*1024+1)
            if response.status!=200 or len(body)>1024*1024:raise RuntimeError('Node health HTTP failed')
            doc=json.loads(body)
            if doc.get('service')!='DARK XRAY NODE' or doc.get('agent_only') is not True:raise RuntimeError('Unexpected Node health payload')
            if expected_version and doc.get('version')!=expected_version:raise RuntimeError('Node is not running the candidate version')
            return doc
        except Exception as ex:last=type(ex).__name__+': '+str(ex)[:200];time.sleep(.4)
        finally:connection.close()
    raise RuntimeError('Node Agent health failed after update: '+last)


def atomic_source(commit:str,version:str,ref:str):
    path=DATA/'installed-source.json';info=regular(path)
    raw=json.dumps({'commit':commit,'version':version,'ref':ref,'installed_at':time.time(),'role':'node-agent'},indent=2)+'\n'
    # Preserve root:darkxray ownership so the unprivileged Agent can report SHA.
    atomic_bytes(path,raw.encode(),0o640,info.st_uid,info.st_gid)


def activate_candidate(src:Path,new_venv:Path,commit:str,version:str,ref:str,transaction:Path):
    archive=transaction/'source.tar.gz';database=transaction/'node.sqlite3'
    old_venv=APP/'.venv';saved_venv=transaction/'venv'
    meta=DATA/'installed-source.json';meta_info=regular(meta);meta_bytes=meta.read_bytes()
    previous=current_source();source_snapshot(archive)
    moved_venv=False;database_info=None;mutation_started=False
    run(['systemctl','stop','dark-xray-node.service'],timeout=60)
    try:
        database_info=snapshot_database(database)
        mutation_started=True;copy_source(src)
        os.rename(old_venv,saved_venv);moved_venv=True
        shutil.copytree(new_venv,old_venv,symlinks=True)
        install_units()
        run(['systemctl','restart','dark-xray-node-guard.service'],timeout=30)
        run(['systemctl','start','dark-xray-node.service'],timeout=30)
        health_probe(expected_version=version)
        atomic_source(commit,version,ref)
    except Exception as cause:
        errors=[]
        def attempt(label,operation):
            try:operation()
            except Exception as exc:errors.append(label+':'+type(exc).__name__)
        attempt('stop',lambda:run(['systemctl','stop','dark-xray-node.service'],timeout=60))
        if mutation_started:attempt('source',lambda:restore_source(archive))
        if moved_venv:
            def recover_venv():
                if old_venv.exists():shutil.rmtree(old_venv)
                os.rename(saved_venv,old_venv)
            attempt('venv',recover_venv)
        # A failure before the rename must NEVER delete the only usable venv.
        if database_info is not None:attempt('database',lambda:restore_database(database,database_info))
        attempt('source-identity',lambda:atomic_bytes(meta,meta_bytes,0o640,meta_info.st_uid,meta_info.st_gid))
        attempt('units',install_units)
        attempt('guard',lambda:run(['systemctl','restart','dark-xray-node-guard.service'],timeout=30))
        attempt('start',lambda:run(['systemctl','start','dark-xray-node.service'],timeout=30))
        attempt('health',lambda:health_probe(expected_version=previous['version']))
        if errors:raise RuntimeError('Node update failed; rollback incomplete: '+','.join(errors)) from cause
        raise RuntimeError('Node update failed; previous source, environment and database restored') from cause
    shutil.rmtree(saved_venv,ignore_errors=True)
    return {'updated':True,'commit':commit,'version':version,'rollback':str(transaction)}


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--ref',required=True);args=parser.parse_args()
    if os.geteuid()!=0:raise SystemExit('Root required')
    ref=str(args.ref).lower()
    if not SHA_RE.fullmatch(ref):raise SystemExit('Node update requires exact 40-character commit SHA')
    if not APP.is_dir() or APP.is_symlink() or not (APP/'.venv/bin/python').is_file():raise SystemExit('Node Agent installation is incomplete')
    if not CONF.is_dir() or CONF.is_symlink() or not DATA.is_dir() or DATA.is_symlink():raise SystemExit('Node Agent state is incomplete')
    if shutil.disk_usage(APP).free<160*1024*1024:raise SystemExit('Need at least 160 MiB free for Node rollback')
    rollback_root=APP/'.rollback'
    if rollback_root.is_symlink():raise SystemExit('Unsafe Node rollback directory')
    rollback_root.mkdir(mode=0o700,exist_ok=True)
    info=rollback_root.stat()
    if info.st_uid!=0 or stat.S_IMODE(info.st_mode)!=0o700:raise SystemExit('Node rollback directory must be root-owned and private')
    lock_fd=os.open(APP/'.node-update.lock',os.O_WRONLY|os.O_CREAT|os.O_NOFOLLOW,0o600)
    try:
        try:fcntl.flock(lock_fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise SystemExit('Another Node update is running')
        with tempfile.TemporaryDirectory(prefix='dark-node-update.') as td:
            temp=Path(td);src=temp/'src'
            run(['git','init','-q',src],timeout=10);run(['git','-C',src,'remote','add','origin',REPO],timeout=10)
            run(['git','-C',src,'fetch','-q','--depth','1','origin',ref],timeout=60)
            run(['git','-C',src,'checkout','-q','--detach','FETCH_HEAD'],timeout=15)
            commit=source_commit(src);version=validate_source(src)
            if commit!=ref:raise RuntimeError('Fetched Node candidate SHA mismatch')
            current=current_source()
            if version_key(version) and version_key(current['version']) and version_key(version)<version_key(current['version']):
                raise RuntimeError('Node downgrade refused')
            venv=build_candidate_venv(src,temp)
            transaction=rollback_root/(time.strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:12])
            transaction.mkdir(mode=0o700)
            print(json.dumps(activate_candidate(src,venv,commit,version,ref,transaction)))
    finally:os.close(lock_fd)


if __name__=='__main__':main()
