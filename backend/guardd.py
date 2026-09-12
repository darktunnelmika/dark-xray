#!/usr/bin/env python3
"""DARK IP Guard v0.6: optional Linux root-owned nftables broker.

Only this project's fixed inet table is touched. Root must approve a finite data
port allowlist and verify that Xray sees the actual packet source at this host.
The unprivileged panel can never add protected management ports to that list.
Starting/restarting this broker releases previous DARK bans; it never flushes the
host's ruleset. Native nft element timeouts release bans even if the broker dies.
"""
from __future__ import annotations
import argparse
from dataclasses import dataclass
import ipaddress
import json
import os
from pathlib import Path
import socket
import socketserver
import stat
import struct
import subprocess
import threading
import time
import uuid

from dark_policy import PolicyError, integer, normalize_ip, normalize_ports
from guard_bridge import MAX_MESSAGE

TABLE = 'dark_xray_ip'
MARKER = 'DARK XRAY IP Guard v1'

@dataclass(frozen=True)
class BrokerConfig:
    allowed_uid: int
    allowed_ports: tuple[int, ...]
    protected_ports: tuple[int, ...]
    socket_path: str = '/run/dark-xray-guard/control.sock'
    direct_source_verified: bool = False
    max_ban_seconds: int = 3600
    exempt_ips: tuple[str, ...] = ()
    nft_binary: str = '/usr/sbin/nft'

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, dict):
            raise PolicyError('Guard config must be an object')
        allowed_keys = {'allowed_uid', 'allowed_ports', 'protected_ports', 'socket_path',
                        'direct_source_verified', 'max_ban_seconds', 'exempt_ips', 'nft_binary'}
        if set(value) - allowed_keys:
            raise PolicyError('Unknown guard configuration field')
        uid = integer(value.get('allowed_uid'), 1, 2**31-1)
        ports = normalize_ports(value.get('allowed_ports', []))
        protected = normalize_ports(value.get('protected_ports', [22, 2087, 10085]))
        if not ports or 22 not in protected or set(ports) & set(protected):
            raise PolicyError('Empty data ports or protected-port overlap; include actual SSH, panel and core API ports')
        verified = value.get('direct_source_verified', False)
        if type(verified) is not bool:
            raise PolicyError('direct_source_verified must be boolean')
        sock = value.get('socket_path', '/run/dark-xray-guard/control.sock')
        if sock != '/run/dark-xray-guard/control.sock':
            raise PolicyError('Only the fixed production Unix socket is supported')
        nft = value.get('nft_binary', '/usr/sbin/nft')
        if nft not in ('/usr/sbin/nft', '/sbin/nft', '/usr/bin/nft'):
            raise PolicyError('Unexpected nft executable path')
        exempt = value.get('exempt_ips', [])
        if not isinstance(exempt, list) or len(exempt) > 512:
            raise PolicyError('Invalid exemptions')
        try:
            exempt = tuple(str(ipaddress.ip_network(x, strict=False)) for x in exempt)
        except ValueError as exc:
            raise PolicyError('Invalid exempt CIDR') from exc
        return cls(uid, ports, protected, sock, verified,
                   integer(value.get('max_ban_seconds', 3600), 10, 86400), exempt, nft)

    @classmethod
    def load(cls, path: Path):
        if path.is_symlink() or not path.is_file():
            raise PolicyError('Guard config must be a regular file')
        s = path.stat()
        if s.st_uid != 0 or stat.S_IMODE(s.st_mode) & 0o077:
            raise PolicyError('Guard config must be root-owned and mode 0600')
        if s.st_size > MAX_MESSAGE:
            raise PolicyError('Guard config too large')
        return cls.from_dict(json.loads(path.read_text()))

class NftFirewall:
    """Numeric/IP-only input, fixed table, no shell, one serialized writer."""
    def __init__(self, config: BrokerConfig, runner=subprocess.run):
        self.config, self.runner = config, runner
        self.lock = threading.RLock()
        self.leases: dict[tuple[str, int], float] = {}
        self.ready = False
        self.boot_id = uuid.uuid4().hex

    def _run(self, *args, script=None, check=True):
        result = self.runner([self.config.nft_binary, *args], input=script,
                             text=True, capture_output=True, timeout=5, check=False)
        if check and result.returncode:
            raise PolicyError('nftables refused operation: ' + (result.stderr or result.stdout)[-500:])
        return result

    def bootstrap(self):
        with self.lock:
            old = self._run('-j', 'list', 'tables')
            tables = json.loads(old.stdout).get('nftables', [])
            exists = any(r.get('table', {}).get('family') == 'inet' and
                         r['table'].get('name') == TABLE for r in tables)
            prefix = ''
            if exists:
                doc = json.loads(self._run('-j', 'list', 'table', 'inet', TABLE).stdout)
                owned = any(r.get('table', {}).get('comment') == MARKER for r in doc.get('nftables', []))
                if not owned:
                    raise PolicyError('A foreign table has the DARK name; refusing to replace it')
                prefix = f'delete table inet {TABLE}\n'
            script = prefix + self.rules()
            self._run('-c', '-f', '-', script=script)
            self._run('-f', '-', script=script)
            self.ready = True
            self.leases.clear()

    def rules(self):
        return f'''table inet {TABLE} {{
 comment "{MARKER}"
 set sources4 {{ type ipv4_addr . inet_service; flags timeout; timeout 1h; size 100000; }}
 set sources6 {{ type ipv6_addr . inet_service; flags timeout; timeout 1h; size 100000; }}
 chain input {{
  type filter hook input priority -10; policy accept;
  meta l4proto {{ tcp, udp }} ip saddr . th dport @sources4 counter drop
  meta l4proto {{ tcp, udp }} ip6 saddr . th dport @sources6 counter drop
 }}
}}
'''

    def _ip(self, raw):
        ip = normalize_ip(raw)
        addr = ipaddress.ip_address(ip)
        if (not addr.is_global or addr.is_multicast or
                any(addr.version == n.version and addr in n
                    for n in map(ipaddress.ip_network, self.config.exempt_ips))):
            raise PolicyError('Special, private, reserved or exempt source; ban refused')
        return ip

    def status(self):
        if not self.ready:
            raise PolicyError('Firewall broker not initialized')
        # Detect a deleted table, not merely whether a previous command succeeded.
        self._run('list', 'table', 'inet', TABLE)
        now = time.time()
        self.leases = {k: v for k, v in self.leases.items() if v > now}
        return {'ok': True, 'ready': True, 'backend': 'nftables', 'boot_id': self.boot_id,
                'allowed_ports': list(self.config.allowed_ports),
                'protected_ports': list(self.config.protected_ports),
                'direct_source_verified': self.config.direct_source_verified,
                'max_ban_seconds': self.config.max_ban_seconds,
                'active_address_port_leases': len(self.leases), 'packet_block_verified': False}

    def dispatch(self, message, uid):
        if uid != self.config.allowed_uid:
            raise PolicyError('Unix peer UID is not authorized')
        if not isinstance(message, dict):
            raise PolicyError('Message must be an object')
        op = message.get('operation')
        fields = {'status': {'operation'}, 'clear': {'operation'},
                  'unban': {'operation', 'ip'}, 'ban': {'operation', 'ip', 'ports', 'seconds'}}
        if op not in fields or set(message) != fields[op]:
            raise PolicyError('Unknown operation or fields')
        with self.lock:
            if op == 'status':
                return self.status()
            if not self.ready:
                raise PolicyError('Firewall broker not initialized')
            if op == 'clear':
                self._run('-f', '-', script=f'flush set inet {TABLE} sources4\nflush set inet {TABLE} sources6\n')
                self.leases.clear()
                return {'ok': True, 'cleared': True}
            ip = self._ip(message.get('ip'))
            setname = 'sources6' if ':' in ip else 'sources4'
            if op == 'unban':
                released = 0
                for port in self.config.allowed_ports:
                    # Expired elements need no deletion. A failed read is surfaced
                    # by the table liveness check before any completion claim.
                    got = self._run('get', 'element', 'inet', TABLE, setname,
                                    '{', ip, '.', str(port), '}', check=False)
                    if got.returncode == 0:
                        self._run('-f', '-', script=f'delete element inet {TABLE} {setname} {{ {ip} . {port} }}\n')
                        released += 1
                    self.leases.pop((ip, port), None)
                self.status()
                return {'ok': True, 'released_elements': released}
            if not self.config.direct_source_verified:
                raise PolicyError('Root has not approved direct packet-source enforcement')
            ports = normalize_ports(message['ports'])
            if not ports or not set(ports) <= set(self.config.allowed_ports) or set(ports) & set(self.config.protected_ports):
                raise PolicyError('Unapproved or management port')
            seconds = integer(message['seconds'], 10, self.config.max_ban_seconds)
            now = time.time()
            self.leases = {k: v for k, v in self.leases.items() if v > now}
            if len(self.leases) + len(ports) > 100000:
                raise PolicyError('Lease capacity reached')
            remaining = [p for p in ports if self.leases.get((ip, p), 0) <= now]
            if remaining:
                values = ', '.join(f'{ip} . {p} timeout {seconds}s' for p in remaining)
                self._run('-f', '-', script=f'add element inet {TABLE} {setname} {{ {values} }}\n')
                for port in remaining:
                    self.leases[ip, port] = now + seconds
            return {'ok': True, 'command_accepted': True, 'packet_block_verified': False}

class BrokerServer(socketserver.UnixStreamServer):
    allow_reuse_address = False
    def __init__(self, path, firewall):
        self.firewall = firewall
        super().__init__(path, BrokerHandler)

class BrokerHandler(socketserver.StreamRequestHandler):
    def handle(self):
        self.connection.settimeout(3)
        try:
            raw = self.connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize('3i'))
            _, uid, _ = struct.unpack('3i', raw)
            data = self.rfile.readline(MAX_MESSAGE + 1)
            if len(data) > MAX_MESSAGE or not data.endswith(b'\n'):
                raise PolicyError('Incomplete or oversized message')
            result = self.server.firewall.dispatch(json.loads(data), uid)
        except (PolicyError, ValueError, OSError, subprocess.SubprocessError) as exc:
            result = {'ok': False, 'error': str(exc)[:500]}
        try:
            self.wfile.write(json.dumps(result).encode() + b'\n')
        except OSError:
            pass

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=Path('/etc/dark-xray/guard.json'))
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise SystemExit('The narrow broker, not the panel, needs root/CAP_NET_ADMIN')
    cfg = BrokerConfig.load(args.config)
    path = Path(cfg.socket_path)
    if path.parent.is_symlink() or not path.parent.is_dir():
        raise SystemExit('Use the systemd RuntimeDirectory or create the root-owned socket directory first')
    st = path.parent.stat()
    if st.st_uid != 0 or st.st_mode & 0o022:
        raise SystemExit('Guard socket directory must not be writable by the panel')
    import fcntl
    with (path.parent/'instance.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if path.is_symlink():
            raise SystemExit('Socket symlink refused')
        path.unlink(missing_ok=True)
        firewall = NftFirewall(cfg)
        firewall.bootstrap()
        with BrokerServer(str(path), firewall) as server:
            os.chmod(path, 0o660)
            try:
                server.serve_forever(poll_interval=.5)
            finally:
                path.unlink(missing_ok=True)

if __name__ == '__main__':
    main()
