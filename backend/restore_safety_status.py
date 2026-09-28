"""Read-only Restore safety snapshot. No test traffic or synthetic migration events."""
from __future__ import annotations
import json
import time
from pathlib import Path


def safety_snapshot(restore, group_id=None):
    rows = restore.rows(group_id); counts = {}; now = time.time()
    for row in rows:
        state = row['service_status']; counts[state] = counts.get(state, 0) + 1
    inbounds = restore.engine.inbounds()
    local_ids = {int(i['id']) for i in inbounds if i.get('enable', True) and
                 i.get('panelMeta', {}).get('deployLocal', True) is not False}
    active = set(); hub_state = 'stopped'
    if restore.engine.running:
        try:
            config = json.loads((Path(restore.engine.runtime) / 'active.json').read_text())
            for inbound in config.get('inbounds', []):
                settings = inbound.get('settings') or {}
                active.update(c.get('email') for c in settings.get('clients', []))
                active.update(c.get('user') for c in settings.get('accounts', []))
            matches = all((r['core_email'] in active) ==
                          (r['service_status'] == 'eligible' and bool(local_ids.intersection(r['inbound_ids']))) for r in rows)
            hub_state = 'synced' if matches else 'pending'
        except (OSError, ValueError, TypeError, AttributeError):
            hub_state = 'unverified'
    with restore.store.lock:
        desired = {r['node_id']: dict(r) for r in restore.store.db.execute('SELECT * FROM remote_node_desired_state')}
    runtimes = []
    for node in restore.nodes.list():
        ids = {int(a['local_inbound_id']) for a in node.get('assignments', [])}
        relevant = [r for r in rows if ids.intersection(r['inbound_ids'])]
        if not relevant:
            continue
        ds = desired.get(node['id']); state = 'unverified'
        online = bool(node.get('online'))
        if not online:
            state = 'offline'
        elif ds:
            try:
                payload = json.loads(ds['desired_json']); states = {}
                for assignment in payload.get('assignments', []):
                    for c in assignment.get('clients', []):
                        states.setdefault(c['sourceEmail'], []).append(bool(c['client'].get('enable', True)))
                match = all((bool(states.get(r['core_email'])) and all(states[r['core_email']]))
                            == (r['service_status'] == 'eligible') and
                            (r['service_status'] == 'eligible' or not any(states.get(r['core_email'], []))) for r in relevant)
                acknowledged = ds['revision'] > 0 and ds['revision'] == ds['applied_revision'] and ds['desired_hash'] == ds['applied_hash']
                core = (node.get('health') or {}).get('core') or {}
                healthy = core.get('state') == 'running' and not core.get('dirty') and not core.get('last_error')
                state = 'synced' if match and acknowledged and healthy and not ds['last_error'] else 'pending'
            except (ValueError, TypeError, KeyError, AttributeError):
                state = 'unverified'
        # This covers all deployed copies, including Nodes not currently published
        # in a group's subscription. Hiding a route is not credential revocation.
        runtimes.append({'id': node['id'], 'name': node['name'], 'state': state,
                         'last_seen': node.get('last_seen', 0), 'error': bool(node.get('last_error'))})
    return {'counts': counts, 'clients': len(rows),
            'subscription_received': sum(r['subscription_received'] for r in rows),
            'traffic_observed': sum(r['traffic_observed'] for r in rows),
            'legacy_unconfirmed': sum(r['metadata_state'] == 'legacy_saved' for r in rows),
            'hub_running': restore.engine.running, 'hub_state': hub_state,
            'writes_enabled': restore.engine.config.writes_enabled, 'nodes': runtimes, 'sampled_at': now,
            'enforcement_model': 'hub_reconciliation', 'offline_enforcement_guaranteed': False, 'byte_exact_quota': False}
