"""Restore eligibility and explicit source review, isolated from native accounts.

The existing CoreEngine apply path remains the only runtime writer. Two per-engine
adapters reconcile only Restore enable flags before apply and before Node bundles
read clients. Native client policy, credentials, quota and traffic are untouched.
No new worker thread is created. Remote enforcement is acknowledged convergence,
not a promise about a Node that is offline or an unobserved byte-perfect quota.
"""
from __future__ import annotations
import hashlib
import json
import time

from fastapi import Depends, HTTPException
from pydantic import BaseModel, Field, StrictBool, StrictInt
from restore_scan import FIELDS, MAX_BYTES, MAX_EXPIRE
from restore_safety_status import safety_snapshot


class RestoreReviewBody(BaseModel):
    upload: StrictInt = Field(ge=0, le=MAX_BYTES)
    download: StrictInt = Field(ge=0, le=MAX_BYTES)
    total: StrictInt = Field(ge=0, le=MAX_BYTES)
    expire: StrictInt = Field(ge=0, le=MAX_EXPIRE)
    note: str = Field(min_length=5, max_length=240)
    expectedRevision: str = Field(pattern=r'^[0-9a-f]{64}$')
    confirmed: StrictBool


class RestoreSuspensionBody(BaseModel):
    suspended: StrictBool
    expectedRevision: str = Field(pattern=r'^[0-9a-f]{64}$')


def _valid_numbers(row):
    values = [row.get('legacy_' + k) for k in FIELDS]
    return all(type(v) is int and 0 <= v <= (MAX_EXPIRE if k == 'expire' else MAX_BYTES)
               for k, v in zip(FIELDS, values)) and values[0] + values[1] <= MAX_BYTES


def decision(row, metadata, client, used, *, now=None):
    now = time.time() if now is None else now
    if client is None:
        return 'identity_missing'
    if not _valid_numbers(row) or metadata.get('metadata_state') not in ('verified', 'manual', 'legacy_saved'):
        return 'needs_review'
    if not row['enabled'] or metadata.get('external_disabled'):
        return 'suspended'
    if row['legacy_expire'] and row['legacy_expire'] <= now:
        return 'expired'
    if row['legacy_total'] and row['legacy_upload'] + row['legacy_download'] + used >= row['legacy_total']:
        return 'exhausted'
    return 'eligible'


class RestoreSafetyMixin:
    def _init_safety(self):
        with self.store.transaction() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS restore_safety(
                restore_id TEXT PRIMARY KEY,
                metadata_state TEXT NOT NULL,checked_at REAL NOT NULL DEFAULT 0,
                note TEXT NOT NULL DEFAULT '',expected_enable INTEGER NOT NULL DEFAULT 1,
                external_disabled INTEGER NOT NULL DEFAULT 0,
                decision TEXT NOT NULL DEFAULT '',decision_at REAL NOT NULL DEFAULT 0,
                FOREIGN KEY(restore_id) REFERENCES restore_subscriptions(id) ON DELETE CASCADE)''')
            self._seed_safety(db, legacy=True)
        engine = self.engine
        engine._dark_restore_safety = self
        if hasattr(engine, '_dark_restore_original_clients'):
            return
        engine._dark_restore_original_clients = engine.clients
        engine._dark_restore_original_apply = engine.apply
        def clients():
            with engine.lock:
                rows = engine._dark_restore_original_clients()
                engine._dark_restore_safety.reconcile_safety(rows)
                return rows
        def apply(*args, **kwargs):
            with engine.lock:
                rows = engine._dark_restore_original_clients()
                engine._dark_restore_safety.reconcile_safety(rows)
                return engine._dark_restore_original_apply(*args, **kwargs)
        engine.clients = clients
        engine.apply = apply

    def _seed_safety(self, db, *, legacy=False):
        # Pre-existing scans are explicitly legacy_saved: their original header
        # field-presence was not persisted. Never invent a fresh verification.
        state = 'legacy_saved' if legacy else 'verified'
        db.execute('''INSERT OR IGNORE INTO restore_safety
            (restore_id,metadata_state,checked_at,expected_enable,external_disabled)
            SELECT r.id,CASE WHEN r.scan_status='verified' THEN ? ELSE 'review' END,r.created_at,
                CASE WHEN COALESCE(json_extract(c.body,'$.enable'),1) THEN 1 ELSE 0 END,
                CASE WHEN COALESCE(json_extract(c.body,'$.enable'),1) THEN 0 ELSE 1 END
            FROM restore_subscriptions r LEFT JOIN core_clients c ON c.email=r.core_email''', (state,))

    def reconcile_safety(self, records=None):
        if not self.engine.config.writes_enabled:
            return {'changed': 0, 'paused': True}
        record_map = {r['email']: r for r in records or []}
        changed = 0; now = time.time()
        with self.engine.lock, self.store.transaction() as db:
            self._seed_safety(db)
            rows = list(db.execute('''SELECT r.*,s.metadata_state,s.checked_at,s.note,
                s.expected_enable,s.external_disabled,s.decision old_decision,c.body core_body,
                COALESCE(u.used,0) dark_used
                FROM restore_subscriptions r JOIN restore_safety s ON s.restore_id=r.id
                LEFT JOIN core_clients c ON c.email=r.core_email
                LEFT JOIN (SELECT restore_id,SUM(up+down) used FROM restore_usage GROUP BY restore_id) u ON u.restore_id=r.id'''))
            for raw in rows:
                r = dict(raw); client = json.loads(r['core_body']) if r['core_body'] else None
                metadata_state = r['metadata_state']
                if metadata_state == 'review' and r['scan_status'] == 'verified':
                    metadata_state = r['metadata_state'] = 'verified'
                external = int(r['external_disabled'])
                if client is not None and not client.get('enable', True) and r['expected_enable']:
                    external = r['external_disabled'] = 1
                reason = decision(r, r, client, int(r['dark_used']), now=now)
                desired = reason == 'eligible'
                observed = bool(client.get('enable', True)) if client is not None else False
                if client is not None and observed != desired:
                    client['enable'] = desired
                    db.execute('UPDATE core_clients SET body=? WHERE email=?', (json.dumps(client), r['core_email']))
                    changed += 1
                if r['core_email'] in record_map:
                    record_map[r['core_email']]['enable'] = desired
                if (r['old_decision'] != reason or r['expected_enable'] != int(desired)
                        or raw['external_disabled'] != external or raw['metadata_state'] != metadata_state):
                    db.execute('''UPDATE restore_safety SET metadata_state=?,expected_enable=?,external_disabled=?,
                        decision=?,decision_at=? WHERE restore_id=?''',
                        (metadata_state, int(desired), external, reason, now, r['id']))
                    db.execute('INSERT INTO restore_events(restore_id,event,detail,at) VALUES(?,?,?,?)',
                        (r['id'], 'eligibility.changed', json.dumps({'status': reason, 'enabled': desired}), now))
        return {'changed': changed, 'paused': False}

    def _safety_records(self):
        with self.store.lock:
            return {r['restore_id']: dict(r) for r in self.store.db.execute('SELECT * FROM restore_safety')}

    @staticmethod
    def _review_revision(row, meta):
        value = [row['id'], *[row['legacy_' + k] for k in FIELDS], int(row['enabled']),
                 meta.get('metadata_state'), meta.get('note'), meta.get('checked_at'), meta.get('external_disabled')]
        return hashlib.sha256(json.dumps(value, separators=(',', ':')).encode()).hexdigest()

    def rows(self, group_id=None):
        rows = super().rows(group_id); metadata = self._safety_records()
        with self.store.lock:
            clients = {r['email']: json.loads(r['body']) for r in self.store.db.execute('SELECT email,body FROM core_clients')}
        now = time.time()
        for row in rows:
            meta = metadata.get(row['id'], {'metadata_state': 'verified' if row['scan_status'] == 'verified' else 'review'})
            row['service_status'] = decision(row, meta, clients.get(row['core_email']), row['dark_used'], now=now)
            row['metadata_state'] = meta['metadata_state']
            row['metadata_checked_at'] = meta.get('checked_at', 0)
            row['metadata_note'] = meta.get('note', '')
            row['subscription_received'] = bool(row['first_seen'])
            row['traffic_observed'] = row['dark_used'] > 0
            row['metadata_revision'] = self._review_revision(row, meta)
            row['unlimited_volume'] = row['legacy_total'] == 0 and row['service_status'] != 'needs_review'
        return rows

    def groups(self):
        groups = super().groups(); rows = self.rows(); by_id = {g['id']: g for g in groups}
        for group in groups:
            group.update(eligible=0, needs_review=0, blocked=0, legacy_unconfirmed=0, subscription_received=0, traffic_observed=0)
        for row in rows:
            group = by_id[row['group_id']]
            key = 'eligible' if row['service_status'] == 'eligible' else 'needs_review' if row['service_status'] == 'needs_review' else 'blocked'
            group[key] += 1
            group['legacy_unconfirmed'] += int(row['metadata_state'] == 'legacy_saved')
            group['subscription_received'] += int(row['subscription_received'])
            group['traffic_observed'] += int(row['traffic_observed'])
        return groups

    def require_eligible(self, row):
        row = dict(row)
        with self.store.lock:
            s = self.store.db.execute('SELECT * FROM restore_safety WHERE restore_id=?', (row['id'],)).fetchone()
            c = self.store.db.execute('SELECT body FROM core_clients WHERE email=?', (row['core_email'],)).fetchone()
        meta = dict(s) if s else {'metadata_state': 'verified' if row['scan_status'] == 'verified' else 'review'}
        reason = decision(row, meta, json.loads(c['body']) if c else None, sum(self.usage(row['id']).values()))
        if reason != 'eligible':
            raise HTTPException(403, {'code': 'restore_' + reason, 'message': 'Restore subscription is not eligible: ' + reason})

    def safety_status(self, group_id=None):
        return safety_snapshot(self, group_id)

    def review_metadata(self, restore_id, value):
        if not value['confirmed'] or len(value['note'].strip()) < 5:
            raise HTTPException(400, 'Explicit source metadata confirmation is required')
        if value['upload'] + value['download'] > MAX_BYTES:
            raise HTTPException(400, 'Source traffic counter overflow')
        with self._import_lock, self.engine.lock, self.store.transaction() as db:
            r = db.execute('SELECT * FROM restore_subscriptions WHERE id=?', (restore_id,)).fetchone()
            if not r:
                raise HTTPException(404, 'Restore user not found')
            s = db.execute('SELECT * FROM restore_safety WHERE restore_id=?', (restore_id,)).fetchone()
            if not s or self._review_revision(dict(r), dict(s)) != value['expectedRevision']:
                raise HTTPException(409, 'Metadata changed. Reopen the review before saving.')
            c = db.execute('SELECT body FROM core_clients WHERE email=?', (r['core_email'],)).fetchone()
            if not c:
                raise HTTPException(409, 'Restore identity missing; automatic recreation refused')
            body = json.loads(c['body']); body['totalGB'] = value['total']; body['expiryTime'] = value['expire'] * 1000
            now = time.time()
            db.execute('UPDATE core_clients SET body=? WHERE email=?', (json.dumps(body), r['core_email']))
            # verified here means a complete accepted snapshot, not an upstream
            # scan claim: metadata_state=manual and the review event retain provenance.
            db.execute('''UPDATE restore_subscriptions SET legacy_upload=?,legacy_download=?,legacy_total=?,legacy_expire=?,
                scan_status='verified',scan_error='',updated_at=? WHERE id=?''',
                (value['upload'], value['download'], value['total'], value['expire'], now, restore_id))
            db.execute("UPDATE restore_safety SET metadata_state='manual',checked_at=?,note=? WHERE restore_id=?", (now, value['note'].strip(), restore_id))
            detail = {'before': {k: r['legacy_' + k] for k in FIELDS}, 'after': {k: value[k] for k in FIELDS}, 'note': value['note'].strip()}
            db.execute('INSERT INTO restore_events(restore_id,event,detail,at) VALUES(?,?,?,?)',
                (restore_id, 'metadata.reviewed', json.dumps(detail), now))
        return self._apply_safety_change()

    def set_suspension(self, restore_id, value):
        with self._import_lock, self.engine.lock, self.store.transaction() as db:
            r = db.execute('SELECT * FROM restore_subscriptions WHERE id=?', (restore_id,)).fetchone()
            if not r:
                raise HTTPException(404, 'Restore user not found')
            s = db.execute('SELECT * FROM restore_safety WHERE restore_id=?', (restore_id,)).fetchone()
            if not s or self._review_revision(dict(r), dict(s)) != value['expectedRevision']:
                raise HTTPException(409, 'Restore state changed. Reopen before saving.')
            # Resume clears manual suspension only; quota and expiry still apply.
            now = time.time()
            db.execute('UPDATE restore_subscriptions SET enabled=?,updated_at=? WHERE id=?', (int(not value['suspended']), now, restore_id))
            db.execute('UPDATE restore_safety SET external_disabled=0,expected_enable=0 WHERE restore_id=?', (restore_id,))
            db.execute('INSERT INTO restore_events(restore_id,event,detail,at) VALUES(?,?,?,?)',
                (restore_id, 'suspension.changed', json.dumps({'suspended': value['suspended']}), now))
        return self._apply_safety_change()

    def _apply_safety_change(self):
        try:
            self.engine.apply(start=self.engine.running)
            return {'saved': True, 'applied': True, 'hub_running': self.engine.running}
        except Exception as ex:
            return {'saved': True, 'applied': False, 'apply_error': type(ex).__name__, 'retry': 'automatic_reconciliation'}

    def install_safety_routes(self, app, owner, writable, audit):
        @app.get('/api/dark-restore/safety')
        def status(groupId: str = '', p=Depends(owner)):
            return self.safety_status(groupId or None)

        @app.put('/api/dark-restore/{restore_id}/metadata')
        def review(restore_id: str, body: RestoreReviewBody, p=Depends(owner)):
            writable(); result = self.review_metadata(restore_id, body.model_dump())
            audit(p.actor, p.actor.id, 'dark_restore.metadata.review', restore_id, 'Explicit source review; DARK usage retained')
            return result

        @app.put('/api/dark-restore/{restore_id}/suspension')
        def suspend(restore_id: str, body: RestoreSuspensionBody, p=Depends(owner)):
            writable(); result = self.set_suspension(restore_id, body.model_dump())
            audit(p.actor, p.actor.id, 'dark_restore.suspension', restore_id, str(body.suspended))
            return result
