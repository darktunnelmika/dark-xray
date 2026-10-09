"""Grant a Node lease only after durable traffic import and fresh policy apply."""
from dark_policy import PolicyError


def verify_hub_accounting_policy(nodes, manager, engine, node_id):
    """Shared fail-closed policy check for normal leases and replacement Start."""
    # Include local/Restore bytes before checking quotas, expiry and manual blocks.
    engine.collect_stats(force=True, strict=True)
    manager.tick(suppress=False)
    if manager.last_error or engine.stats_error or not engine.config.writes_enabled:
        raise PolicyError('Hub accounting/policy engine is not healthy and writable')
    allowed = nodes._allowed_traffic_clients(node_id)
    with manager.store.lock:
        rows = manager.store.db.execute(
            "SELECT email,state,op,error FROM managed_clients WHERE state!='deleted'").fetchall()
    def lease_blocked(row):
        if row['email'] not in allowed:
            return False
        unresolved = row['op'] != 'none' or row['error'] or row['state'] not in ('applied',)
        if not unresolved:
            return False
        # A delete can be deferred when its former server is unreachable;
        # the last successfully applied policy is still the enforced one.
        if (row['op'] == 'delete' and row['state'] == 'error' and
                str(row['error']).startswith('Node connection failed:')):
            return False
        return True
    if any(lease_blocked(row) for row in rows):
        raise PolicyError('Managed client policy has unresolved operations or errors')


def grant_stopped_replacement_lease(nodes, manager, engine, node_id, traffic,
                                    state, control_revision, request):
    """Narrow replacement-only grant while disabled; never enable/publish the Node.

    Called only by the explicit activation coordinator, after saved Start intent,
    identity-pinned HTTPS, exact stopped stage and durable traffic import.
    """
    require_accounted_snapshot(nodes, manager, node_id, traffic)
    verify_hub_accounting_policy(nodes, manager, engine, node_id)
    challenge = traffic.get('accounting_lease')
    if not isinstance(challenge, str) or len(challenge) != 64:
        raise PolicyError('Replacement accounting challenge missing')
    current = nodes.desired_state(node_id, include_payload=False)
    if (current.get('pending') or current.get('last_error') or
            current['revision'] != state['revision'] or current['hash'] != state['hash']):
        raise PolicyError('Replacement configuration was not fully acknowledged')
    command = nodes.commands.status(node_id)
    if (not command.get('persisted') or command['action'] != 'start' or
            command['revision'] != control_revision or not command.get('pending')):
        raise PolicyError('Replacement Start intent changed before accounting grant')
    body = {'challenge': challenge, 'revision': state['revision'], 'hash': state['hash'],
            'controlRevision': control_revision}
    doc, _ms = request('/node/api/v1/accounting/lease', 'POST', body)
    if not isinstance(doc, dict) or not isinstance(doc.get('lease'), dict) or doc['lease'].get('valid') is not True:
        raise PolicyError('Replacement Node did not acknowledge valid accounting authority')
    return doc['lease']


def renew_accounting_lease(nodes, manager, engine, node_id, traffic, desired_provider, sync_provider):
    challenge = traffic.get('accounting_lease')
    if not challenge:
        return {'supported': False}
    require_accounted_snapshot(nodes, manager, node_id, traffic)
    try:
        with nodes.installations.operation(node_id):
            verify_hub_accounting_policy(nodes, manager, engine, node_id)
            state = desired_provider(node_id)
            nodes.sync_desired_state(node_id, state, legacy_bundles=sync_provider(node_id))
            current = nodes.desired_state(node_id, include_payload=False)
            if current.get('pending') or current.get('last_error') or current['revision'] != state['revision']:
                raise PolicyError('Node has not acknowledged current quota/configuration')
            command = nodes.commands.status(node_id)
            body = {'challenge': challenge, 'revision': state['revision'], 'hash': state['hash'],
                    'controlRevision': int(command.get('revision', 0))}
            doc, _ms = nodes._request(node_id, '/node/api/v1/accounting/lease', 'POST', body, 10.0)
            if not isinstance(doc, dict) or not doc.get('lease', {}).get('valid'):
                raise PolicyError('Node did not acknowledge a valid accounting lease')
            return doc['lease']
    except Exception as exc:
        raise PolicyError('Accounting lease withheld: ' + str(exc)[:350]) from exc


def require_accounted_snapshot(nodes, manager, node_id, traffic):
    """Native and Restore have separate ledgers; both must finish before a grant."""
    ignored = traffic.get('ignored_clients', 0)
    restored = traffic.get('restore_usage', {})
    counted = restored.get('clients', 0) if isinstance(restored, dict) else -1
    if type(ignored) is not int or type(counted) is not int or ignored < 0 or counted < 0 or ignored != counted:
        raise PolicyError('Accounting lease refused: Node reports unmanaged traffic identities')
    if not counted:
        return
    # The observer's count is produced only after its SQLite transaction commits.
    # Do not let a native/Restore identity collision offset an unknown identity.
    native = nodes._allowed_traffic_clients(node_id)
    with manager.store.lock:
        restored_ids = {row[0] for row in manager.store.db.execute(
            'SELECT core_email FROM restore_subscriptions')}
    if native.intersection(restored_ids):
        raise PolicyError('Accounting lease refused: ambiguous native/Restore traffic identity')
