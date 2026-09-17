#!/usr/bin/env python3
"""Conservative server-side REALITY target probes for DARK XRAY.

Only explicit public hosts are contacted. Private, loopback, link-local,
multicast, reserved and unspecified addresses are rejected after DNS
resolution. Search accepts a small list of host[:port] candidates and never
expands CIDR ranges.
"""
from __future__ import annotations

import concurrent.futures
import ipaddress
import socket
import ssl
import time
from dataclasses import dataclass

DEFAULT_REALITY_TARGETS = (
    'www.bing.com:443',
    'www.cloudflare.com:443',
    'www.google.com:443',
    'github.com:443',
    'www.amazon.com:443',
)
# Xray-core v26.3.27 / github.com/xtls/reality has an 8192-byte
# target-handshake record limit. www.microsoft.com has been observed returning
# a Certificate record of 8273 bytes and is therefore fail-closed here.
INCOMPATIBLE_REALITY_HOSTS = {'www.microsoft.com'}
DISCOURAGED_REALITY_HOSTS = {'www.apple.com', 'icloud.com', 'www.icloud.com'}
MAX_TARGETS = 20
DEFAULT_TIMEOUT = 4.0


class RealityScanError(ValueError):
    pass


@dataclass(frozen=True)
class ParsedTarget:
    host: str
    port: int

    @property
    def display(self) -> str:
        return f'[{self.host}]:{self.port}' if ':' in self.host else f'{self.host}:{self.port}'


def parse_target(value: str) -> ParsedTarget:
    value = (value or '').strip()
    if not value or len(value) > 300:
        raise RealityScanError('Target must be a host or host:port')
    if any(ch in value for ch in '/?#@') or any(ch.isspace() for ch in value):
        raise RealityScanError('Target must not contain a URL path, credentials or whitespace')
    if '/' in value:
        raise RealityScanError('CIDR/range scanning is not supported')
    host = value
    port = 443
    if value.startswith('['):
        end = value.find(']')
        if end < 2:
            raise RealityScanError('Invalid IPv6 target')
        host = value[1:end]
        rest = value[end + 1:]
        if rest:
            if not rest.startswith(':') or not rest[1:].isdigit():
                raise RealityScanError('Invalid target port')
            port = int(rest[1:])
    elif value.count(':') == 1:
        maybe_host, maybe_port = value.rsplit(':', 1)
        if maybe_port.isdigit():
            host, port = maybe_host, int(maybe_port)
    elif value.count(':') > 1:
        # Bare IPv6 literal without an explicit port.
        host = value
    host = host.rstrip('.').strip().lower()
    if not host or len(host) > 253 or not 1 <= port <= 65535:
        raise RealityScanError('Invalid REALITY target')
    return ParsedTarget(host, port)


def reality_target_policy(value: str | ParsedTarget) -> dict:
    target = value if isinstance(value, ParsedTarget) else parse_target(value)
    host = target.host.lower().rstrip('.')
    if host in INCOMPATIBLE_REALITY_HOSTS:
        return {
            'compatible': False,
            'severity': 'blocked',
            'advisory': (
                'Known incompatible REALITY target on pinned Xray-core v26.3.27: '
                'the target may return a TLS Certificate record larger than REALITY\'s '
                '8192-byte parser limit. Use www.bing.com:443 or run Target Search.'
            ),
            'suggestedTarget': 'www.bing.com:443',
        }
    if host in DISCOURAGED_REALITY_HOSTS or host.endswith('.icloud.com'):
        return {
            'compatible': True,
            'severity': 'warning',
            'advisory': (
                'This target family is discouraged by current Xray REALITY guidance. '
                'Prefer a neutral TLS 1.3 target returned by Target Search.'
            ),
            'suggestedTarget': 'www.bing.com:443',
        }
    return {'compatible': True, 'severity': 'ok', 'advisory': '', 'suggestedTarget': ''}


def _public_addresses(target: ParsedTarget) -> list[str]:
    try:
        rows = socket.getaddrinfo(target.host, target.port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise RealityScanError('DNS resolution failed') from exc
    out: list[str] = []
    for row in rows:
        addr = row[4][0]
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            continue
        if not ip.is_global:
            continue
        text = str(ip)
        if text not in out:
            out.append(text)
    if not out:
        raise RealityScanError('Target does not resolve to a public IP')
    return out[:6]


def _flatten_name(parts) -> str:
    values = []
    for group in parts or ():
        for key, value in group:
            if key in ('commonName', 'organizationName') and value:
                values.append(value)
    return ' / '.join(values[:3])


def _scan_address(target: ParsedTarget, address: str, timeout: float) -> dict:
    context = ssl.create_default_context()
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.set_alpn_protocols(['h2', 'http/1.1'])
    started = time.monotonic()
    sock = socket.create_connection((address, target.port), timeout=timeout)
    try:
        sock.settimeout(timeout)
        with context.wrap_socket(sock, server_hostname=target.host) as tls:
            latency = max(1, round((time.monotonic() - started) * 1000))
            cert = tls.getpeercert() or {}
            sans = [v for k, v in cert.get('subjectAltName', ()) if k == 'DNS'][:32]
            version = tls.version() or ''
            alpn = tls.selected_alpn_protocol() or ''
            cipher = (tls.cipher() or ('', '', 0))[0]
            tls13 = version == 'TLSv1.3'
            h2 = alpn == 'h2'
            return {
                'target': target.display,
                'host': target.host,
                'port': target.port,
                'ip': address,
                'latencyMs': latency,
                'tlsVersion': version,
                'tls13': tls13,
                'alpn': alpn,
                'h2': h2,
                'cipher': cipher,
                'certValid': True,
                'certSubject': _flatten_name(cert.get('subject')),
                'certIssuer': _flatten_name(cert.get('issuer')),
                'serverNames': sans,
                'ok': True,
                # Python's stdlib TLS API does not expose the negotiated ECDHE group.
                'x25519Verified': None,
                'recommended': tls13 and h2,
            }
    except Exception:
        sock.close()
        raise


def scan_target(value: str, *, timeout: float = DEFAULT_TIMEOUT) -> dict:
    target = parse_target(value)
    addresses = _public_addresses(target)
    errors: list[str] = []
    best: dict | None = None
    for address in addresses:
        try:
            row = _scan_address(target, address, timeout)
            if best is None or row['latencyMs'] < best['latencyMs']:
                best = row
        except (OSError, ssl.SSLError, TimeoutError) as exc:
            errors.append(type(exc).__name__)
    if best:
        best['resolved'] = addresses
        policy = reality_target_policy(target)
        best.update(policy)
        best['recommended'] = bool(best.get('recommended')) and policy['compatible'] and policy['severity'] == 'ok'
        return best
    return {
        'target': target.display,
        'host': target.host,
        'port': target.port,
        'resolved': addresses,
        'latencyMs': 0,
        'tlsVersion': '',
        'tls13': False,
        'alpn': '',
        'h2': False,
        'cipher': '',
        'certValid': False,
        'certSubject': '',
        'certIssuer': '',
        'serverNames': [],
        'ok': False,
        'x25519Verified': None,
        'recommended': False,
        **reality_target_policy(target),
        'error': 'TLS probe failed' + (': ' + ', '.join(errors[:3]) if errors else ''),
    }


def normalize_candidates(values: list[str] | None) -> list[str]:
    source = list(values or DEFAULT_REALITY_TARGETS)
    out: list[str] = []
    for item in source:
        item = (item or '').strip()
        if not item:
            continue
        # Parse now so malformed/private-looking URLs never enter the worker pool.
        parse_target(item)
        if item not in out:
            out.append(item)
        if len(out) >= MAX_TARGETS:
            break
    if not out:
        raise RealityScanError('No target candidates supplied')
    return out


def search_targets(values: list[str] | None, *, timeout: float = DEFAULT_TIMEOUT) -> list[dict]:
    candidates = normalize_candidates(values)
    rows: list[dict] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(5, len(candidates))) as pool:
        future_map = {pool.submit(scan_target, item, timeout=timeout): item for item in candidates}
        for future in concurrent.futures.as_completed(future_map):
            item = future_map[future]
            try:
                rows.append(future.result())
            except RealityScanError as exc:
                rows.append({'target': item, 'ok': False, 'recommended': False, 'latencyMs': 0, 'error': str(exc)})
            except Exception as exc:
                rows.append({'target': item, 'ok': False, 'recommended': False, 'latencyMs': 0, 'error': type(exc).__name__})
    rows.sort(key=lambda r: (not bool(r.get('compatible', True)), not bool(r.get('recommended')), not bool(r.get('ok')), int(r.get('latencyMs') or 10**9), str(r.get('target', ''))))
    return rows
