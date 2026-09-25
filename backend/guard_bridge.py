"""Unprivileged client for the local, narrowly scoped DARK firewall broker.

The broker does not run arbitrary commands from the panel. JSON messages cross a
Unix socket authenticated with Linux peer credentials; no TCP listener exists.
"""
from __future__ import annotations
import json
import socket
from pathlib import Path
from dark_policy import Policy, PolicyError, jail_for, normalize_ip

MAX_MESSAGE = 65536

class BrokerClient:
    def __init__(self, path: str, timeout: float = 3):
        self.path, self.timeout = path, timeout

    def request(self, message: dict) -> dict:
        raw = json.dumps(message, separators=(',', ':')).encode() + b'\n'
        if len(raw) > MAX_MESSAGE:
            raise PolicyError('Guard request too large')
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
                sock.settimeout(self.timeout)
                sock.connect(self.path)
                sock.sendall(raw)
                result = bytearray()
                while b'\n' not in result:
                    chunk = sock.recv(4096)
                    if not chunk:
                        raise PolicyError('Guard closed without a response')
                    result.extend(chunk)
                    if len(result) > MAX_MESSAGE:
                        raise PolicyError('Guard response too large')
                answer = json.loads(result.split(b'\n', 1)[0])
        except (OSError, ValueError) as exc:
            raise PolicyError('IP enforcement broker unavailable: ' + type(exc).__name__) from exc
        if not isinstance(answer, dict):raise PolicyError('Malformed guard response')
        if answer.get('ok') is not True:
            raise PolicyError(str(answer.get('error', 'Guard request rejected'))[:400])
        return answer

    def status(self) -> dict:
        return self.request({'operation': 'status'})

    def release(self, ip: str) -> dict:
        return self.request({'operation': 'unban', 'ip': normalize_ip(ip)})

    def clear(self) -> dict:
        return self.request({'operation': 'clear'})

    def set_ports(self, ports: list[int]) -> dict:
        return self.request({'operation':'set_ports','ports':list(ports)})

class BrokerExecutor:
    """Guard-compatible executor; commands are still not packet-flow proof."""
    confirmation = 'nftables_broker_accepted'

    def __init__(self, policy: Policy, client: BrokerClient):
        policy.assert_enforcement_safe()
        self.policy, self.client = policy, client
        self.jails = {jail_for(c.ports): c.ports for c in policy.clients.values() if c.limit_ip}
        self.status_info: dict = {}

    def check(self):
        status = self.client.status()
        if not status.get('direct_source_verified'):
            raise PolicyError('Root-owned guard configuration has not verified direct packet sources')
        allowed = set(status.get('allowed_ports', []))
        requested = {p for ports in self.jails.values() for p in ports}
        if requested - allowed:
            raise PolicyError('Root approval is missing for data ports: ' + ','.join(map(str, sorted(requested - allowed))))
        if self.policy.ban_seconds > status.get('max_ban_seconds', 0):
            raise PolicyError('Ban duration exceeds the root-owned guard maximum')
        self.status_info = status
        return status

    def ban(self, jail: str, ip: str, seconds: int | None = None):
        if jail not in self.jails:
            raise PolicyError('Unmanaged data-port group')
        duration = self.policy.ban_seconds if seconds is None else int(seconds)
        if duration <= 0:
            raise PolicyError('Invalid ban duration')
        self.client.request({'operation': 'ban', 'ip': normalize_ip(ip),
                             'ports': list(self.jails[jail]), 'seconds': duration})

    def unban(self, jail: str, ip: str):
        if jail not in self.jails:
            raise PolicyError('Unmanaged data-port group')
        self.client.release(normalize_ip(ip))
