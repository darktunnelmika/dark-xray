#!/usr/bin/env python3
"""Safe DARK XRAY source updater. Preserves /etc and /var/lib state."""
from __future__ import annotations
import argparse, os, shutil, subprocess, sys, tarfile, tempfile, time
from pathlib import Path

APP=Path('/opt/dark-xray'); CONF=Path('/etc/dark-xray'); DATA=Path('/var/lib/dark-xray')
REPO='https://github.com/darktunnelmika/dark-xray.git'

def run(args, **kw):
    return subprocess.run([str(x) for x in args], check=True, **kw)

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

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source',type=Path,help='Already-cloned source tree (used by online bootstrap)')
    p.add_argument('--non-interactive',action='store_true')
    a=p.parse_args()
    if os.geteuid()!=0: raise SystemExit('Root is required for update')
    if not APP.is_dir() or not (APP/'.venv/bin/python').exists(): raise SystemExit('DARK XRAY application path is incomplete; use installer repair mode')
    if not (CONF/'config.json').is_file() or not DATA.exists(): raise SystemExit('Configuration/data paths are incomplete; use installer repair mode')
    temp=None
    src=a.source.resolve() if a.source else None
    if src is None:
        temp=Path(tempfile.mkdtemp(prefix='dark-xray-update.'))
        src=temp/'src'; run(['git','clone','--depth','1','--branch','main',REPO,src],stdout=subprocess.DEVNULL)
    if not (src/'backend/server.py').is_file() or not (src/'tools/repo-check.py').is_file(): raise SystemExit('Invalid update source')
    run([sys.executable,src/'tools/repo-check.py'],stdout=subprocess.DEVNULL)
    stamp=time.strftime('%Y%m%d-%H%M%S')
    backup=DATA/'backups'/f'pre-update-source-{stamp}.tar.gz'; backup.parent.mkdir(parents=True,exist_ok=True)
    with tarfile.open(backup,'w:gz') as tf:
        for name in ('backend','web','tools','deploy','darkxray','requirements.txt','VERSION'):
            path=APP/name
            if path.exists(): tf.add(path,arcname=name)
    print('Rollback source snapshot:',backup)
    subprocess.run(['systemctl','daemon-reload'],check=False,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    guard_was_active=subprocess.run(['systemctl','is-active','--quiet','dark-xray-guard.service'],check=False).returncode==0
    subprocess.run(['systemctl','stop','dark-xray.service'],check=False,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    try:
        for name in ('backend','web','tools','deploy'):
            dst=APP/name
            if dst.exists(): shutil.rmtree(dst)
            shutil.copytree(src/name,dst,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
        for name in ('darkxray','requirements.txt','LICENSE','THIRD-PARTY-NOTICES.md','VERSION'):
            if (src/name).exists(): shutil.copy2(src/name,APP/name)
        os.chmod(APP/'darkxray',0o755)
        run([APP/'.venv/bin/python','-m','pip','install','-q','--disable-pip-version-check','-r',APP/'requirements.txt'])
        for unit in ('dark-xray.service','dark-xray-guard.service'):
            shutil.copy2(APP/'deploy'/unit,Path('/etc/systemd/system')/unit)
        write_wrapper()
        run(['systemctl','daemon-reload'])
        run(['systemctl','enable','dark-xray.service'])
        run(['systemctl','restart','dark-xray.service'])
        run(['systemctl','is-active','--quiet','dark-xray.service'])
        if guard_was_active:
            run(['systemctl','restart','dark-xray-guard.service'])
    except Exception:
        subprocess.run(['systemctl','start','dark-xray.service'],check=False)
        raise
    finally:
        if temp: shutil.rmtree(temp,ignore_errors=True)
    print('DARK XRAY updated successfully.')
    print('Run: darkxray')
if __name__=='__main__': main()
