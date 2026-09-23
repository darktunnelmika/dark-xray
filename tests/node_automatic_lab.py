"""Start the automatic-policy test ONLY in a separately created net namespace."""
import os
import subprocess
import sys
from pathlib import Path
from node_security_lab import check_namespace, SOURCES

ROOT = Path(__file__).resolve().parents[1]


def main():
    check_namespace()  # Must precede EVERY network mutation.
    if os.geteuid() != 0:
        raise RuntimeError('Isolated namespace bootstrap requires root')
    subprocess.run(['ip', 'link', 'set', 'lo', 'up'], check=True, timeout=5)
    for address in SOURCES:
        subprocess.run(['ip', 'address', 'add', address+'/32', 'dev', 'lo'], check=True, timeout=5)
    # No public route, external target, NAT, veth or firewall configuration.
    return subprocess.run([sys.executable, '-m', 'pytest',
        'tests/test_node_automatic_limits.py', '-q',
        '--junitxml=qa/junit/node-automatic-limits.xml'], cwd=ROOT, timeout=900).returncode


if __name__ == '__main__':
    raise SystemExit(main())
