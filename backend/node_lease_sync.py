"""Grant a Node lease only after durable traffic import and fresh policy apply."""
import logging
import math
import time

from dark_policy import PolicyError

# A 30-second Node token must leave 12s for an authenticated ACK (10s timeout).
CHALLENGE_ACK_MAX_AGE_SECONDS=18.0
MAX_CHALLENGE_REFRESHES=2
logger=logging.getLogger(__name__)


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
    # The Node may still have the staged Stop revision: the new Start is a
    # Hub-saved intent, but has not yet been sent. Bind the lease to the
    # Node's CURRENT durable command, either stopped at N-1 or a failed Start
    # at N. Do not fake a future Node receipt or disable lease enforcement.
    health, _ = request('/node/api/health')
    receipt = health.get('control_receipt') if isinstance(health, dict) else None
    core = health.get('core') if isinstance(health, dict) else None
    if (not isinstance(receipt, dict) or not isinstance(core, dict) or
            core.get('state') != 'stopped' or
            type(receipt.get('revision')) is not int or
            receipt['revision'] not in (control_revision - 1, control_revision) or
            (receipt['revision'] == control_revision - 1 and
             (receipt.get('action') != 'stop' or receipt.get('phase') != 'applied')) or
            (receipt['revision'] == control_revision and receipt.get('action') != 'start')):
        raise PolicyError('Replacement control intent and Node state do not match')
    body = {'challenge': challenge, 'revision': state['revision'], 'hash': state['hash'],
            'controlRevision': receipt['revision']}
    doc, _ms = request('/node/api/v1/accounting/lease', 'POST', body)
    if not isinstance(doc, dict) or not isinstance(doc.get('lease'), dict) or doc['lease'].get('valid') is not True:
        raise PolicyError('Replacement Node did not acknowledge valid accounting authority')
    return doc['lease']


def _challenge_hub_age(snapshot:dict)->float|None:
    """Safe upper bound since authenticated GET began, using local monotonic clock.

    Legacy callers without timing metadata keep the original strict grant path.
    """
    issued=snapshot.get('accounting_challenge_requested_at')
    if type(issued) not in (int,float) or not math.isfinite(issued):
        return None
    age=time.monotonic()-issued
    if age<0:raise PolicyError('Accounting lease monotonic challenge clock invalid')
    return age


def renew_accounting_lease(nodes, manager, engine, node_id, traffic, desired_provider, sync_provider):
    challenge=traffic.get('accounting_lease')
    if not challenge:
        return {'supported': False}
    require_accounted_snapshot(nodes,manager,node_id,traffic)
    try:
        with nodes.installations.operation(node_id):
            policy_start=time.monotonic()
            verify_hub_accounting_policy(nodes,manager,engine,node_id)
            policy_seconds=time.monotonic()-policy_start

            def verified_state():
                state=desired_provider(node_id)
                current=nodes.desired_state(node_id,include_payload=False)
                def acknowledged(row):
                    return (not row.get('pending') and not row.get('last_error') and
                            row.get('revision')==state['revision'] and
                            row.get('hash')==state['hash'] and
                            row.get('applied_revision')==state['revision'] and
                            row.get('applied_hash')==state['hash'])
                if not acknowledged(current):
                    nodes.sync_desired_state(node_id,state,legacy_bundles=sync_provider(node_id))
                    current=nodes.desired_state(node_id,include_payload=False)
                if not acknowledged(current):
                    raise PolicyError('Node has not acknowledged current quota/configuration')
                command=nodes.commands.status(node_id)
                return state,int(command.get('revision',0))

            state,revision=verified_state()
            refreshes=0
            while True:
                age=_challenge_hub_age(traffic)
                stale=(age is not None and age>CHALLENGE_ACK_MAX_AGE_SECONDS)
                if stale and refreshes>=MAX_CHALLENGE_REFRESHES:
                    raise PolicyError('Accounting lease challenge remained too old after bounded refresh')
                if stale:
                    reason='slow-pregrant'
                else:
                    reason=''
                    body={'challenge':traffic['accounting_lease'],
                          'revision':state['revision'],'hash':state['hash'],
                          'controlRevision':revision}
                    try:
                        doc,_ms=nodes._request(node_id,'/node/api/v1/accounting/lease','POST',body,10.0)
                    except PolicyError as exc:
                        # No automatic retry for auth/TLS, identity, policy,
                        # configuration or control failures.
                        retry=(getattr(exc,'status',None)==409 and
                               str(exc)=='Node HTTP 409: Accounting lease challenge is expired, stale or unknown')
                        if not retry or refreshes>=MAX_CHALLENGE_REFRESHES:
                            raise
                        reason='node-rejected-old-challenge'
                    else:
                        if not isinstance(doc,dict) or not doc.get('lease',{}).get('valid'):
                            raise PolicyError('Node did not acknowledge a valid accounting lease')
                        return doc['lease']
                refreshes+=1
                logger.warning('Node accounted lease challenge refresh: node=%s reason=%s age_s=%s policy_s=%.2f attempt=%d',
                               node_id,reason,round(age,1) if age is not None else 'unknown',
                               policy_seconds,refreshes)
                # Reissue token only with a fully authenticated fresh traffic
                # checkpoint committed to the native/Restore ledgers first.
                traffic=nodes.sync_traffic(node_id)
                if not traffic.get('accounting_lease'):
                    raise PolicyError('Fresh accounted Node challenge missing')
                require_accounted_snapshot(nodes,manager,node_id,traffic)
                # New usage can cross a quota limit. Re-run strict accounting
                # policy and any changed desired-state acknowledgement.
                if traffic.get('charged_bytes'):
                    verify_hub_accounting_policy(nodes,manager,engine,node_id)
                state,revision=verified_state()
    except Exception as exc:
        raise PolicyError('Accounting lease withheld: '+str(exc)[:350]) from exc


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
