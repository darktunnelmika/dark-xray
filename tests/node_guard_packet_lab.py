"""Bootstrap Stage 6 Node Guard packet acceptance in a disposable net namespace."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from node_security_lab import SOURCES, check_namespace

ROOT=Path(__file__).resolve().parents[1]


def main()->int:
    check_namespace()
    parent_mnt=os.environ.get('DARK_GUARD_PARENT_MNTNS','')
    current_mnt=os.readlink('/proc/self/ns/mnt')
    if not re.fullmatch(r'mnt:\[\d+\]',parent_mnt) or parent_mnt==current_mnt:
        raise RuntimeError('Stage 6 packet lab requires an isolated mount namespace')
    if os.geteuid()!=0:
        raise RuntimeError('Stage 6 packet lab requires root inside the isolated namespace')
    for command in ('ip','nft','mount'):
        if not shutil.which(command):
            raise RuntimeError(command+' is required')
    subprocess.run(['mount','--make-rprivate','/'],check=True,timeout=5)
    guard_dir=Path('/run/dark-xray-guard');guard_dir.mkdir(parents=True,exist_ok=True)
    subprocess.run(['mount','-t','tmpfs','-o','mode=0750','tmpfs',str(guard_dir)],check=True,timeout=5)
    subprocess.run(['ip','link','set','lo','up'],check=True,timeout=5)
    for address in SOURCES:
        subprocess.run(['ip','address','add',address+'/32','dev','lo'],check=True,timeout=5)
    # No veth/default route/NAT/external interface is created. The namespace
    # disappears when this bootstrap exits, taking the nftables table with it.
    return subprocess.run([
        sys.executable,'-m','pytest','tests/test_node_real_guard_enforcement.py','-q',
        '--junitxml=qa/junit/node-real-guard-enforcement.xml',
    ],cwd=ROOT,timeout=180).returncode


if __name__=='__main__':
    raise SystemExit(main())
