"""Grant a Node lease only after durable traffic import and fresh policy apply."""
from dark_policy import PolicyError


def renew_accounting_lease(nodes, manager, engine, node_id, traffic, desired_provider, sync_provider):
    challenge = traffic.get('accounting_lease')
    if not challenge:
        return {'supported': False}
    if traffic.get('ignored_clients'):
        raise PolicyError('Accounting lease refused: Node reports unmanaged traffic identities')
    try:
        with nodes.installations.operation(node_id):
            # Even zero-byte traffic must check expiry, representative credit,
            # manual blocks and Restore eligibility before renewing authority.
            manager.tick(suppress=False)
            if manager.last_error or engine.stats_error or not engine.config.writes_enabled:
                raise PolicyError('Hub accounting/policy engine is not healthy and writable')
            allowed = nodes._allowed_traffic_clients(node_id)
            with manager.store.lock:
                rows = manager.store.db.execute(
                    "SELECT email,state,op,error FROM managed_clients WHERE state!='deleted'").fetchall()
            if any(row['email'] in allowed and (row['op'] != 'none' or row['error'] or
                   row['state'] not in ('applied',)) for row in rows):
                raise PolicyError('Managed client policy has unresolved operations or errors')
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
