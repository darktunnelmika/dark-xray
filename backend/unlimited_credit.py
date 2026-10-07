"""Consumable credit: one unit is one IP-user for 30 days.
All writes require Store.transaction(); old free balances are preserved.
Deletion, disabling and expiration never refund purchased service.
"""
from __future__ import annotations
import copy
import json
import time
from typing import Any
from dark_policy import PolicyError, integer

DAY = 86400
MONTH = 30 * DAY
MAX_DAYS = 3650


def units_for_days(ip_limit: int, duration_days: int) -> int:
    integer(ip_limit, 1, 1000)
    integer(duration_days, 1, MAX_DAYS)
    return (ip_limit * duration_days + 29) // 30


def _empty() -> dict[str, Any]:
    return {'segments': [], 'seconds': 0, 'units': 0, 'pending_seconds': 0,
            'pending_ips': 0, 'legacy_untimed': False, 'legacy_ips': 0}


def _cover(segments: list, start: int, end: int, ips: int) -> tuple[list, int]:
    """Buy only uncovered capacity; preserve different IP tiers over time."""
    segments = [[max(start, int(a)), int(b), int(n)] for a, b, n in segments
                if int(b) > start and int(n) > 0]
    points = sorted({start, end, *(x for a, b, _ in segments for x in (a, b))})
    merged: list[list[int]] = []
    added = 0
    for a, b in zip(points, points[1:]):
        if b <= a:
            continue
        old = max((n for l, r, n in segments if l <= a and b <= r), default=0)
        requested = ips if start <= a and b <= end else 0
        new = max(old, requested)
        added += max(0, requested - old) * (b - a)
        if new:
            if merged and merged[-1][1] == a and merged[-1][2] == new:
                merged[-1][1] = b
            else:
                merged.append([a, b, new])
    if len(merged) > 4096:
        raise PolicyError('Too many unlimited entitlement changes; contact the Owner')
    return merged, added


def plan_change(state: dict | None, *, quota_bytes: int, ip_limit: int,
                expires_at: int, now: int, duration_days: int | None = None,
                creation: bool = False) -> tuple[dict, int]:
    """Pure quote; cumulative rounding prevents free repeated tiny upgrades."""
    integer(quota_bytes); integer(ip_limit, 0, 1000); integer(expires_at)
    out = copy.deepcopy(state) if state is not None else _empty()
    old_units = int(out['units'])
    if quota_bytes > 0:
        out.update(segments=[], pending_seconds=0, pending_ips=0, legacy_untimed=False)
        return out, 0
    if not ip_limit:
        raise PolicyError('Unlimited-traffic service requires a finite IP-user count; unlimited IP is only available for volumetric plans')
    if duration_days is not None:
        integer(duration_days, 1, MAX_DAYS)
    if creation and expires_at == 0:
        if duration_days is None:
            raise PolicyError('Unlimited service requires a finite duration')
        seconds = duration_days * DAY
        out.update(pending_seconds=seconds, pending_ips=ip_limit,
                   seconds=seconds * ip_limit, units=units_for_days(ip_limit, duration_days))
        return out, out['units']
    if expires_at == 0:
        pending = int(out.get('pending_seconds') or 0)
        if not pending:
            raise PolicyError('Unlimited time is not supported by user-month credit')
        added = max(0, ip_limit - int(out['pending_ips'])) * pending
        out['pending_ips'] = max(ip_limit, int(out['pending_ips']))
    else:
        if creation and expires_at <= now:
            raise PolicyError('Unlimited service expiration must be in the future')
        if expires_at - now > MAX_DAYS * DAY + DAY:
            raise PolicyError('Unlimited duration exceeds the supported range')
        if out.get('legacy_untimed'):
            out['segments'] = [[now, max(now, expires_at), int(out['legacy_ips']) or 1000]]
            out['legacy_untimed'] = False
        pending = int(out.get('pending_seconds') or 0)
        if pending:
            out['segments'] = [[now, now + pending, int(out['pending_ips'])]]
            out['pending_seconds'] = 0
            out['pending_ips'] = 0
        out['segments'], added = _cover(out['segments'], now, max(now, expires_at), ip_limit)
    out['seconds'] = integer(int(out['seconds']) + added)
    out['units'] = (out['seconds'] + MONTH - 1) // MONTH
    return out, max(0, out['units'] - old_units)


def migrate(store) -> None:
    """Atomic v3 -> v4 migration preserves free balance, not obsolete slots."""
    with store.transaction() as db:
        columns = {r[1] for r in db.execute('PRAGMA table_info(owners)')}
        fresh = 'unlimited_spent' not in columns
        if fresh:
            db.execute('ALTER TABLE owners ADD COLUMN unlimited_spent INTEGER NOT NULL DEFAULT 0')
        db.execute('''CREATE TABLE IF NOT EXISTS unlimited_entitlements(
          client_id TEXT PRIMARY KEY, owner TEXT NOT NULL, state_json TEXT NOT NULL,
          revision INTEGER NOT NULL DEFAULT 0, updated_at REAL NOT NULL)''')
        if fresh:
            now = int(time.time())
            has_orders = bool(db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='commerce_orders'").fetchone())
            rows = db.execute('''SELECT c.* FROM clients c JOIN api_admins a ON a.id=c.owner
                                 WHERE c.quota_bytes=0 AND a.role='reseller' ''').fetchall()
            counts: dict[str, int] = {}
            for row in rows:
                state = _empty()
                expiry = int(row['expires_at'])
                ips = int(row['limit_ip']) or 1000
                pending = None
                if has_orders:
                    pending = db.execute('''SELECT duration_days FROM commerce_orders
                      WHERE client_id=? AND status='provisioned_waiting_activation'
                      ORDER BY created_at DESC LIMIT 1''', (row['id'],)).fetchone()
                if pending and int(pending[0]) > 0:
                    state.update(pending_seconds=int(pending[0]) * DAY, pending_ips=ips)
                elif expiry:
                    state['segments'] = [[now, expiry, ips]] if expiry > now else []
                else:
                    state.update(legacy_untimed=True, legacy_ips=ips)
                db.execute('INSERT INTO unlimited_entitlements VALUES(?,?,?,0,?)',
                           (row['id'], row['owner'], json.dumps(state), now))
                counts[row['owner']] = counts.get(row['owner'], 0) + 1
            for owner, count in counts.items():
                credit = int(db.execute('SELECT unlimited_credit FROM owners WHERE id=?', (owner,)).fetchone()[0])
                opening = min(count, credit)
                db.execute('UPDATE owners SET unlimited_spent=? WHERE id=?', (opening, owner))
                db.execute('''INSERT INTO resource_credit_ledger
                  (event_id,owner,volume_bytes,unlimited_units,kind,reference,at)
                  VALUES(?,?,0,?,'unlimited_legacy_opening','v3-free-balance-preserved',?)''',
                  ('unlimited-v4-opening:' + owner, owner, -opening, now))
        db.execute('PRAGMA user_version=4')


def change(store, db, *, client_id: str, owner: str, quota_bytes: int,
           ip_limit: int, expires_at: int, duration_days: int | None = None,
           creation: bool = False, preview: bool = False) -> dict:
    account = db.execute('SELECT unlimited_credit,unlimited_spent FROM owners WHERE id=?', (owner,)).fetchone()
    if not account:
        raise PolicyError('Owner is not registered')
    remaining = max(0, int(account['unlimited_credit']) - int(account['unlimited_spent']))
    if not store._resource_credit_enforced(db, owner):
        return {'units': 0, 'remaining': None, 'after': None, 'enforced': False, 'affordable': True}
    row = db.execute('SELECT * FROM unlimited_entitlements WHERE client_id=?', (client_id,)).fetchone() if client_id else None
    if row and row['owner'] != owner:
        raise PolicyError('Unlimited entitlement owner mismatch')
    if creation and row:
        raise PolicyError('Client identity has a historical entitlement; choose a new identity')
    state = json.loads(row['state_json']) if row else None
    now = int(time.time())
    new, debit = plan_change(state, quota_bytes=quota_bytes, ip_limit=ip_limit,
                             expires_at=expires_at, now=now, duration_days=duration_days,
                             creation=creation or row is None)
    quote = {'units': debit, 'remaining': remaining, 'after': remaining - debit,
             'enforced': True, 'affordable': debit <= remaining, 'unit': 'user_month',
             'month_days': 30, 'rounding': 'cumulative_ceiling', 'ip_limit': ip_limit}
    if preview:
        return quote
    if debit > remaining:
        raise PolicyError(f'Insufficient representative unlimited credit: need {debit} user-month units, available {remaining}')
    if state == new:
        return quote
    revision = int(row['revision']) + 1 if row else 1
    if debit:
        updated = db.execute('''UPDATE owners SET unlimited_spent=unlimited_spent+?
          WHERE id=? AND unlimited_credit-unlimited_spent>=?''', (debit, owner, debit))
        if updated.rowcount != 1:
            raise PolicyError('Insufficient representative unlimited credit')
        db.execute('''INSERT INTO resource_credit_ledger
          (event_id,owner,volume_bytes,unlimited_units,kind,reference,at)
          VALUES(?,?,0,?,?,?,?)''',
          (f'unlimited:{client_id}:{revision}', owner, -debit,
           'unlimited_create' if creation else 'unlimited_change', client_id, time.time()))
    db.execute('''INSERT INTO unlimited_entitlements(client_id,owner,state_json,revision,updated_at)
      VALUES(?,?,?,?,?) ON CONFLICT(client_id) DO UPDATE SET state_json=excluded.state_json,
      revision=excluded.revision,updated_at=excluded.updated_at''',
      (client_id, owner, json.dumps(new, separators=(',', ':')), revision, time.time()))
    return quote
