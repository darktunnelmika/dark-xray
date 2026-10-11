#!/usr/bin/env python3
"""Root-owned, non-disruptive HUB runtime log rotation installer.

Never opens a customer database, restarts the panel or changes Xray.
"""
from __future__ import annotations
import os,shutil,subprocess,sys,tempfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
CONF=Path('/etc/logrotate.d/dark-xray-runtime')
UNIT_DIR=Path('/etc/systemd/system')
UNITS=('dark-xray-runtime-rotate.service','dark-xray-runtime-rotate.timer')
POLICY="""/var/lib/dark-xray/runtime/access.log /var/lib/dark-xray/runtime/error.log {
    size 100M
    rotate 5
    compress
    delaycompress
    missingok
    notifempty
    copytruncate
}
"""


def atomic_text(path:Path,body:bytes):
    if path.is_symlink():raise RuntimeError('Unsafe rotation destination symlink: '+str(path))
    if not path.parent.is_dir():raise RuntimeError('Rotation destination parent missing')
    fd,name=tempfile.mkstemp(prefix='.'+path.name+'.',dir=path.parent)
    tmp=Path(name)
    try:
        with os.fdopen(fd,'wb') as f:
            os.fchmod(f.fileno(),0o644)
            f.write(body)
            f.flush();os.fsync(f.fileno())
        os.replace(tmp,path)
    finally:
        tmp.unlink(missing_ok=True)


def install(source:Path=ROOT,conf:Path=CONF,unit_dir:Path=UNIT_DIR,*,runner=subprocess.run,logrotate_bin:str|None=None)->bool:
    if os.geteuid()!=0:raise RuntimeError('Installing HUB rotation requires root')
    executable=logrotate_bin if logrotate_bin is not None else shutil.which('logrotate')
    if not executable:
        print('HUB log rotation requires logrotate; no panel service was modified',file=sys.stderr)
        return False
    source_units=[source/'deploy'/name for name in UNITS]
    for item in source_units:
        if not item.is_file() or item.is_symlink():raise RuntimeError('Invalid rotation source: '+str(item))
    for target in (conf,*(unit_dir/name for name in UNITS)):
        if target.is_symlink():raise RuntimeError('Unsafe rotation target: '+str(target))
    atomic_text(conf,POLICY.encode('utf-8'))
    for src,name in zip(source_units,UNITS):
        atomic_text(unit_dir/name,src.read_bytes())
    runner(['systemctl','daemon-reload'],check=True)
    runner(['systemctl','enable','--now','dark-xray-runtime-rotate.timer'],check=True)
    return True


if __name__=='__main__':
    raise SystemExit(0 if install() else 0)
