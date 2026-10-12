"""Cheap, read-only host socket telemetry for high-connection-count dashboards.

Linux sockstat exposes O(1) aggregate TCP/UDP in-use counts. A psutil
process-wide enumeration is used only when kernel counters are unavailable.
These are *host socket counts*, not Xray active-client counts.
"""
from __future__ import annotations

from pathlib import Path
import socket

import psutil


def host_socket_counts(paths=None) -> dict:
    if paths is None:
        paths = (Path('/proc/net/sockstat'), Path('/proc/net/sockstat6'))
    counts = {'tcp': 0, 'udp': 0}
    found = False
    try:
        for path in paths:
            try:
                # Kernel sockstat is tiny even for hosts with many connections.
                lines = Path(path).read_text(encoding='ascii').splitlines()
            except FileNotFoundError:
                continue
            for line in lines:
                items = line.split()
                if not items or items[0] not in ('TCP:', 'TCP6:', 'UDP:', 'UDP6:'):
                    continue
                fields = dict(zip(items[1::2], items[2::2]))
                value = int(fields['inuse'])
                if value < 0:
                    raise ValueError('Negative kernel socket counter')
                key = 'tcp' if items[0].startswith('TCP') else 'udp'
                counts[key] += value
                found = True
        if found:
            return {'open': counts['tcp'] + counts['udp'],
                    'tcp': counts['tcp'], 'udp': counts['udp'],
                    'available': True, 'source': 'linux-sockstat'}
    except (OSError, ValueError, KeyError):
        pass

    try:
        tcp = udp = 0
        for row in psutil.net_connections(kind='inet'):
            if row.type == socket.SOCK_STREAM:
                tcp += 1
            elif row.type == socket.SOCK_DGRAM:
                udp += 1
        return {'open': tcp + udp, 'tcp': tcp, 'udp': udp,
                'available': True, 'source': 'psutil-enumeration'}
    except (psutil.AccessDenied, psutil.Error, OSError):
        return {'open': 0, 'tcp': 0, 'udp': 0,
                'available': False, 'source': 'unavailable'}
