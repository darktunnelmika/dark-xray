"""Opt-in namespace bootstrap. Never configure addresses in the host namespace."""
from __future__ import annotations

import os
import re
import socket
import subprocess
import sys
from pathlib import Path

SOURCES = ('11.250.0.1', '11.250.0.2')
ROOT = Path(__file__).resolve().parents[1]


def require_isolated(parent: str, current: str, interfaces: set[str]) -> None:
    if not re.fullmatch(r'net:\[\d+\]', parent or ''):
        raise RuntimeError('Explicit parent network namespace identity is required')
    if not re.fullmatch(r'net:\[\d+\]', current or '') or parent == current:
        raise RuntimeError('Refusing the parent/host network namespace')
    if interfaces != {'lo'}:
        raise RuntimeError('This lab requires an isolated loopback-only namespace')


def check_namespace() -> None:
    require_isolated(os.environ.get('DARK_SECURITY_PARENT_NETNS', ''),
                     os.readlink('/proc/self/ns/net'), {name for _, name in socket.if_nameindex()})


def main() -> int:
    check_namespace()  # Before ANY address or link command.
    if os.geteuid() != 0:
        raise RuntimeError('Namespace bootstrap requires root inside its isolated namespace')
    subprocess.run(['ip', 'link', 'set', 'lo', 'up'], check=True, timeout=5)
    for address in SOURCES:
        subprocess.run(['ip', 'address', 'add', address+'/32', 'dev', 'lo'], check=True, timeout=5)
    # Never add a default route, veth, external interface, NAT or nft rule.
    return subprocess.run([sys.executable, '-m', 'pytest',
        'tests/test_node_real_security.py', '-q',
        '--junitxml=qa/junit/node-real-security.xml'], cwd=ROOT, timeout=600).returncode


if __name__ == '__main__':
    raise SystemExit(main())
