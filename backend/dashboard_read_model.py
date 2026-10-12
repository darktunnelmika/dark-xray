"""Bounded, read-only Dashboard FastPath V2 reporting model.

The immutable traffic ledger remains the accounting authority. This module
reports *current stored client counters* from the much smaller clients table;
it never claims they equal lifetime, period, or online Xray traffic.
No schema changes, background tasks, or mutable connections are introduced.
"""
from __future__ import annotations

import copy
import sqlite3
import threading
import time
from pathlib import Path
from urllib.parse import quote


class DashboardUnavailable(RuntimeError):
    """The fast read model has no trustworthy snapshot right now."""


class DashboardReadModel:
    def __init__(self, database, *, ttl=8.0, budget_seconds=0.35,
                 busy_timeout_seconds=0.12, stale_seconds=90.0):
        self.database = Path(database).absolute()
        self.ttl = float(ttl)
        self.budget_seconds = float(budget_seconds)
        self.busy_timeout_seconds = float(busy_timeout_seconds)
        self.stale_seconds = float(stale_seconds)
        self._lock = threading.Lock()
        self._cache = {}

    @staticmethod
    def _visible(snapshot, age, *, stale=False):
        doc = copy.deepcopy(snapshot)
        doc['freshness'] = 'stale' if stale else 'fresh'
        doc['age_seconds'] = round(max(0.0, age), 2)
        return doc

    def get(self, *, role, account):
        # Never cache or return another principal's private client counts.
        if role not in ('owner', 'reseller') or not isinstance(account, str) or not account:
            raise PermissionError('Dashboard summary is not available for this role')
        key = (role, account)
        now = time.monotonic()
        cached = self._cache.get(key)
        if cached and now - cached[0] < self.ttl:
            return self._visible(cached[1], now - cached[0])

        # Never make an interactive response wait for a different slow reader.
        if not self._lock.acquire(blocking=False):
            if cached and now - cached[0] < self.stale_seconds:
                return self._visible(cached[1], now - cached[0], stale=True)
            raise DashboardUnavailable('Dashboard summary temporarily busy')
        try:
            now = time.monotonic()
            cached = self._cache.get(key)
            if cached and now - cached[0] < self.ttl:
                return self._visible(cached[1], now - cached[0])
            try:
                snapshot = self._read(role, account)
            except (sqlite3.Error, OSError, ValueError, TimeoutError) as exc:
                if cached and now - cached[0] < self.stale_seconds:
                    return self._visible(cached[1], now - cached[0], stale=True)
                # Never put raw SQLite errors or absolute filesystem paths in API errors.
                raise DashboardUnavailable('Dashboard read-only snapshot unavailable') from exc
            self._cache[key] = (time.monotonic(), snapshot)
            return self._visible(snapshot, 0)
        finally:
            self._lock.release()

    def _read(self, role, account):
        if self.database.is_symlink() or not self.database.is_file():
            raise DashboardUnavailable('Dashboard database unavailable')
        # A separate, short-lived read-only connection avoids contention on
        # Store.lock held by the accounting/reconciliation writer.
        uri = 'file:' + quote(str(self.database), safe='/') + '?mode=ro'
        db = sqlite3.connect(uri, uri=True, timeout=self.busy_timeout_seconds)
        start = time.monotonic()
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA query_only=ON')
        db.execute('PRAGMA busy_timeout=' + str(int(self.busy_timeout_seconds * 1000)))
        db.set_progress_handler(
            lambda: int(time.monotonic() - start > self.budget_seconds), 1000)
        now = time.time()
        try:
            query = """SELECT c.owner,c.manual,c.expires_at,c.quota_bytes,c.used_bytes,
                              c.global_ip_block,c.global_device_block,
                              o.manual AS owner_manual,o.account_disabled,
                              m.external_disabled,m.expected_enable
                       FROM clients c
                       LEFT JOIN owners o ON o.id=c.owner
                       LEFT JOIN managed_clients m ON m.email=c.id"""
            params = ()
            if role != 'owner':
                query += ' WHERE c.owner=?'
                params = (account,)

            total = eligible = blocked = 0
            current_bytes = 0
            for row in db.execute(query, params):
                total += 1
                used = int(row['used_bytes'] or 0)
                current_bytes += used  # Python integers preserve exact precision.
                quota = int(row['quota_bytes'] or 0)
                disallowed = (
                    bool(row['manual']) or bool(row['owner_manual']) or
                    row['owner_manual'] is None or bool(row['account_disabled']) or
                    bool(row['global_ip_block']) or bool(row['global_device_block']) or
                    bool(row['external_disabled']) or row['expected_enable'] == 0 or
                    (int(row['expires_at'] or 0) > 0 and int(row['expires_at']) <= now) or
                    (quota > 0 and used >= quota))
                if disallowed:
                    blocked += 1
                else:
                    eligible += 1

            clients = {
                'total': total, 'policy_eligible': eligible, 'policy_blocked': blocked,
                'current_stored_usage_bytes': current_bytes,
                'usage_scope': 'current-stored-counters-not-lifetime-ledger',
                'presence_is_live': False
            }
            inbounds = representatives = nodes = None
            if role == 'owner':
                inbound_rows = db.execute('SELECT body FROM core_inbounds').fetchall()
                import json
                inbounds = {
                    'total': len(inbound_rows),
                    'enabled': sum(1 for x in inbound_rows
                                   if json.loads(x['body']).get('enable', True) is not False)
                }
                reps = db.execute("""
                    SELECT a.role,a.disabled,o.manual,o.account_disabled
                    FROM owner_profiles p
                    LEFT JOIN api_admins a ON a.id=p.id
                    LEFT JOIN owners o ON o.id=p.id
                    WHERE p.id NOT IN (SELECT id FROM api_admins WHERE role='owner')
                """).fetchall()
                representatives = {
                    'total': len(reps),
                    'enabled': sum(1 for x in reps if x['role']=='reseller' and x['manual'] is not None and
                                   not x['manual'] and not x['account_disabled'] and
                                   not x['disabled'])
                }
                heartbeat_rows = db.execute(
                    'SELECT enabled,last_seen FROM remote_nodes').fetchall()
                enabled_nodes = [x for x in heartbeat_rows if x['enabled']]
                nodes = {
                    'total': len(heartbeat_rows), 'enabled': len(enabled_nodes),
                    'recent_heartbeat': sum(
                        1 for x in enabled_nodes
                        if x['last_seen'] and 0 <= now-float(x['last_seen']) <= 45),
                    'status_source': 'cached-heartbeat-only-not-route-readiness'
                }
            return {
                'generated_at': now,
                'source': 'read-only SQLite current-state tables',
                'read_only': True, 'accounting_mutations': False,
                'scope': 'owner' if role == 'owner' else 'own-clients',
                'clients': clients, 'inbounds': inbounds,
                'representatives': representatives, 'nodes': nodes
            }
        finally:
            db.set_progress_handler(None, 0)
            db.close()
