"""Owner-confirmed sequence repair after restoring a stale Hub backup.

Repair means conditional Stop plus *pending* local configuration re-numbering.
It never restores missing users/usage, pushes a configuration, enables a Node,
rotates credentials, adopts an installation, or automatically replays Start.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
import uuid

from fastapi import Depends
from pydantic import BaseModel, ConfigDict, StrictBool, StrictStr
from dark_policy import PolicyError
from node_recovery_protocol import LIMIT, valid_hex, validate_checkpoint


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def assert_recovery_allows(db, node_id, action):
    if action not in {'enable', 'start', 'restart'}:
        return
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='remote_node_recoveries'").fetchone():
        return
    if db.execute('SELECT 1 FROM remote_node_recoveries r JOIN remote_node_installations i '
                  "ON i.binding_id=r.binding_id WHERE r.node_id=? AND i.retired_at=0 AND r.phase='pending'",
                  (node_id,)).fetchone():
        raise PolicyError('Node recovery is pending; verify Stop before enable/Start')


class RecoveryConfirmation(BaseModel):
    model_config = ConfigDict(extra='forbid')
    bindingId: StrictStr
    reviewHash: StrictStr
    acknowledgeServiceInterruption: StrictBool
    acknowledgeBackupMayBeStale: StrictBool


class NodeRecovery:
    def __init__(self, registry):
        self.registry, self.store = registry, registry.store
        with self.store.lock:
            self.store.db.execute('''CREATE TABLE IF NOT EXISTS remote_node_recoveries(
                binding_id TEXT PRIMARY KEY,attempt_id TEXT NOT NULL UNIQUE,node_id TEXT NOT NULL,
                installation_id TEXT NOT NULL,origin TEXT NOT NULL,credential_hash TEXT NOT NULL,
                review_hash TEXT NOT NULL,local_hash TEXT NOT NULL,expected_json TEXT NOT NULL,
                command_revision INTEGER NOT NULL,command_id TEXT NOT NULL,
                phase TEXT NOT NULL CHECK(phase IN ('pending','recovered_stopped')),
                last_error TEXT NOT NULL DEFAULT '',created_at REAL NOT NULL,completed_at REAL NOT NULL DEFAULT 0,
                rebased_revision INTEGER NOT NULL DEFAULT 0)''')

    def _local(self, node_id):
        """Read only; do not call desired_state(), which can rewrite its payload."""
        with self.store.lock:
            binding = self.registry.installations.capture(node_id)
            control = self.store.db.execute('SELECT revision,command_id,action,applied_revision FROM remote_node_control '
                                            'WHERE node_id=?', (node_id,)).fetchone()
            desired = self.store.db.execute('SELECT * FROM remote_node_desired_state WHERE node_id=?', (node_id,)).fetchone()
            command = dict(control) if control else {'revision': 0, 'command_id': '', 'action': '', 'applied_revision': 0}
            configuration = dict(desired) if desired else None
            identity = {key: binding[key] for key in ('binding_id','agent_id','installation_id','origin','enabled')}
            identity['credential_hash'] = digest(binding['token_enc'])
            # Diagnostics/timestamps may change without changing operator intent.
            value = {'binding': identity, 'command': command, 'configuration': None if not desired else
                     {key: desired[key] for key in ('revision','desired_hash','desired_json','applied_revision','applied_hash')}}
        return binding, command, configuration, digest(value)

    def _row(self, node_id, attempt_id=None):
        if attempt_id is not None and not valid_hex(attempt_id, 32):
            raise PolicyError('Invalid recovery attempt')
        with self.store.lock:
            if attempt_id:
                row = self.store.db.execute('SELECT * FROM remote_node_recoveries WHERE node_id=? AND attempt_id=?',
                                            (node_id, attempt_id)).fetchone()
            else:
                binding = self.registry.installations.capture(node_id)
                row = self.store.db.execute('SELECT * FROM remote_node_recoveries WHERE binding_id=?',
                                            (binding['binding_id'],)).fetchone()
        return dict(row) if row else None

    def status(self, node_id, attempt_id=None):
        row = self._row(node_id, attempt_id)
        result = {'node_id': node_id, 'phase': 'not_started', 'recovery_completed': False,
                  'status_scope': 'saved_recovery_receipt', 'live_state_verified': False,
                  'configuration_applied': False, 'usage_reconciled': False, 'service_started': False}
        if row:
            binding = self.registry.installations.capture(node_id)
            result.update({key: row[key] for key in ('attempt_id','binding_id','installation_id','phase','last_error',
                                                   'command_revision','rebased_revision','created_at','completed_at')})
            result['binding_current'] = binding['binding_id'] == row['binding_id'] and binding['installation_id'] == row['installation_id']
            result['recovery_completed'] = row['phase'] == 'recovered_stopped'
            result['activation_held'] = result['binding_current'] and row['phase'] == 'pending'
            result['node_enabled_now'] = bool(binding['enabled'])
        elif attempt_id:
            raise PolicyError('Recovery attempt not found')
        return result

    def _exchange(self, binding, path, body=None):
        from nodes import node_https_request
        try:
            token = self.registry.cipher.decrypt(binding['token_enc'].encode()).decode()
            return node_https_request(binding['origin'], token, path, 'GET' if body is None else 'POST', body, 12.0,
                                      agent_id=binding['agent_id'], installation_id=binding['installation_id'])[0]
        except (PolicyError, OSError, ValueError) as exc:
            raise PolicyError('recovery_contact_or_identity_failed') from exc

    def _remote(self, binding):
        doc = self._exchange(binding, '/node/api/v1/recovery/state')
        if (not isinstance(doc, dict) or doc.get('service') != 'DARK XRAY NODE'
                or type(doc.get('protocol')) is not int or doc['protocol'] != 1
                or doc.get('node_id') != binding['agent_id'] or doc.get('installation_id') != binding['installation_id']
                or doc.get('writes_enabled') is not True
                or not isinstance(doc.get('core'),dict) or doc['core'].get('state') not in ('running','stopped')
                or not isinstance(doc.get('run_control'),dict)
                or type(doc['run_control'].get('manual_stop')) is not bool
                or type(doc['run_control'].get('effective_running')) is not bool):
            raise PolicyError('recovery_protocol_or_identity_unavailable')
        validate_checkpoint(doc.get('checkpoint'))
        return doc

    def _assert_no_replacement(self, node_id):
        # Do not compete with the replacement lifecycle's own Stop/Start journal.
        with self.store.lock:
            for table, query in (
                ('remote_node_replacements', 'SELECT 1 FROM remote_node_replacements WHERE node_id=?'),
                ('remote_node_replacement_deployments', 'SELECT 1 FROM remote_node_replacement_deployments d '
                 'JOIN remote_node_installations i ON i.binding_id=d.binding_id '
                 'WHERE d.node_id=? AND i.retired_at=0 AND d.activation_hold=1')):
                if (self.store.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
                        and self.store.db.execute(query, (node_id,)).fetchone()):
                    raise PolicyError('Resolve pending Node replacement before sequence recovery')

    def _review(self, node_id):
        self._assert_no_replacement(node_id)
        binding, command, config, local_hash = self._local(node_id)
        if not binding['installation_id']:
            raise PolicyError('Verify and pin the current Agent installation before recovery')
        if not config:
            raise PolicyError('No Hub configuration snapshot; use explicit enrolment/rebuild instead')
        remote = self._remote(binding)
        if self._local(node_id)[3] != local_hash:
            raise PolicyError('recovery_review_changed')
        check = remote['checkpoint']; rc, rd = check['command'], check['configuration']
        reasons = []
        pending = self._row(node_id)
        if pending and pending['phase'] == 'pending':
            reasons.append('recovery_incomplete')
        if rc['revision'] > command['revision']:
            reasons.append('agent_command_ahead')
        elif rc['revision'] == command['revision'] and (rc['commandId'], rc['action']) != (command['command_id'], command['action']):
            reasons.append('command_identity_conflict')
        if command['applied_revision'] > rc['revision']:
            reasons.append('agent_command_behind_ack')
        if rd['revision'] > config['revision']:
            reasons.append('agent_configuration_ahead')
        elif rd['revision'] == config['revision'] and rd['hash'] != config['desired_hash']:
            reasons.append('configuration_hash_conflict')
        if config['applied_revision'] > rd['revision']:
            reasons.append('agent_configuration_behind_ack')
        if max(command['revision'], rc['revision'], config['revision'], rd['revision']) >= LIMIT:
            raise PolicyError('recovery_sequence_exhausted')
        review_hash = digest({'local': local_hash, 'remote': check})
        result = {'node_id': node_id, 'binding_id': binding['binding_id'], 'review_hash': review_hash,
                  'recovery_needed': bool(reasons), 'reasons': reasons,
                  'hub_command_revision': command['revision'], 'agent_command_revision': rc['revision'],
                  'hub_configuration_revision': config['revision'], 'agent_configuration_revision': rd['revision'],
                  'agent_core_state': (remote.get('core') or {}).get('state', 'unknown'),
                  'configuration_will_be_applied': False, 'node_will_remain_disabled': True,
                  'usage_will_be_reconstructed': False, 'backup_contents_require_operator_review': True}
        return result, binding, check, command, config, local_hash

    def review(self, node_id):
        with self.registry._node_operation(node_id):
            return self._review(node_id)[0]

    def stop(self, node_id, *, binding_id, review_hash, acknowledge_interruption, acknowledge_stale_backup):
        if (not valid_hex(binding_id, 32) or not valid_hex(review_hash, 64)
                or acknowledge_interruption is not True or acknowledge_stale_backup is not True):
            raise PolicyError('Explicit recovery confirmations required')
        with self.registry._node_operation(node_id):
            old = self._row(node_id)
            if old and old['binding_id'] == binding_id and old['review_hash'] == review_hash:
                return self.retry(node_id, old['attempt_id'])  # Completed retry is read-only, never disables again.
            review, binding, expected, command, config, local_hash = self._review(node_id)
            if (not review['recovery_needed'] or binding_id != binding['binding_id']
                    or not hmac.compare_digest(review_hash, review['review_hash'])):
                raise PolicyError('recovery_review_changed_or_not_required')
            now, attempt, command_id = time.time(), uuid.uuid4().hex, uuid.uuid4().hex
            revision = max(command['revision'], expected['command']['revision']) + 1
            with self.store.transaction() as db:
                if self._local(node_id)[3] != local_hash:
                    raise PolicyError('recovery_review_changed')
                self._assert_no_replacement(node_id)
                db.execute('''INSERT INTO remote_node_control(node_id,revision,command_id,action,updated_at)
                    VALUES(?,?,?,'stop',?) ON CONFLICT(node_id) DO UPDATE SET revision=excluded.revision,
                    command_id=excluded.command_id,action='stop',applied_revision=0,applied_at=0,last_error='',updated_at=excluded.updated_at''',
                           (node_id, revision, command_id, now))
                db.execute("UPDATE remote_nodes SET enabled=0,last_seen=0,last_health='{}',last_error='',updated_at=? WHERE id=?", (now,node_id))
                after_hash = self._local(node_id)[3]
                db.execute('''INSERT INTO remote_node_recoveries(binding_id,attempt_id,node_id,installation_id,origin,
                    credential_hash,review_hash,local_hash,expected_json,command_revision,command_id,phase,created_at)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,'pending',?) ON CONFLICT(binding_id) DO UPDATE SET
                    attempt_id=excluded.attempt_id,credential_hash=excluded.credential_hash,review_hash=excluded.review_hash,
                    local_hash=excluded.local_hash,expected_json=excluded.expected_json,command_revision=excluded.command_revision,
                    command_id=excluded.command_id,phase='pending',last_error='',created_at=excluded.created_at,
                    completed_at=0,rebased_revision=0''',
                           (binding_id,attempt,node_id,binding['installation_id'],binding['origin'],digest(binding['token_enc']),
                            review_hash,after_hash,json.dumps(expected),revision,command_id,now))
            return self.retry(node_id, attempt)

    def _assert(self, row):
        binding, _, _, local_hash = self._local(row['node_id'])
        latest = self._row(row['node_id'])
        if (not latest or latest['attempt_id'] != row['attempt_id'] or latest['phase'] != 'pending'
                or binding['binding_id'] != row['binding_id'] or binding['installation_id'] != row['installation_id']
                or binding['origin'] != row['origin'] or digest(binding['token_enc']) != row['credential_hash']
                or binding['enabled'] or local_hash != row['local_hash']):
            raise PolicyError('recovery_local_state_changed')
        self._assert_no_replacement(row['node_id'])
        return binding

    def retry(self, node_id, attempt_id):
        with self.registry._node_operation(node_id):
            row = self._row(node_id, attempt_id)
            if not row:
                raise PolicyError('Recovery attempt not found')
            if row['phase'] == 'recovered_stopped':
                return self.status(node_id, attempt_id)
            try:
                with self.store.lock:
                    binding = self._assert(row)
                expected = validate_checkpoint(json.loads(row['expected_json']))
                body = {'nodeId': binding['agent_id'], 'installationId': binding['installation_id'],
                        'revision': row['command_revision'], 'commandId': row['command_id'], 'expected': expected}
                ack = self._exchange(binding, '/node/api/v1/recovery/stop', body)
                if (not isinstance(ack,dict) or ack.get('service') != 'DARK XRAY NODE' or ack.get('node_id') != binding['agent_id']
                        or type(ack.get('revision')) is not int or ack['revision'] != row['command_revision']
                        or ack.get('commandId') != row['command_id'] or ack.get('action') != 'stop'
                        or ack.get('applied') is not True or (ack.get('engine') or {}).get('state') != 'stopped'):
                    raise PolicyError('recovery_stop_unacknowledged')
                with self.store.lock:
                    self._assert(row)
                remote = self._remote(binding)
                if (remote['checkpoint']['command'] != {'revision': row['command_revision'], 'commandId': row['command_id'], 'action': 'stop'}
                        or remote['checkpoint']['configuration'] != expected['configuration']
                        or (remote.get('core') or {}).get('state') != 'stopped'
                        or (remote.get('run_control') or {}).get('manual_stop') is not True
                        or (remote.get('run_control') or {}).get('effective_running') is not False):
                    raise PolicyError('recovery_stop_not_independently_verified')
                with self.store.transaction() as db:
                    self._assert(row)
                    config = db.execute('SELECT * FROM remote_node_desired_state WHERE node_id=?', (node_id,)).fetchone()
                    payload = self.registry._open_desired_payload(config['desired_json'])
                    payload = {**payload, 'nodeId': binding['agent_id'], 'desiredRunning': False}
                    _, config_hash = self.registry._desired_payload(payload)
                    sealed = self.registry._seal_desired_payload(payload)
                    revision = max(config['revision'], expected['configuration']['revision']) + 1
                    if revision > LIMIT:
                        raise PolicyError('recovery_sequence_exhausted')
                    db.execute("UPDATE remote_node_desired_state SET revision=?,desired_hash=?,desired_json=?,"
                               "applied_revision=0,applied_hash='',applied_at=0,last_error='',updated_at=? WHERE node_id=?",
                               (revision, config_hash, sealed, time.time(), node_id))
                    db.execute("UPDATE remote_node_inbounds SET remote_inbound_id=0,last_sync=0,last_error='recovery_requires_sync' WHERE node_id=?", (node_id,))
                    db.execute("UPDATE remote_node_control SET applied_revision=revision,applied_at=?,last_error='' WHERE node_id=?", (time.time(),node_id))
                    db.execute("UPDATE remote_node_recoveries SET phase='recovered_stopped',last_error='',completed_at=?,rebased_revision=? WHERE attempt_id=?",
                               (time.time(),revision,attempt_id))
            except Exception as exc:
                safe_errors = {'recovery_local_state_changed', 'recovery_contact_or_identity_failed',
                               'recovery_stop_unacknowledged', 'recovery_stop_not_independently_verified',
                               'recovery_sequence_exhausted', 'recovery_protocol_or_identity_unavailable'}
                error = str(exc) if isinstance(exc, PolicyError) and str(exc) in safe_errors else 'recovery_not_confirmed'
                # Untrusted errors may contain tokens/configuration. Only a fixed local code is saved.
                with self.store.transaction() as db:
                    db.execute("UPDATE remote_node_recoveries SET last_error=? WHERE attempt_id=? AND phase='pending'", (error,attempt_id))
            return self.status(node_id, attempt_id)


def install_hub_recovery(app, registry, owner, writable, audit):
    recovery = NodeRecovery(registry)
    app.state.node_recovery = recovery

    @app.post('/api/nodes/{node_id}/recovery/review')
    def review(node_id: str, p=Depends(owner)):
        return recovery.review(node_id)

    @app.post('/api/nodes/{node_id}/recovery/stop')
    def stop(node_id: str, body: RecoveryConfirmation, p=Depends(owner)):
        writable()
        result = recovery.stop(node_id, binding_id=body.bindingId, review_hash=body.reviewHash,
                               acknowledge_interruption=body.acknowledgeServiceInterruption,
                               acknowledge_stale_backup=body.acknowledgeBackupMayBeStale)
        audit(p.actor,p.actor.id,'node.recovery.stop',node_id,result['phase'])
        return result

    @app.get('/api/nodes/{node_id}/recovery/current')
    def status(node_id: str, p=Depends(owner)):
        return recovery.status(node_id)

    @app.post('/api/nodes/{node_id}/recovery/{attempt_id}/retry')
    def retry(node_id: str, attempt_id: str, p=Depends(owner)):
        writable()
        result = recovery.retry(node_id, attempt_id)
        audit(p.actor,p.actor.id,'node.recovery.retry',node_id,result['phase'])
        return result
