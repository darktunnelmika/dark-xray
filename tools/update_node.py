#!/usr/bin/env python3
"""Rollback-safe updater for the lightweight DARK XRAY Node Agent."""
from __future__ import annotations
import argparse,json,os,re,shutil,socket,ssl,subprocess,sys,tarfile,tempfile,time
from pathlib import Path
from urllib.parse import urlsplit

APP=Path('/opt/dark-xray-node');CONF=Path('/etc/dark-xray-node');DATA=Path('/var/lib/dark-xray-node')
REPO='https://github.com/darktunnelmika/dark-xray.git'
SHA_RE=re.compile(r'[0-9a-f]{40}')
BACKEND=('node_agent.py','node_runtime.py','core.py','dark_policy.py','guard_bridge.py','guardd.py','reality_scan.py','node_updated.py','update_bridge.py')
TOOLS=('fetch-core.py','import-core.py','update_node.py')
DEPLOY=('dark-xray-node.service','dark-xray-node-guard.service','dark-xray-node-update.service')
ROOT_FILES=('requirements-node.txt','VERSION','LICENSE','THIRD-PARTY-NOTICES.md')

def run(args,*,timeout=90,cwd=None,check=True,stdout=None):
    cp=subprocess.run([str(x) for x in args],cwd=cwd,text=True,capture_output=stdout is None,stdout=stdout,stderr=None if stdout is not None else subprocess.PIPE,
                      check=False,timeout=timeout,env={k:v for k,v in os.environ.items() if k not in {'PYTHONHOME','PYTHONPATH'}})
    if check and cp.returncode:raise RuntimeError((cp.stderr or cp.stdout or 'command failed')[-800:])
    return cp

def version_key(value:str):
    m=re.fullmatch(r'v?(\d+)\.(\d+)\.(\d+)(?:-rc(\d+))?',str(value or ''))
    if not m:return None
    a,b,c,rc=m.groups();return (int(a),int(b),int(c),1 if rc is None else 0,int(rc or 0))

def validate_source(src:Path):
    for path in [*(src/'backend'/x for x in BACKEND),*(src/'tools'/x for x in TOOLS),*(src/'deploy'/x for x in DEPLOY),*(src/x for x in ROOT_FILES),(src/'install-node.sh')]:
        if not path.is_file() or path.is_symlink():raise RuntimeError('Candidate is missing Node Agent file: '+str(path.relative_to(src)))
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
    # Import candidate agent without touching live DB.
    env={**os.environ,'PYTHONPATH':str(src/'backend')}
    cp=subprocess.run([str(py),'-c','import node_agent,node_runtime,guardd'],cwd=src/'backend',env=env,capture_output=True,text=True,timeout=30)
    if cp.returncode:raise RuntimeError('Candidate Node Agent import failed: '+(cp.stderr or '')[-500:])
    return venv

def source_snapshot(path:Path):
    with tarfile.open(path,'w:gz') as tf:
        for sub,names in [('backend',BACKEND),('tools',TOOLS),('deploy',DEPLOY)]:
            for name in names:
                p=APP/sub/name
                if p.is_file() and not p.is_symlink():tf.add(p,arcname=sub+'/'+name)
        for name in ROOT_FILES:
            p=APP/name
            if p.is_file() and not p.is_symlink():tf.add(p,arcname=name)
    os.chmod(path,0o600)

def restore_source(path:Path):
    with tarfile.open(path,'r:gz') as tf:
        for m in tf.getmembers():
            target=(APP/m.name).resolve()
            if APP.resolve()!=target and APP.resolve() not in target.parents:raise RuntimeError('Unsafe Node rollback archive')
        tf.extractall(APP,filter='data')

def copy_source(src:Path):
    for sub,names in [('backend',BACKEND),('tools',TOOLS),('deploy',DEPLOY)]:
        base=APP/sub;base.mkdir(parents=True,exist_ok=True,mode=0o755)
        keep=set(names)
        for p in base.iterdir():
            if p.is_file() and p.name not in keep:p.unlink()
        for name in names:shutil.copy2(src/sub/name,base/name)
    for name in ROOT_FILES:shutil.copy2(src/name,APP/name)
    for p in APP.rglob('*'):
        if p.is_symlink():continue
        os.chmod(p,0o755 if p.is_dir() else 0o644)

def install_units():
    for name in DEPLOY:
        src=APP/'deploy'/name;dst=Path('/etc/systemd/system')/name
        shutil.copy2(src,dst);os.chmod(dst,0o644)
    run(['systemctl','daemon-reload'],timeout=20)

def health_probe(timeout:float=15.0):
    cfg=json.loads((CONF/'config.json').read_text(encoding='utf-8'));origin=urlsplit(cfg['public_origin'])
    host=origin.hostname;port=origin.port or 443;token=(DATA/'token').read_text().strip()
    deadline=time.monotonic()+timeout;last=''
    while time.monotonic()<deadline:
        try:
            raw=socket.create_connection(('127.0.0.1',port),timeout=2)
            ctx=ssl.create_default_context()
            with ctx.wrap_socket(raw,server_hostname=host) as sock:
                req=(f'GET /node/api/health HTTP/1.1\r\nHost: {origin.netloc}\r\nAuthorization: Bearer {token}\r\nConnection: close\r\n\r\n').encode()
                sock.sendall(req);data=b''
                while len(data)<1024*1024:
                    x=sock.recv(65536)
                    if not x:break
                    data+=x
            head,body=data.split(b'\r\n\r\n',1)
            if b' 200 ' not in head.split(b'\r\n',1)[0]:raise RuntimeError('Node health HTTP failed')
            doc=json.loads(body.decode())
            if doc.get('service')!='DARK XRAY NODE' or doc.get('agent_only') is not True:raise RuntimeError('Unexpected Node health payload')
            return doc
        except Exception as ex:last=type(ex).__name__+': '+str(ex)[:200];time.sleep(.4)
    raise RuntimeError('Node Agent health failed after update: '+last)

def atomic_source(commit:str,version:str,ref:str):
    p=DATA/'installed-source.json';tmp=p.with_name('.installed-source.update')
    tmp.write_text(json.dumps({'commit':commit,'version':version,'ref':ref,'installed_at':time.time(),'role':'node-agent'},indent=2)+'\n')
    os.chmod(tmp,0o640);os.replace(tmp,p)

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--ref',required=True);a=p.parse_args()
    if os.geteuid()!=0:raise SystemExit('Root required')
    ref=str(a.ref).lower()
    if not SHA_RE.fullmatch(ref):raise SystemExit('Node update requires exact 40-character commit SHA')
    if not APP.is_dir() or APP.is_symlink() or not (APP/'.venv/bin/python').is_file():raise SystemExit('Node Agent installation is incomplete')
    if not CONF.is_dir() or not DATA.is_dir():raise SystemExit('Node Agent state is incomplete')
    if shutil.disk_usage(DATA).free<160*1024*1024:raise SystemExit('Need at least 160 MiB free for Node rollback')
    temp=Path(tempfile.mkdtemp(prefix='dark-node-update.'));src=temp/'src'
    rollback=DATA/'backups'/('pre-node-update-'+time.strftime('%Y%m%d-%H%M%S')+'.tar.gz');rollback.parent.mkdir(parents=True,exist_ok=True)
    old_venv=APP/'.venv';venv_backup=APP/'.venv-rollback'
    try:
        run(['git','init','-q',src],timeout=10);run(['git','-C',src,'remote','add','origin',REPO],timeout=10)
        run(['git','-C',src,'fetch','-q','--depth','1','origin',ref],timeout=60)
        run(['git','-C',src,'checkout','-q','--detach','FETCH_HEAD'],timeout=15)
        commit=source_commit(src);version=validate_source(src)
        if commit!=ref:raise RuntimeError('Fetched Node candidate SHA mismatch')
        cur=current_source()
        if version_key(version) and version_key(cur['version']) and version_key(version)<version_key(cur['version']):raise RuntimeError('Node downgrade refused')
        new_venv=build_candidate_venv(src,temp);source_snapshot(rollback)
        run(['systemctl','stop','dark-xray-node.service'],timeout=30)
        try:
            copy_source(src)
            if venv_backup.exists():shutil.rmtree(venv_backup)
            os.rename(old_venv,venv_backup);shutil.copytree(new_venv,old_venv,symlinks=True)
            install_units()
            run(['systemctl','restart','dark-xray-node-guard.service'],timeout=30)
            run(['systemctl','start','dark-xray-node.service'],timeout=30)
            run(['systemctl','is-active','--quiet','dark-xray-node.service'],timeout=10)
            health_probe();atomic_source(commit,version,ref)
            shutil.rmtree(venv_backup,ignore_errors=True)
            print(json.dumps({'updated':True,'commit':commit,'version':version,'rollback':str(rollback)}))
        except Exception:
            run(['systemctl','stop','dark-xray-node.service'],timeout=20,check=False)
            restore_source(rollback)
            if old_venv.exists():shutil.rmtree(old_venv)
            if venv_backup.exists():os.rename(venv_backup,old_venv)
            install_units()
            run(['systemctl','restart','dark-xray-node-guard.service'],timeout=30,check=False)
            run(['systemctl','start','dark-xray-node.service'],timeout=30)
            health_probe()
            raise
    finally:shutil.rmtree(temp,ignore_errors=True)

if __name__=='__main__':main()
