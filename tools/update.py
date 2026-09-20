#!/usr/bin/env python3
"""Rollback-safe DARK XRAY source updater.

The updater validates the candidate before downtime, checks the live installation,
creates independent source + SQLite rollback snapshots, then activates the new
source. A successful systemd restart is not enough: the local Doctor must also
confirm configuration, SQLite integrity, panel route and a real web asset.

If activation fails after the new code has touched the database, both source and
database are restored before the previous service is reactivated.
"""
from __future__ import annotations
import argparse,json,os,re,shutil,socket,sqlite3,ssl,subprocess,sys,tarfile,tempfile,time,uuid
from urllib.parse import urlsplit
from pathlib import Path

APP=Path('/opt/dark-xray'); CONF=Path('/etc/dark-xray'); DATA=Path('/var/lib/dark-xray')
DB=DATA/'dark.sqlite3'
REPO='https://github.com/darktunnelmika/dark-xray.git'
COPY_DIRS=('backend','web','tools','deploy')
COPY_FILES=('darkxray','requirements.txt','LICENSE','THIRD-PARTY-NOTICES.md','VERSION')
SNAPSHOT_FILES=COPY_DIRS+('darkxray','requirements.txt','VERSION','LICENSE','THIRD-PARTY-NOTICES.md')
MIB=1024*1024
STATUS_FILE:Path|None=None
JOB_ID=''
UPDATE_REF=''


def _status(state:str,phase:str,percent:int,message:str,**extra):
    if STATUS_FILE is None:return
    current={}
    try:
        if STATUS_FILE.is_file() and not STATUS_FILE.is_symlink() and STATUS_FILE.stat().st_size<1024*1024:
            value=json.loads(STATUS_FILE.read_text(encoding='utf-8'))
            if isinstance(value,dict):current=value
    except Exception:current={}
    current.update({'state':state,'phase':phase,'percent':max(0,min(100,int(percent))),'message':message,
                    'job_id':JOB_ID or current.get('job_id',''),'updated_at':time.time()})
    current.update(extra)
    STATUS_FILE.parent.mkdir(parents=True,exist_ok=True)
    tmp=STATUS_FILE.with_name('.'+STATUS_FILE.name+'.tmp-'+uuid.uuid4().hex)
    tmp.write_text(json.dumps(current,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');os.chmod(tmp,0o640)
    try:
        import pwd
        os.chown(tmp,0,pwd.getpwnam('darkxray').pw_gid)
    except Exception:pass
    os.replace(tmp,STATUS_FILE)


def _source_info(commit:str,version:str,ref:str):
    path=DATA/'installed-source.json';tmp=path.with_name('.'+path.name+'.tmp-'+uuid.uuid4().hex)
    value={'commit':commit,'version':version,'ref':ref,'installed_at':time.time()}
    tmp.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');os.chmod(tmp,0o640)
    try:
        import pwd
        os.chown(tmp,0,pwd.getpwnam('darkxray').pw_gid)
    except Exception:pass
    os.replace(tmp,path)


def run(args, **kw):
    return subprocess.run([str(x) for x in args], check=True, **kw)


def quiet(args):
    return subprocess.run([str(x) for x in args],check=False,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)


def write_wrapper():
    wrapper=Path('/usr/local/bin/darkxray')
    wrapper.write_text("""#!/usr/bin/env bash
set -Eeuo pipefail
export DARK_CONFIG=/etc/dark-xray/config.json DARK_DATA=/var/lib/dark-xray
case "${1:-menu}" in
  init|reset-password|account|stage-runtime|stage-panel-path|check|serve|backup|backup-verify|guard-control|doctor)
    if [[ $EUID -eq 0 ]]; then
      exec runuser -u darkxray -- env DARK_CONFIG="$DARK_CONFIG" DARK_DATA="$DARK_DATA" /opt/dark-xray/darkxray "$@"
    fi ;;
esac
exec /opt/dark-xray/darkxray "$@"
""")
    os.chmod(wrapper,0o755)


def source_commit(src:Path)->str:
    cp=subprocess.run(['git','-C',str(src),'rev-parse','HEAD'],capture_output=True,text=True,check=False)
    return cp.stdout.strip() if cp.returncode==0 else 'local-source'


def source_version(src:Path)->str:
    path=src/'VERSION'
    if not path.is_file():raise SystemExit('Invalid update source: VERSION is missing')
    value=path.read_text(encoding='utf-8').strip()
    if not re.fullmatch(r'\d+\.\d+\.\d+(?:-[A-Za-z0-9.-]+)?',value):raise SystemExit('Invalid update source: malformed VERSION')
    return value


def validate_source(src:Path):
    if src.is_symlink() or not src.is_dir():raise SystemExit('Invalid update source directory')
    required=[src/'backend/server.py',src/'backend/core.py',src/'backend/owner_recovery.py',src/'backend/updated.py',src/'backend/update_bridge.py',src/'backend/node_agent.py',src/'backend/node_runtime.py',src/'backend/node_updated.py',src/'tools/repo-check.py',src/'tools/doctor.py',src/'tools/backup_cli.py',src/'tools/guard-control.py',src/'tools/update_node.py',src/'tools/provision_node.py',src/'deploy/dark-xray.service',src/'deploy/dark-xray-update.service',src/'deploy/dark-xray-node.service',src/'deploy/dark-xray-node-guard.service',src/'deploy/dark-xray-node-update.service',src/'darkxray',src/'install-node.sh',src/'requirements.txt',src/'requirements-node.txt',src/'VERSION']
    if any(not p.is_file() or p.is_symlink() for p in required):raise SystemExit('Invalid update source: required application files are missing or unsafe')
    source_version(src)
    run([sys.executable,src/'tools/repo-check.py'],stdout=subprocess.DEVNULL)
    run([sys.executable,'-m','py_compile',src/'backend/server.py',src/'backend/core.py',src/'backend/manager.py',src/'backend/auth.py',src/'backend/owner_recovery.py',src/'backend/updated.py',src/'backend/update_bridge.py',src/'backend/node_agent.py',src/'backend/node_runtime.py',src/'backend/node_updated.py',src/'tools/menu.py',src/'tools/settings_apply.py',src/'tools/doctor.py',src/'tools/backup_cli.py',src/'tools/guard-control.py',src/'tools/target-vps-gate.py',src/'tools/provision_node.py',src/'tools/update_node.py'])
    run(['bash','-n',src/'darkxray',src/'setup.sh',src/'install-online.sh',src/'install-node.sh'])


def _snapshot_tree_bytes()->int:
    total=0
    for name in SNAPSHOT_FILES:
        item=APP/name
        if not item.exists() or item.is_symlink():continue
        if item.is_file():
            total+=item.stat().st_size;continue
        for path in item.rglob('*'):
            if path.is_file() and not path.is_symlink():
                try:total+=path.stat().st_size
                except OSError:pass
    return total


def database_preflight(path:Path=DB)->dict:
    if path.is_symlink() or not path.is_file():raise SystemExit('DARK database is missing or unsafe; update refused')
    try:
        with sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True,timeout=10) as db:
            quick=db.execute('PRAGMA quick_check').fetchone()[0]
            version=int(db.execute('PRAGMA user_version').fetchone()[0])
    except sqlite3.Error as ex:raise SystemExit('DARK database cannot be opened safely: '+type(ex).__name__) from ex
    if quick!='ok':raise SystemExit('DARK database quick_check failed; repair/backup before update')
    return {'quick_check':quick,'user_version':version,'bytes':path.stat().st_size}


def disk_preflight(db_info:dict)->dict:
    # Source snapshot + SQLite rollback copy + restore scratch + safety headroom.
    need=max(160*MIB,_snapshot_tree_bytes()+2*int(db_info['bytes'])+96*MIB)
    free_data=shutil.disk_usage(DATA).free
    free_tmp=shutil.disk_usage(tempfile.gettempdir()).free
    if free_data<need:raise SystemExit(f'Insufficient free space for safe rollback snapshots: need about {need//MIB} MiB on {DATA}')
    if free_tmp<160*MIB:raise SystemExit('Insufficient /tmp space for isolated dependency preflight (need at least 160 MiB)')
    return {'required_data_bytes':need,'free_data_bytes':free_data,'free_tmp_bytes':free_tmp}


def dependency_preflight(src:Path,work:Path):
    venv=work/'candidate-venv'
    current_py=APP/'.venv/bin/python'
    run([current_py,'-m','venv',venv])
    py=venv/'bin/python'
    run([py,'-m','pip','install','-q','--disable-pip-version-check','-r',src/'requirements.txt'])
    run([py,'-m','pip','check'],stdout=subprocess.DEVNULL)
    # Imports the candidate application without opening the live database/config.
    run([py,src/'backend/server.py','--help'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)


def source_snapshot(path:Path):
    path.parent.mkdir(parents=True,exist_ok=True)
    with tarfile.open(path,'w:gz') as tf:
        for name in SNAPSHOT_FILES:
            item=APP/name
            if item.exists():tf.add(item,arcname=name,recursive=True)
    os.chmod(path,0o600)


def database_snapshot(path:Path,source:Path=DB):
    path.parent.mkdir(parents=True,exist_ok=True)
    if path.exists():path.unlink()
    with sqlite3.connect(source.resolve().as_uri()+'?mode=ro',uri=True,timeout=30) as src, sqlite3.connect(path) as dst:
        src.backup(dst)
        if dst.execute('PRAGMA quick_check').fetchone()[0]!='ok':raise RuntimeError('Rollback database snapshot failed integrity check')
    os.chmod(path,0o600)


def restore_database(snapshot:Path,target:Path=DB):
    if snapshot.is_symlink() or not snapshot.is_file():raise RuntimeError('Rollback database snapshot is unsafe')
    with sqlite3.connect(snapshot.resolve().as_uri()+'?mode=ro',uri=True,timeout=10) as db:
        if db.execute('PRAGMA quick_check').fetchone()[0]!='ok':raise RuntimeError('Rollback database snapshot is corrupt')
    old=target.stat() if target.exists() else None
    target.parent.mkdir(parents=True,exist_ok=True)
    fd,name=tempfile.mkstemp(prefix='.dark-db-restore-',dir=target.parent);os.close(fd);tmp=Path(name)
    try:
        shutil.copyfile(snapshot,tmp)
        os.chmod(tmp,(old.st_mode&0o777) if old else 0o600)
        if old and os.geteuid()==0:os.chown(tmp,old.st_uid,old.st_gid)
        with tmp.open('rb+') as f:f.flush();os.fsync(f.fileno())
        os.replace(tmp,target)
        for suffix in ('-wal','-shm'):Path(str(target)+suffix).unlink(missing_ok=True)
        dfd=os.open(target.parent,os.O_RDONLY)
        try:os.fsync(dfd)
        finally:os.close(dfd)
    finally:
        tmp.unlink(missing_ok=True)
    database_preflight(target)


def clear_installed_source():
    for name in COPY_DIRS:
        dst=APP/name
        if dst.exists():shutil.rmtree(dst)
    for name in COPY_FILES:
        dst=APP/name
        if dst.exists() or dst.is_symlink():dst.unlink(missing_ok=True)


def normalize_source_permissions(root:Path=APP):
    if not root.is_dir() or root.is_symlink():raise RuntimeError('Installed application root must be a real directory')
    os.chmod(root,0o755)
    for name in COPY_DIRS:
        base=root/name
        if not base.exists():continue
        if base.is_symlink() or not base.is_dir():raise RuntimeError('Installed source directory has an unsafe shape: '+name)
        os.chmod(base,0o755)
        for path in base.rglob('*'):
            if path.is_symlink():continue
            if path.is_dir():os.chmod(path,0o755)
            elif path.is_file():os.chmod(path,0o644)
    for name in COPY_FILES:
        path=root/name
        if not path.exists():continue
        if path.is_symlink() or not path.is_file():raise RuntimeError('Installed source file has an unsafe shape: '+name)
        os.chmod(path,0o755 if name=='darkxray' else 0o644)


def copy_source(src:Path):
    clear_installed_source()
    for name in COPY_DIRS:shutil.copytree(src/name,APP/name,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    for name in COPY_FILES:
        if (src/name).exists():shutil.copy2(src/name,APP/name)
    normalize_source_permissions(APP)


def install_runtime_files():
    py=APP/'.venv/bin/python'
    run([py,'-m','pip','install','-q','--disable-pip-version-check','-r',APP/'requirements.txt'])
    run([py,'-m','pip','check'],stdout=subprocess.DEVNULL)
    for unit in ('dark-xray.service','dark-xray-guard.service'):
        shutil.copy2(APP/'deploy'/unit,Path('/etc/systemd/system')/unit);os.chmod(Path('/etc/systemd/system')/unit,0o644)
    update_unit=APP/'deploy/dark-xray-update.service'
    installed_update_unit=Path('/etc/systemd/system/dark-xray-update.service')
    if update_unit.is_file() and not update_unit.is_symlink():
        shutil.copy2(update_unit,installed_update_unit);os.chmod(installed_update_unit,0o644)
    else:
        # Rollback to a pre-broker release must remain possible during the
        # one-time bootstrap from older RC builds.
        quiet(['systemctl','disable','--now','dark-xray-update.service'])
        installed_update_unit.unlink(missing_ok=True)
    write_wrapper();run(['systemctl','daemon-reload']);run(['systemctl','enable','dark-xray.service'])
    if update_unit.is_file() and not update_unit.is_symlink():
        # --now on an already-active broker is a no-op; on the first CLI
        # bootstrap it starts the newly installed root-owned broker.
        run(['systemctl','enable','--now','dark-xray-update.service'])



def _compat_panel_route()->dict:
    """Strict local UI/asset probe for pre-panel_route legacy Doctor versions."""
    raw=json.loads((CONF/'config.json').read_text(encoding='utf-8'))
    origin=urlsplit(str(raw.get('public_origin') or ''))
    if origin.scheme not in {'http','https'} or not origin.hostname:
        raise RuntimeError('legacy config has no usable public_origin')
    bind_host=str(raw.get('bind_host') or '127.0.0.1')
    target='127.0.0.1' if bind_host in {'0.0.0.0','::'} else bind_host
    port_raw=raw.get('bind_port')
    bind_port=int(port_raw if port_raw is not None else (origin.port or (443 if origin.scheme=='https' else 80)))
    if not 1 <= bind_port <= 65535: raise RuntimeError('legacy bind_port is invalid')
    panel_path=str(raw.get('panel_path') or '/')
    if not panel_path.startswith('/'): raise RuntimeError('legacy panel_path is invalid')
    base='' if panel_path=='/' else panel_path.rstrip('/')
    host=origin.netloc
    def status(path:str)->int:
        sock=socket.create_connection((target,bind_port),timeout=2.5)
        try:
            if origin.scheme=='https':
                ctx=ssl.create_default_context();sock=ctx.wrap_socket(sock,server_hostname=origin.hostname)
            req=f'GET {path} HTTP/1.1\r\nHost: {host}\r\nConnection: close\r\nUser-Agent: darkxray-update-compat\r\n\r\n'.encode()
            sock.sendall(req);raw_head=b''
            while b'\r\n' not in raw_head and len(raw_head)<4096:
                chunk=sock.recv(512)
                if not chunk:break
                raw_head+=chunk
            first=raw_head.split(b'\r\n',1)[0].decode('ascii','replace').split()
            return int(first[1]) if len(first)>=2 and first[1].isdigit() else 0
        finally:
            try:sock.close()
            except Exception:pass
    ui=status(base+'/');asset=status(base+'/assets/style.css')
    return {'ui_status':ui,'asset_status':asset,'ok':ui==200 and asset==200,'probe':'legacy-compat'}


def _doctor_once()->tuple[bool,str]:
    cp=subprocess.run([APP/'.venv/bin/python',APP/'tools/doctor.py','--config',CONF/'config.json','--data',DATA],capture_output=True,text=True,check=False,timeout=15)
    if cp.returncode:return False,'doctor exit '+str(cp.returncode)
    try:doc=json.loads(cp.stdout);checks=doc.get('checks',{})
    except Exception:return False,'doctor returned invalid JSON'
    route=checks.get('panel_route') if isinstance(checks.get('panel_route'),dict) else {}
    if not route and checks.get('configuration')=='ok' and checks.get('database')=='ok':
        try:route=_compat_panel_route()
        except Exception as ex:route={'ok':False,'probe':'legacy-compat','error':type(ex).__name__+': '+str(ex)[:180]}
    ok=checks.get('configuration')=='ok' and checks.get('database')=='ok' and route.get('ok') is True
    detail='config='+str(checks.get('configuration'))+', db='+str(checks.get('database'))+', panel='+str(route)
    return ok,detail


def require_live_panel(timeout:float=10.0):
    deadline=time.monotonic()+timeout;last='not checked'
    while time.monotonic()<deadline:
        try:
            ok,last=_doctor_once()
            if ok:return
        except Exception as ex:last=type(ex).__name__+': '+str(ex)[:200]
        time.sleep(.35)
    raise RuntimeError('Local DARK panel health verification failed: '+last)


def activate():
    run(['systemctl','restart','dark-xray.service']);run(['systemctl','is-active','--quiet','dark-xray.service'])
    require_live_panel(12.0)


def rollback(source_backup:Path,db_backup:Path,guard_was_active:bool)->bool:
    _status('rolling_back','rollback',88,'Activation failed; restoring previous DARK XRAY source and database')
    print('Update activation failed; restoring previous DARK XRAY source + database...',file=sys.stderr)
    quiet(['systemctl','stop','dark-xray.service'])
    try:
        clear_installed_source()
        with tarfile.open(source_backup,'r:gz') as tf:
            for member in tf.getmembers():
                target=(APP/member.name).resolve()
                if APP.resolve() not in target.parents and target!=APP.resolve():raise RuntimeError('Unsafe rollback archive member')
            tf.extractall(APP,filter='data')
        normalize_source_permissions(APP)
        restore_database(db_backup)
        install_runtime_files();activate()
        if guard_was_active:run(['systemctl','restart','dark-xray-guard.service'])
        _status('rolled_back','rollback',100,'Update failed; previous DARK XRAY source and database were restored successfully',finished_at=time.time(),rollback_ok=True)
        print('Previous DARK XRAY source and database restored; panel route is healthy.',file=sys.stderr);return True
    except Exception as ex:
        _status('failed','rollback_failed',100,'CRITICAL: automatic rollback could not reactivate the previous installation',finished_at=time.time(),rollback_ok=False,error=type(ex).__name__+': '+str(ex)[:300])
        print('CRITICAL: rollback could not reactivate previous installation: '+type(ex).__name__+': '+str(ex)[:300],file=sys.stderr)
        quiet(['systemctl','status','dark-xray.service','--no-pager','-l']);return False


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source',type=Path,help='Already-cloned candidate source tree (online bootstrap uses this)')
    p.add_argument('--ref',default=os.environ.get('DARK_UPDATE_REF','main'),help='Git branch/tag/commit when --source is omitted')
    p.add_argument('--non-interactive',action='store_true')
    p.add_argument('--status-file',type=Path)
    p.add_argument('--job-id',default='')
    a=p.parse_args()
    global STATUS_FILE,JOB_ID,UPDATE_REF
    STATUS_FILE=a.status_file;JOB_ID=str(a.job_id or '');UPDATE_REF=str(a.ref or '')
    if os.geteuid()!=0:raise SystemExit('Root is required for update')
    if APP.is_symlink() or not APP.is_dir() or not (APP/'.venv/bin/python').is_file():raise SystemExit('DARK XRAY application path is incomplete or unsafe; use installer repair mode')
    if CONF.is_symlink() or DATA.is_symlink() or not (CONF/'config.json').is_file() or not DATA.is_dir():raise SystemExit('Configuration/data paths are incomplete or unsafe; use installer repair mode')
    _status('running','current_health',20,'Checking current DARK installation health')
    database_info=database_preflight(DB)
    try:require_live_panel(5.0)
    except Exception as ex:raise SystemExit('Current DARK panel is not healthy enough for a rollback-safe update; run darkxray doctor/repair first: '+str(ex))

    _status('running','resolve',25,'Resolving candidate source')
    temp=None;preflight=Path(tempfile.mkdtemp(prefix='dark-xray-preflight.'));src=a.source.resolve() if a.source else None
    try:
        if src is None:
            temp=Path(tempfile.mkdtemp(prefix='dark-xray-update.'));src=temp/'src'
            run(['git','clone','--filter=blob:none','--no-checkout',REPO,src],stdout=subprocess.DEVNULL)
            run(['git','-C',src,'fetch','--depth','1','origin',a.ref],stdout=subprocess.DEVNULL)
            run(['git','-C',src,'checkout','--detach','FETCH_HEAD'],stdout=subprocess.DEVNULL)
        _status('running','validate_source',32,'Validating candidate source and syntax')
        validate_source(src);candidate=source_commit(src);candidate_version=source_version(src)
        current_version=(APP/'VERSION').read_text(encoding='utf-8').strip() if (APP/'VERSION').is_file() else 'unknown'
        print('Current version:',current_version);print('Candidate version:',candidate_version);print('Candidate source:',candidate)
        _status('running','rollback_preflight',40,'Checking database integrity and rollback disk space')
        space=disk_preflight(database_info)
        print('Rollback preflight: database quick_check=ok; free data space=',space['free_data_bytes']//MIB,'MiB')
        print('Dependency preflight: building isolated candidate environment...')
        _status('running','dependencies',50,'Building isolated candidate environment and checking dependencies')
        dependency_preflight(src,preflight);print('Dependency preflight: passed')

        stamp=time.strftime('%Y%m%d-%H%M%S');backups=DATA/'backups'
        source_backup=backups/f'pre-update-source-{stamp}.tar.gz';db_backup=backups/f'pre-update-db-{stamp}.sqlite3'
        _status('running','snapshot_source',60,'Creating rollback source snapshot')
        source_snapshot(source_backup);print('Rollback source snapshot:',source_backup)
        guard_was_active=quiet(['systemctl','is-active','--quiet','dark-xray-guard.service']).returncode==0
        quiet(['systemctl','stop','dark-xray.service'])
        try:
            # Capture SQLite after the old service has flushed traffic and closed the DB.
            _status('running','snapshot_database',68,'Stopping panel briefly and creating SQLite rollback snapshot')
            database_preflight(DB);database_snapshot(db_backup);print('Rollback database snapshot:',db_backup)
        except Exception as ex:
            # Source is still untouched here; restore availability immediately.
            try:activate()
            except Exception as restart_ex:raise SystemExit('Database snapshot failed and current panel could not be reactivated: '+str(restart_ex)) from ex
            raise SystemExit('Database snapshot failed; current version was reactivated without changing source') from ex
        try:
            _status('running','apply_source',76,'Applying verified DARK source and runtime files')
            copy_source(src);install_runtime_files()
            _status('restarting','restart',84,'Restarting DARK panel and verifying local route / asset health')
            activate()
            if guard_was_active:run(['systemctl','restart','dark-xray-guard.service'])
        except Exception:
            ok=rollback(source_backup,db_backup,guard_was_active)
            if not ok:raise SystemExit('Update failed and automatic source/database rollback also failed; inspect systemd and rollback snapshots')
            raise SystemExit('Update failed; previous source + database were restored successfully')
        _source_info(candidate,candidate_version,UPDATE_REF)
        _status('success','complete',100,'DARK XRAY updated successfully; local health checks passed',finished_at=time.time(),installed={'commit':candidate,'version':candidate_version,'ref':UPDATE_REF},rollback_ok=None)
        print('DARK XRAY updated successfully.');print('Installed source:',candidate);print('Local panel route/asset Doctor probe: passed');print('Run: darkxray')
    finally:
        shutil.rmtree(preflight,ignore_errors=True)
        if temp:shutil.rmtree(temp,ignore_errors=True)

if __name__=='__main__':
    try:
        main()
    except SystemExit as ex:
        if STATUS_FILE is not None:
            try:
                read_status=json.loads(STATUS_FILE.read_text(encoding='utf-8')) if STATUS_FILE.is_file() else {}
            except Exception:
                read_status={}
            if read_status.get('state') not in {'rolled_back','success','failed'}:
                _status('failed','failed',100,str(ex)[:700] or 'Update failed',finished_at=time.time())
        raise
    except Exception as ex:
        if STATUS_FILE is not None:
            try:
                read_status=json.loads(STATUS_FILE.read_text(encoding='utf-8')) if STATUS_FILE.is_file() else {}
            except Exception:
                read_status={}
            if read_status.get('state') not in {'rolled_back','success','failed'}:
                _status('failed','failed',100,type(ex).__name__+': '+str(ex)[:650],finished_at=time.time())
        raise
