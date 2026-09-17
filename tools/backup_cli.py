#!/usr/bin/env python3
"""Interactive encrypted DARK XRAY backup/restore command line.

Passphrases are read from the controlling terminal and are never accepted in argv.
Restore always targets a new isolated directory; the live installation is never
overwritten by this tool.
"""
from __future__ import annotations

import argparse
import getpass
import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))
from backup import create_backup,restore_backup
from dark_policy import PolicyError


def secret(prompt:str)->str:
    if not sys.stdin.isatty():raise SystemExit('Interactive TTY is required for backup passphrase entry')
    try:return getpass.getpass(prompt)
    except (EOFError,KeyboardInterrupt):raise SystemExit('\nPassphrase entry cancelled')


def new_secret()->str:
    while True:
        first=secret('Backup passphrase (12+ characters): ')
        second=secret('Repeat backup passphrase: ')
        if first!=second:
            print('Passphrases do not match. Try again.',file=sys.stderr);continue
        if len(first)<12:
            print('Backup passphrase must contain at least 12 characters.',file=sys.stderr);continue
        return first


def emit(value:dict):print(json.dumps(value,ensure_ascii=False,indent=2))


def main()->int:
    p=argparse.ArgumentParser(description=__doc__)
    sub=p.add_subparsers(dest='action',required=True)
    b=sub.add_parser('backup');b.add_argument('--config',type=Path,required=True);b.add_argument('--data',type=Path,required=True);b.add_argument('--output',type=Path,required=True)
    r=sub.add_parser('restore');r.add_argument('--archive',type=Path,required=True);r.add_argument('--destination',type=Path,required=True)
    v=sub.add_parser('verify');v.add_argument('--archive',type=Path,required=True)
    a=p.parse_args()
    try:
        if a.action=='backup':
            password=new_secret()
            result=create_backup(a.data,a.config,a.output,password)
            emit({'ok':True,'action':'backup','output':str(a.output.absolute()),'manifest':result});return 0
        if a.action=='restore':
            password=secret('Backup passphrase: ')
            result=restore_backup(a.archive,a.destination,password)
            emit({'ok':True,'action':'restore',**result});return 0
        password=secret('Backup passphrase: ')
        with tempfile.TemporaryDirectory(prefix='dark-xray-backup-verify.') as temp:
            dest=Path(temp)/'restore'
            result=restore_backup(a.archive,dest,password)
            # The temporary rehearsal must disappear even if future restore code
            # creates extra regular files under the isolated destination.
            shutil.rmtree(dest,ignore_errors=True)
        emit({'ok':True,'action':'verify','archive':str(a.archive.absolute()),'backup_version':result.get('backup_version','unknown'),'excluded':result.get('excluded',[])})
        return 0
    except PolicyError as ex:
        print('Backup operation refused: '+str(ex),file=sys.stderr);return 2


if __name__=='__main__':raise SystemExit(main())
