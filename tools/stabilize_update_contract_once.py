#!/usr/bin/env python3
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]

UPDATE=r'''#!/usr/bin/env python3
"""Rollback-safe DARK XRAY source updater.

The updater validates the candidate before downtime, checks the live installation,
creates independent source + SQLite rollback snapshots, then activates the new
source. A successful systemd restart is not enough: the local Doctor must also
confirm configuration, SQLite integrity, panel route and a real web asset.

If activation fails after the new code has touched the database, both source and
database are restored before the previous service is reactivated.
"""
from __future__ import annotations
import argparse,json,os,re,shutil,sqlite3,subprocess,sys,tarfile,tempfile,time
from pathlib import Path

APP=Path('/opt/dark-xray'); CONF=Path('/etc/dark-xray'); DATA=Path('/var/lib/dark-xray')
DB=DATA/'dark.sqlite3'
REPO='https://github.com/darktunnelmika/dark-xray.git'
COPY_DIRS=('backend','web','tools','deploy')
COPY_FILES=('darkxray','requirements.txt','LICENSE','THIRD-PARTY-NOTICES.md','VERSION')
SNAPSHOT_FILES=COPY_DIRS+('darkxray','requirements.txt','VERSION','LICENSE','THIRD-PARTY-NOTICES.md')
MIB=1024*1024


def run(args, **kw):
    return subprocess.run([str(x) for x in args], check=True, **kw)


def quiet(args):
    return subprocess.run([str(x) for x in args],check=False,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)


def write_wrapper():
    wrapper=Path('/usr/local/bin/darkxray')
    wrapper.write_text('''#!/usr/bin/env bash
set -Eeuo pipefail
export DARK_CONFIG=/etc/dark-xray/config.json DARK_DATA=/var/lib/dark-xray
case "${1:-menu}" in
  init|reset-password|account|stage-runtime|stage-panel-path|check|serve|backup|doctor)
    if [[ $EUID -eq 0 ]]; then
      exec runuser -u darkxray -- env DARK_CONFIG="$DARK_CONFIG" DARK_DATA="$DARK_DATA" /opt/dark-xray/darkxray "$@"
    fi ;;
esac
exec /opt/dark-xray/darkxray "$@"
''')
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
    required=[src/'backend/server.py',src/'backend/core.py',src/'backend/owner_recovery.py',src/'tools/repo-check.py',src/'tools/doctor.py',src/'deploy/dark-xray.service',src/'darkxray',src/'requirements.txt',src/'VERSION']
    if any(not p.is_file() or p.is_symlink() for p in required):raise SystemExit('Invalid update source: required application files are missing or unsafe')
    source_version(src)
    run([sys.executable,src/'tools/repo-check.py'],stdout=subprocess.DEVNULL)
    run([sys.executable,'-m','py_compile',src/'backend/server.py',src/'backend/core.py',src/'backend/manager.py',src/'backend/auth.py',src/'backend/owner_recovery.py',src/'tools/menu.py',src/'tools/settings_apply.py',src/'tools/doctor.py'])
    run(['bash','-n',src/'darkxray',src/'setup.sh',src/'install-online.sh'])


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


def disk_preflight(src:Path,db_info:dict)->dict:
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
    # Import the candidate application without touching the live DB/config.
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
    fd,tmp=tempfile.mkstemp(prefix='.dark-db-restore-',dir=target.parent);os.close(fd);tmp=Path(tmp)
    try:
        shutil.copyfile(snapshot,tmp)
        os.chmod(tmp,(old.st_mode&0o777) if old else 0o600)
        if old and os.geteuid()==0:os.chown(tmp,old.st_uid,old.st_gid)
        with tmp.open('rb') as f:os.fsync(f.fileno())
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
    write_wrapper();run(['systemctl','daemon-reload']);run(['systemctl','enable','dark-xray.service'])


def _doctor_once()->tuple[bool,str]:
    cp=subprocess.run([APP/'.venv/bin/python',APP/'tools/doctor.py','--config',CONF/'config.json','--data',DATA],capture_output=True,text=True,check=False,timeout=15)
    if cp.returncode:return False,'doctor exit '+str(cp.returncode)
    try:doc=json.loads(cp.stdout);checks=doc.get('checks',{})
    except Exception:return False,'doctor returned invalid JSON'
    route=checks.get('panel_route') if isinstance(checks.get('panel_route'),dict) else {}
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
        print('Previous DARK XRAY source and database restored; panel route is healthy.',file=sys.stderr);return True
    except Exception as ex:
        print('CRITICAL: rollback could not reactivate previous installation: '+type(ex).__name__+': '+str(ex)[:300],file=sys.stderr)
        quiet(['systemctl','status','dark-xray.service','--no-pager','-l']);return False


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source',type=Path,help='Already-cloned candidate source tree (online bootstrap uses this)')
    p.add_argument('--ref',default=os.environ.get('DARK_UPDATE_REF','main'),help='Git branch/tag/commit when --source is omitted')
    p.add_argument('--non-interactive',action='store_true');a=p.parse_args()
    if os.geteuid()!=0:raise SystemExit('Root is required for update')
    if APP.is_symlink() or not APP.is_dir() or not (APP/'.venv/bin/python').is_file():raise SystemExit('DARK XRAY application path is incomplete or unsafe; use installer repair mode')
    if CONF.is_symlink() or DATA.is_symlink() or not (CONF/'config.json').is_file() or not DATA.is_dir():raise SystemExit('Configuration/data paths are incomplete or unsafe; use installer repair mode')
    # A rollback can only be called safe when the current panel has a known-good baseline.
    database_info=database_preflight(DB)
    try:require_live_panel(5.0)
    except Exception as ex:raise SystemExit('Current DARK panel is not healthy enough for a rollback-safe update; run darkxray doctor/repair first: '+str(ex))

    temp=None;preflight=Path(tempfile.mkdtemp(prefix='dark-xray-preflight.'));src=a.source.resolve() if a.source else None
    try:
        if src is None:
            temp=Path(tempfile.mkdtemp(prefix='dark-xray-update.'));src=temp/'src'
            run(['git','clone','--filter=blob:none','--no-checkout',REPO,src],stdout=subprocess.DEVNULL)
            run(['git','-C',src,'fetch','--depth','1','origin',a.ref],stdout=subprocess.DEVNULL)
            run(['git','-C',src,'checkout','--detach','FETCH_HEAD'],stdout=subprocess.DEVNULL)
        validate_source(src);candidate=source_commit(src);candidate_version=source_version(src)
        current_version=(APP/'VERSION').read_text(encoding='utf-8').strip() if (APP/'VERSION').is_file() else 'unknown'
        print('Current version:',current_version);print('Candidate version:',candidate_version);print('Candidate source:',candidate)
        space=disk_preflight(src,database_info)
        print('Rollback preflight: database quick_check=ok; free data space=',space['free_data_bytes']//MIB,'MiB')
        print('Dependency preflight: building isolated candidate environment...')
        dependency_preflight(src,preflight)
        print('Dependency preflight: passed')

        stamp=time.strftime('%Y%m%d-%H%M%S');backups=DATA/'backups'
        source_backup=backups/f'pre-update-source-{stamp}.tar.gz';db_backup=backups/f'pre-update-db-{stamp}.sqlite3'
        source_snapshot(source_backup);database_snapshot(db_backup)
        print('Rollback source snapshot:',source_backup);print('Rollback database snapshot:',db_backup)
        guard_was_active=quiet(['systemctl','is-active','--quiet','dark-xray-guard.service']).returncode==0
        quiet(['systemctl','stop','dark-xray.service'])
        try:
            copy_source(src);install_runtime_files();activate()
            if guard_was_active:run(['systemctl','restart','dark-xray-guard.service'])
        except Exception:
            ok=rollback(source_backup,db_backup,guard_was_active)
            if not ok:raise SystemExit('Update failed and automatic source/database rollback also failed; inspect systemd and rollback snapshots')
            raise SystemExit('Update failed; previous source + database were restored successfully')
        print('DARK XRAY updated successfully.');print('Installed source:',candidate);print('Local panel route/asset Doctor probe: passed');print('Run: darkxray')
    finally:
        shutil.rmtree(preflight,ignore_errors=True)
        if temp:shutil.rmtree(temp,ignore_errors=True)

if __name__=='__main__':main()
'''
(ROOT/'tools/update.py').write_text(UPDATE,encoding='utf-8')

# Fresh-install preflight: architecture, disk, Python, post-apt port check and hard final panel route gate.
p=ROOT/'install-online.sh';s=p.read_text()
old="""valid_source_ref(){ [[ -n \"$1\" && ${#1} -le 200 && \"$1\" != -* && \"$1\" =~ ^[A-Za-z0-9._/@+-]+$ ]]; }
fetch_source(){"""
new="""valid_source_ref(){ [[ -n \"$1\" && ${#1} -le 200 && \"$1\" != -* && \"$1\" =~ ^[A-Za-z0-9._/@+-]+$ ]]; }
supported_arch(){ case \"$(uname -m 2>/dev/null || true)\" in x86_64|amd64|aarch64|arm64) return 0;; *) return 1;; esac; }
free_root_kb(){ df -Pk / 2>/dev/null | awk 'NR==2{print $4}'; }
fetch_source(){"""
if old not in s:raise SystemExit('install helper anchor missing')
s=s.replace(old,new,1)
old="""command -v apt-get >/dev/null 2>&1 || fail \"This installer currently supports Ubuntu/Debian (apt).\"

banner; progress 1 \"Preflight checks\""""
new="""command -v apt-get >/dev/null 2>&1 || fail \"This installer currently supports Ubuntu/Debian (apt).\"
supported_arch || fail \"Only Linux amd64/x86_64 and arm64/aarch64 are supported by the Xray installer.\"

banner; progress 1 \"Preflight checks\""""
if old not in s:raise SystemExit('install arch anchor missing')
s=s.replace(old,new,1)
old="""progress 5 \"Detecting server network and SSH\"
DETECTED_IP="""
new="""ROOT_FREE_KB=\"$(free_root_kb || true)\"; [[ \"$ROOT_FREE_KB\" =~ ^[0-9]+$ ]] || fail \"Could not determine free disk space\"
(( ROOT_FREE_KB >= 524288 )) || fail \"Fresh install requires at least 512 MiB free on the root filesystem\"
progress 5 \"Detecting server network and SSH\"
DETECTED_IP="""
if old not in s:raise SystemExit('install disk anchor missing')
s=s.replace(old,new,1)
old="""apt-get install -y -q git curl ca-certificates python3 python3-venv unzip openssl iproute2 >/dev/null
[[ \"$MODE\" == 1 ]] && apt-get install -y -q certbot >/dev/null

TMP="""
new="""apt-get install -y -q git curl ca-certificates python3 python3-venv unzip openssl iproute2 >/dev/null
[[ \"$MODE\" == 1 ]] && apt-get install -y -q certbot >/dev/null
python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3,11) else 1)' || fail \"Python 3.11+ is required (Ubuntu 24.04 / Debian 12 or equivalent).\"
command -v ss >/dev/null 2>&1 || fail \"iproute2/ss is required for port safety checks\"
port_busy \"$PANEL_PORT\" && fail \"Panel port $PANEL_PORT became occupied during prerequisite installation\"

TMP="""
if old not in s:raise SystemExit('install python anchor missing')
s=s.replace(old,new,1)
old="""progress 97 \"Running final doctor\"; /usr/local/bin/darkxray doctor || warn \"Doctor reported warnings; review them before production use\"
progress 100 \"DARK XRAY installation complete\""""
new="""progress 97 \"Running final doctor\"
DOCTOR_JSON=\"$(/usr/local/bin/darkxray doctor)\" || fail \"Final Doctor command failed\"
printf '%s\\n' \"$DOCTOR_JSON\"
printf '%s\\n' \"$DOCTOR_JSON\" | python3 -c 'import json,sys; c=json.load(sys.stdin).get("checks",{}); r=c.get("panel_route",{}); raise SystemExit(0 if c.get("configuration")=="ok" and c.get("database")=="ok" and isinstance(r,dict) and r.get("ok") is True else 1)' || fail \"Final Doctor critical checks failed: configuration/database/panel route must be healthy\"
progress 100 \"DARK XRAY installation complete\"""
if old not in s:raise SystemExit('install doctor anchor missing')
s=s.replace(old,new,1)
p.write_text(s)

# Updater rollback unit tests.
(ROOT/'tests/test_update_transaction.py').write_text(r'''import importlib.util
import json
import os
import sqlite3
from pathlib import Path

import pytest

ROOT=Path(__file__).resolve().parents[1]
SPEC=importlib.util.spec_from_file_location('dark_update_tx',ROOT/'tools/update.py')
assert SPEC and SPEC.loader
UPDATE=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(UPDATE)


def make_db(path:Path,value:str='before'):
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE state(value TEXT NOT NULL)')
        db.execute('INSERT INTO state VALUES(?)',(value,));db.execute('PRAGMA user_version=7');db.commit()


def value(path:Path):
    with sqlite3.connect(path) as db:return db.execute('SELECT value FROM state').fetchone()[0]


def test_database_snapshot_restore_roundtrip(tmp_path):
    db=tmp_path/'dark.sqlite3';snap=tmp_path/'rollback.sqlite3';make_db(db)
    info=UPDATE.database_preflight(db);assert info['quick_check']=='ok' and info['user_version']==7
    UPDATE.database_snapshot(snap,db)
    with sqlite3.connect(db) as con:con.execute("UPDATE state SET value='after'");con.commit()
    UPDATE.restore_database(snap,db)
    assert value(db)=='before' and UPDATE.database_preflight(db)['quick_check']=='ok'


def test_database_preflight_rejects_symlink(tmp_path):
    real=tmp_path/'real.sqlite3';make_db(real);link=tmp_path/'dark.sqlite3';link.symlink_to(real)
    with pytest.raises(SystemExit,match='unsafe'):UPDATE.database_preflight(link)


def test_database_preflight_rejects_non_sqlite(tmp_path):
    bad=tmp_path/'dark.sqlite3';bad.write_text('not sqlite')
    with pytest.raises(SystemExit,match='cannot be opened'):UPDATE.database_preflight(bad)


def test_doctor_gate_requires_config_db_and_panel_route(monkeypatch):
    good={'checks':{'configuration':'ok','database':'ok','panel_route':{'ok':True,'ui_status':200,'asset_status':200}}}
    class CP:
        returncode=0;stdout=json.dumps(good)
    monkeypatch.setattr(UPDATE.subprocess,'run',lambda *a,**k:CP())
    assert UPDATE._doctor_once()[0] is True
    good['checks']['panel_route']['asset_status']=404;good['checks']['panel_route']['ok']=False
    assert UPDATE._doctor_once()[0] is False


def test_source_version_is_strict(tmp_path):
    (tmp_path/'VERSION').write_text('0.7.2-standalone-lab\n')
    assert UPDATE.source_version(tmp_path)=='0.7.2-standalone-lab'
    (tmp_path/'VERSION').write_text('latest please')
    with pytest.raises(SystemExit,match='malformed'):UPDATE.source_version(tmp_path)
''',encoding='utf-8')

# Active frontend ↔ backend contract and panel-prefix asset regression tests.
(ROOT/'tests/test_web_contract.py').write_text(r'''import re
from pathlib import Path

from fastapi.testclient import TestClient

from auth import Auth
from core import Config,CoreEngine
from dark_policy import Actor,Store
from manager import Manager
from server import make_app

ROOT=Path(__file__).resolve().parents[1]


def app_env(tmp_path,panel_path='/dark-admin'):
    store=Store(tmp_path/'dark.sqlite3')
    config=Config(xray_binary=str(tmp_path/'missing-xray'),xray_assets=str(tmp_path),panel_path=panel_path,test_engine=True)
    engine=CoreEngine(config,store,tmp_path/'runtime');manager=Manager(store,engine);auth=Auth(store,tmp_path/'secret.key')
    auth.bootstrap('dark','ContractPass88');manager.owner_put(Actor('dark','owner',{}),'dark',name='DARK',allowed=[])
    return store,make_app(manager,auth,background=False),config


def active_scripts():
    html=(ROOT/'web/index.html').read_text(encoding='utf-8')
    return [ROOT/'web'/name for name in re.findall(r'<script[^>]+src="assets/([^"?]+\.js)"',html)]


def test_every_index_asset_loads_under_panel_prefix(tmp_path):
    store,app,config=app_env(tmp_path)
    try:
        with TestClient(app,base_url=config.public_origin) as c:
            r=c.get('/dark-admin/');assert r.status_code==200
            refs=re.findall(r'(?:src|href)="(assets/[^"?]+)"',r.text)
            assert refs and len(refs)==len(set(refs))
            for ref in refs:
                got=c.get('/dark-admin/'+ref)
                assert got.status_code==200,(ref,got.status_code)
            assert c.get('/assets/style.css').status_code==404
    finally:store.close()


def test_active_web_api_literals_have_backend_route_prefix(tmp_path):
    store,app,_=app_env(tmp_path,'/')
    try:
        routes={getattr(r,'path','') for r in app.routes}
        dynamic_prefixes={r.split('{',1)[0] for r in routes if '{' in r}
        literals=set()
        for path in active_scripts():
            text=path.read_text(encoding='utf-8')
            literals.update(re.findall(r"(?:api|appUrl)\(\s*['\"](/api/[^'\"]*)['\"]",text))
        assert literals
        missing=[]
        for literal in sorted(literals):
            route=literal.split('?',1)[0]
            if route in routes:continue
            if any(route.startswith(p) or p.startswith(route) for p in dynamic_prefixes):continue
            missing.append(route)
        assert not missing,'Frontend API literals without backend route coverage: '+', '.join(missing)
    finally:store.close()


def test_active_web_never_bypasses_panel_base_for_direct_fetch_or_links():
    html=(ROOT/'web/index.html').read_text(encoding='utf-8')
    assert not re.search(r'(?:src|href)="/(?:assets|api)/',html)
    for path in active_scripts():
        text=path.read_text(encoding='utf-8')
        assert not re.search(r"\bfetch\(\s*['\"]/(?:api|assets)/",text),path.name
        assert not re.search(r'(?:href|src)=\\?["\']/(?:api|assets)/',text),path.name
''',encoding='utf-8')

# Run new contracts in the normal CI suite.
p=ROOT/'tests/run-tests.sh';s=p.read_text()
anchor="python -m pytest tests/test_update_permissions.py -q --junitxml=qa/junit/update-permissions.xml\n"
insert=anchor+"python -m pytest tests/test_update_transaction.py -q --junitxml=qa/junit/update-transaction.xml\npython -m pytest tests/test_web_contract.py -q --junitxml=qa/junit/web-contract.xml\n"
if anchor not in s:raise SystemExit('run-tests update anchor missing')
p.write_text(s.replace(anchor,insert,1))

# Visible patch-level version; backend reads this file directly.
(ROOT/'VERSION').write_text('0.7.2-standalone-lab\n',encoding='utf-8')
print('update/install/web contract stabilization prepared')
