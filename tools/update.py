#!/usr/bin/env python3
"""Transactional-ish DARK XRAY source updater.

The updater preserves /etc and /var/lib, validates candidate source before stopping
the service, snapshots the currently installed application source, and restores
that snapshot if the new application cannot become active. The virtualenv is
reused; requirements are reinstalled after both forward update and rollback.
"""
from __future__ import annotations
import argparse, os, shutil, subprocess, sys, tarfile, tempfile, time
from pathlib import Path

APP=Path('/opt/dark-xray'); CONF=Path('/etc/dark-xray'); DATA=Path('/var/lib/dark-xray')
REPO='https://github.com/darktunnelmika/dark-xray.git'
COPY_DIRS=('backend','web','tools','deploy')
COPY_FILES=('darkxray','requirements.txt','LICENSE','THIRD-PARTY-NOTICES.md','VERSION')
SNAPSHOT_FILES=COPY_DIRS+('darkxray','requirements.txt','VERSION','LICENSE','THIRD-PARTY-NOTICES.md')

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
  init|reset-password|check|serve|backup|doctor)
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

def validate_source(src:Path):
    required=[src/'backend/server.py',src/'backend/core.py',src/'tools/repo-check.py',src/'deploy/dark-xray.service',src/'darkxray',src/'requirements.txt']
    if any(not p.is_file() for p in required):raise SystemExit('Invalid update source: required application files are missing')
    run([sys.executable,src/'tools/repo-check.py'],stdout=subprocess.DEVNULL)
    run([sys.executable,'-m','py_compile',src/'backend/server.py',src/'backend/core.py',src/'backend/manager.py',src/'tools/menu.py',src/'tools/settings_apply.py'])
    run(['bash','-n',src/'darkxray',src/'setup.sh',src/'install-online.sh'])

def source_snapshot(path:Path):
    path.parent.mkdir(parents=True,exist_ok=True)
    with tarfile.open(path,'w:gz') as tf:
        for name in SNAPSHOT_FILES:
            item=APP/name
            if item.exists():tf.add(item,arcname=name,recursive=True)

def clear_installed_source():
    for name in COPY_DIRS:
        dst=APP/name
        if dst.exists():shutil.rmtree(dst)
    for name in COPY_FILES:
        dst=APP/name
        if dst.exists() or dst.is_symlink():dst.unlink(missing_ok=True)

def copy_source(src:Path):
    clear_installed_source()
    for name in COPY_DIRS:
        shutil.copytree(src/name,APP/name,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    for name in COPY_FILES:
        if (src/name).exists():shutil.copy2(src/name,APP/name)
    os.chmod(APP/'darkxray',0o755)

def install_runtime_files():
    run([APP/'.venv/bin/python','-m','pip','install','-q','--disable-pip-version-check','-r',APP/'requirements.txt'])
    for unit in ('dark-xray.service','dark-xray-guard.service'):
        shutil.copy2(APP/'deploy'/unit,Path('/etc/systemd/system')/unit)
    write_wrapper();run(['systemctl','daemon-reload']);run(['systemctl','enable','dark-xray.service'])

def activate():
    run(['systemctl','restart','dark-xray.service'])
    run(['systemctl','is-active','--quiet','dark-xray.service'])

def rollback(snapshot:Path,guard_was_active:bool)->bool:
    print('Update activation failed; restoring previous DARK XRAY source...',file=sys.stderr)
    quiet(['systemctl','stop','dark-xray.service'])
    try:
        clear_installed_source()
        with tarfile.open(snapshot,'r:gz') as tf:
            # Snapshot entries are fixed by source_snapshot; reject unexpected paths anyway.
            for member in tf.getmembers():
                target=(APP/member.name).resolve()
                if APP.resolve() not in target.parents and target!=APP.resolve():raise RuntimeError('Unsafe rollback archive member')
            tf.extractall(APP,filter='data')
        os.chmod(APP/'darkxray',0o755)
        install_runtime_files();activate()
        if guard_was_active:quiet(['systemctl','restart','dark-xray-guard.service'])
        print('Previous DARK XRAY source restored and service is active.',file=sys.stderr)
        return True
    except Exception as ex:
        print('CRITICAL: rollback could not reactivate previous source: '+type(ex).__name__,file=sys.stderr)
        quiet(['systemctl','status','dark-xray.service','--no-pager','-l'])
        return False

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source',type=Path,help='Already-cloned candidate source tree (online bootstrap uses this)')
    p.add_argument('--ref',default=os.environ.get('DARK_UPDATE_REF','main'),help='Git branch/tag/commit when --source is omitted')
    p.add_argument('--non-interactive',action='store_true')
    a=p.parse_args()
    if os.geteuid()!=0:raise SystemExit('Root is required for update')
    if not APP.is_dir() or not (APP/'.venv/bin/python').exists():raise SystemExit('DARK XRAY application path is incomplete; use installer repair mode')
    if not (CONF/'config.json').is_file() or not DATA.exists():raise SystemExit('Configuration/data paths are incomplete; use installer repair mode')
    temp=None;src=a.source.resolve() if a.source else None
    if src is None:
        temp=Path(tempfile.mkdtemp(prefix='dark-xray-update.'));src=temp/'src'
        # Clone then resolve the requested branch/tag/commit exactly; no shell interpolation.
        run(['git','clone','--filter=blob:none','--no-checkout',REPO,src],stdout=subprocess.DEVNULL)
        run(['git','-C',src,'fetch','--depth','1','origin',a.ref],stdout=subprocess.DEVNULL)
        run(['git','-C',src,'checkout','--detach','FETCH_HEAD'],stdout=subprocess.DEVNULL)
    validate_source(src)
    candidate=source_commit(src);print('Candidate source:',candidate)
    stamp=time.strftime('%Y%m%d-%H%M%S');snapshot=DATA/'backups'/f'pre-update-source-{stamp}.tar.gz'
    source_snapshot(snapshot);print('Rollback source snapshot:',snapshot)
    guard_was_active=quiet(['systemctl','is-active','--quiet','dark-xray-guard.service']).returncode==0
    # Dependency installation occurs before stopping DARK so source/preflight failures are non-disruptive.
    run([APP/'.venv/bin/python','-m','pip','install','-q','--disable-pip-version-check','-r',src/'requirements.txt'])
    quiet(['systemctl','stop','dark-xray.service'])
    try:
        copy_source(src);install_runtime_files();activate()
        if guard_was_active:run(['systemctl','restart','dark-xray-guard.service'])
    except Exception:
        ok=rollback(snapshot,guard_was_active)
        if not ok:raise SystemExit('Update failed and automatic rollback also failed; inspect systemd status and the rollback snapshot')
        raise SystemExit('Update failed; previous source was restored successfully')
    finally:
        if temp:shutil.rmtree(temp,ignore_errors=True)
    print('DARK XRAY updated successfully.')
    print('Installed source:',candidate)
    print('Run: darkxray')
if __name__=='__main__':main()
