"""Strict legacy subscription metadata; missing is never zero/unlimited.

Fetch only response headers, preserve TLS hostname verification, pin the validated
public address, and refuse the current HUB as a migration source after cutover.
No subscription URL, token, response body or upstream exception is logged.
"""
from __future__ import annotations
import http.client
import ipaddress
import re
import socket
import ssl
from urllib.parse import urljoin

FIELDS = ('upload', 'download', 'total', 'expire')
MAX_BYTES = (1 << 63) - 1
MAX_EXPIRE = 253402300799


def parse_userinfo(header: str) -> dict:
    values = {}; invalid = False
    for part in str(header or '').split(';'):
        if '=' not in part:
            continue
        key, value = (s.strip() for s in part.split('=', 1)); key = key.lower()
        if key not in FIELDS:
            continue
        if key in values or not re.fullmatch(r'[0-9]{1,20}', value):
            invalid = True; continue
        number = int(value)
        if number > (MAX_EXPIRE if key == 'expire' else MAX_BYTES):
            invalid = True; continue
        values[key] = number
    if values.get('upload', 0) + values.get('download', 0) > MAX_BYTES:
        invalid = True
    missing = [k for k in FIELDS if k not in values]
    status = 'invalid_metadata' if invalid else 'no_usage_data' if not values else 'partial' if missing else 'verified'
    error = 'Invalid or duplicate subscription-userinfo fields' if invalid else ('Missing metadata fields: ' + ', '.join(missing)) if missing else ''
    return {'status': status, 'error': error, 'provided_fields': sorted(values),
            **{k: values.get(k, 0) for k in FIELDS}}


class _PinnedHTTP(http.client.HTTPConnection):
    def __init__(self, host, port, address, **kwargs):
        self.address = address
        super().__init__(host, port, **kwargs)

    def connect(self):
        self.sock = socket.create_connection((self.address, self.port), self.timeout)


class _PinnedHTTPS(http.client.HTTPSConnection):
    def __init__(self, host, port, address, **kwargs):
        self.address = address
        super().__init__(host, port, **kwargs)

    def connect(self):
        raw = socket.create_connection((self.address, self.port), self.timeout)
        try:
            self.sock = self._context.wrap_socket(raw, server_hostname=self.host)
        except BaseException:
            raw.close(); raise


def scan_subscription(restore, url: str) -> dict:
    original = url; seen = set()
    empty = {'upload': 0, 'download': 0, 'total': 0, 'expire': 0, 'provided_fields': []}
    for _ in range(4):
        connection = None
        try:
            parsed, host, path, query = restore._safe_url(url)
            port = parsed.port or (443 if parsed.scheme == 'https' else 80)
            if not 1 <= port <= 65535:
                raise ValueError('port')
            addresses = restore._public_addresses(host)
            own = str(restore.engine.config.public_address).strip('[]').rstrip('.').lower()
            local = set()
            try:
                local.add(str(ipaddress.ip_address(own)))
            except ValueError:
                if own == host:
                    return {**empty, 'status': 'source_is_dark', 'error': 'The source now points to DARK. Do not rescan after DNS cutover; review the saved metadata.'}
            if local.intersection(addresses):
                return {**empty, 'status': 'source_is_dark', 'error': 'The source now points to DARK. Do not rescan after DNS cutover; review the saved metadata.'}
            if url in seen:
                return {**empty, 'status': 'scan_failed', 'error': 'Subscription redirect loop'}
            seen.add(url)
            address = next((a for a in addresses if ':' not in a), addresses[0])
            if parsed.scheme == 'https':
                context = ssl.create_default_context(); context.hostname_checks_common_name = False
                connection = _PinnedHTTPS(host, port, address, timeout=10, context=context)
            else:
                connection = _PinnedHTTP(host, port, address, timeout=10)
            target = path + ('?' + query if query else '')
            connection.request('GET', target, headers={'User-Agent': 'DARK-XRAY-Restore/2.0', 'Accept': '*/*', 'Connection': 'close'})
            response = connection.getresponse()
            if response.status in (301, 302, 303, 307, 308):
                location = response.getheader('Location', '')
                if not location:
                    return {**empty, 'status': 'scan_failed', 'error': 'Subscription redirect has no destination'}
                next_url = urljoin(url, location)
                if parsed.scheme == 'https' and not next_url.lower().startswith('https://'):
                    return {**empty, 'status': 'scan_failed', 'error': 'Refusing an HTTPS to HTTP subscription redirect'}
                url = next_url; continue
            if not 200 <= response.status < 300:
                return {**empty, 'status': 'scan_failed', 'error': 'Source HTTP status ' + str(response.status)}
            return parse_userinfo(response.getheader('subscription-userinfo', ''))
        except Exception as ex:
            # Never include str(ex): network exceptions can contain private URLs.
            return {**empty, 'status': 'scan_failed', 'error': 'Source scan failed (' + type(ex).__name__ + ')'}
        finally:
            if connection is not None:
                connection.close()
    return {**empty, 'status': 'scan_failed', 'error': 'Too many subscription redirects'}
