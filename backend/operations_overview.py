"""Read-only operations snapshot. No probes, reconciliation, or lifecycle actions."""
from __future__ import annotations
import json
import math
import re
import time


def safe_error(value) -> str:
    """Never propagate credential-bearing URLs or keyed secrets into overview UI."""
    text = str(value or '')
    text = re.sub(r'https?://[^\s<>\"\']+', '[URL redacted]', text, flags=re.I)
    text = re.sub(r'\b\d{6,}:[A-Za-z0-9_-]{20,}\b', '[token redacted]', text)
    text = re.sub(r'(?i)(bearer\s+)\S+', r'\1[redacted]', text)
    text = re.sub(r'(?i)((?:[\w-]*(?:token|password|secret|private.?key|api.?key)[\w-]*)[\"\']?\s*[:=]\s*)[\"\']?[^\s,;\"\']+', r'\1[redacted]', text)
    return text[:300]


def timestamp(value):
    try:
        value = float(value)
        return value if math.isfinite(value) and value > 0 else None
    except (TypeError, ValueError):
        return None


def runtime_status(core):
    if core.get('last_error'):
        return 'error'
    if core.get('state') != 'running':
        return 'stopped' if core.get('state') == 'stopped' else 'unknown'
    return 'pending' if core.get('dirty') else 'applied'


def snapshot(store, engine, nodes, bot_runtime=None, *, now=None):
    now = float(time.time() if now is None else now)
    core = engine.runtime_state()
    # Fetch the registry exactly once: cached health + acknowledged desired state.
    registered = nodes.list()
    node_items = []
    for node in registered:
        if not node.get('enabled'):
            continue
        desired, control = node.get('desired_state') or {}, node.get('control') or {}
        health = (node.get('health') or {}).get('core') or {}
        errors = [node.get('last_error'), desired.get('last_error'), health.get('last_error')]
        errors += [a.get('last_error') for a in node.get('assignments', [])]
        error = safe_error(next((x for x in errors if x), ''))
        seen = timestamp(node.get('last_seen'))
        age = now - seen if seen else None
        freshness = 'unknown' if age is None or age < 0 else 'fresh' if age <= 20 else 'stale' if age < 180 else 'offline'
        if error:
            status = 'error'
        elif not node.get('online') or freshness == 'offline':
            status = 'offline'
        elif freshness != 'fresh':
            status = 'stale' if freshness == 'stale' else 'unknown'
        elif desired.get('pending') or control.get('pending') or health.get('dirty'):
            status = 'pending'
        elif health.get('state') != 'running':
            status = 'stopped' if health.get('state') == 'stopped' else 'unknown'
        elif not desired.get('applied_revision') or not timestamp(desired.get('applied_at')):
            status = 'pending'
        else:
            status = 'applied'
        node_items.append({'id': node['id'], 'name': str(node.get('name') or node['id']),
            'online': bool(node.get('online')), 'status': status, 'telemetry_state': freshness,
            'last_seen': seen, 'applied_at': timestamp(desired.get('applied_at')),
            'pending': bool(desired.get('pending') or control.get('pending') or health.get('dirty')),
            'xray_state': str(health.get('state') or 'unknown'), 'last_error': error})
    with store.lock:
        bots = [dict(r) for r in store.db.execute("SELECT owner,enabled,bot_username,last_error,last_seen,CASE WHEN token_enc<>'' THEN 1 ELSE 0 END AS configured FROM telegram_bots ORDER BY owner")]
        profiles = {r['scope']: r['outbound_json'] for r in store.db.execute('SELECT scope,outbound_json FROM warp_profiles')}
        pending = {r['scope'] for r in store.db.execute('SELECT scope FROM warp_pending_profiles')}
    bot_items = []
    for bot in bots:
        # Runtime.status() copies in-memory state only; it never contacts Telegram.
        live = bot_runtime.status(bot['owner']) if bot_runtime else {}
        error = safe_error(bot.get('last_error') or live.get('runtime_error'))
        seen = timestamp(bot.get('last_seen'))
        age = now - seen if seen else None
        if not bot['enabled']:
            status = 'disabled'
        elif not bot['configured']:
            status = 'unconfigured'
        elif error or live.get('runtime_state') == 'error':
            status = 'error'
        elif age is None or age < 0:
            status = 'unknown'
        elif age >= 180:
            status = 'stale'
        elif live.get('runtime_state') == 'online':
            status = 'online'
        elif live.get('runtime_state') == 'stopped':
            status = 'stopped'
        else:
            status = 'unknown'
        bot_items.append({'owner': bot['owner'], 'username': bot['bot_username'],
            'enabled': bool(bot['enabled']), 'configured': bool(bot['configured']),
            'state': status, 'last_seen': seen, 'last_error': error})
    hub = {'state': core.get('state') or 'unknown', 'status': runtime_status(core),
           'dirty': bool(core.get('dirty')), 'last_error': safe_error(core.get('last_error')),
           'applied_at': timestamp(core.get('last_apply'))}
    targets = [('hub', 'HUB', hub)] + [('node:' + x['id'], x['name'], x) for x in node_items]
    warp_items = []
    for scope, name, source in targets:
        configured, staged = scope in profiles, scope in pending
        endpoint, invalid = '', False
        if configured:
            try:
                profile = json.loads(profiles[scope])
                endpoint = profile['settings']['peers'][0]['endpoint']
                if not isinstance(endpoint, str) or not endpoint or not re.fullmatch(r'[A-Za-z0-9.\[\]:_-]{1,255}', endpoint):
                    raise ValueError('Invalid saved endpoint')
            except (ValueError, TypeError, KeyError, IndexError):
                endpoint, invalid = '', True
        status = 'error' if invalid else 'pending' if staged else source['status'] if configured else 'not_configured'
        warp_items.append({'server': scope, 'name': name, 'configured': configured,
            'pending_registration': staged, 'endpoint': endpoint, 'status': status,
            'apply_state': {'status': source['status'], 'applied_at': source.get('applied_at')},
            'connectivity': 'not_tested',
            'last_error': 'Invalid saved WARP endpoint' if invalid else source['last_error'] if configured else ''})
    return {'generated_at': now, 'source': 'local runtime and cached acknowledgements',
        'read_only': True, 'network_probes': False, 'hub': hub, 'nodes': node_items,
        'telegram': bot_items, 'warp': warp_items,
        'summary': {'nodes_enabled': len(node_items), 'nodes_online': sum(x['online'] for x in node_items),
                    'nodes_pending': sum(x['pending'] for x in node_items),
                    'nodes_attention': sum(x['status'] != 'applied' for x in node_items),
                    'bot_errors': sum(x['state'] == 'error' for x in bot_items),
                    'bots_attention': sum(x['enabled'] and x['state'] != 'online' for x in bot_items),
                    'warp_pending': sum(x['status'] == 'pending' for x in warp_items),
                    'warp_attention': sum(x['status'] not in ('not_configured','applied') for x in warp_items)}}
