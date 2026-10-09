"""Bounded Restore-only policy update; no transactions for an unchanged fleet."""
import json
import time


def reconcile(restore, decide, records=None):
    if not restore.engine.config.writes_enabled:
        return {'changed': 0, 'paused': True}
    record_map = {r['email']: r for r in records or []}
    changed = 0; now = time.time()
    with restore.engine.lock, restore.store.lock:
        db = restore.store.db
        missing = db.execute('''SELECT 1 FROM restore_subscriptions r LEFT JOIN restore_safety s
            ON s.restore_id=r.id WHERE s.restore_id IS NULL AND r.promoted_at=0 AND r.deleted_at=0 LIMIT 1''').fetchone()
        if missing:
            with restore.store.transaction() as tx:
                restore._seed_safety(tx)
        rows = list(db.execute('''SELECT r.*,s.metadata_state,s.checked_at,s.note,
            s.expected_enable,s.external_disabled,s.decision old_decision,c.body core_body,
            COALESCE(u.used,0) dark_used
            FROM restore_subscriptions r JOIN restore_safety s ON s.restore_id=r.id
            LEFT JOIN core_clients c ON c.email=r.core_email
            LEFT JOIN (SELECT restore_id,SUM(up+down) used FROM restore_usage GROUP BY restore_id) u ON u.restore_id=r.id
            WHERE r.promoted_at=0 AND r.deleted_at=0'''))
        pending = []
        for raw in rows:
            r = dict(raw); client = json.loads(r['core_body']) if r['core_body'] else None
            metadata_state = r['metadata_state']
            if metadata_state == 'review' and r['scan_status'] == 'verified':
                metadata_state = r['metadata_state'] = 'verified'
            external = int(r['external_disabled'])
            if client is not None and not client.get('enable', True) and r['expected_enable']:
                external = r['external_disabled'] = 1
            reason = decide(r, r, client, int(r['dark_used']), now=now)
            desired = reason == 'eligible'
            observed = bool(client.get('enable', True)) if client is not None else False
            core_change = client is not None and observed != desired
            metadata_change = (r['old_decision'] != reason or r['expected_enable'] != int(desired)
                               or raw['external_disabled'] != external or raw['metadata_state'] != metadata_state)
            if core_change:
                client['enable'] = desired
            if core_change or metadata_change:
                pending.append((r, client, core_change, metadata_change, metadata_state, external, reason, desired))
            if r['core_email'] in record_map:
                record_map[r['core_email']]['enable'] = desired
        if pending:
            with restore.store.transaction() as tx:
                for r,client,core_change,metadata_change,metadata_state,external,reason,desired in pending:
                    if core_change:
                        tx.execute('UPDATE core_clients SET body=? WHERE email=?', (json.dumps(client), r['core_email']))
                        changed += 1
                    if metadata_change:
                        tx.execute('''UPDATE restore_safety SET metadata_state=?,expected_enable=?,external_disabled=?,
                            decision=?,decision_at=? WHERE restore_id=?''',
                            (metadata_state, int(desired), external, reason, now, r['id']))
                        tx.execute('INSERT INTO restore_events(restore_id,event,detail,at) VALUES(?,?,?,?)',
                            (r['id'], 'eligibility.changed', json.dumps({'status': reason, 'enabled': desired}), now))
    return {'changed': changed, 'paused': False}
