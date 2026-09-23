"""Stage a committed replacement while it remains disabled and durably stopped.

This is an owner-initiated maintenance operation, NOT activation or a WAN probe.
It uses the same verified HTTPS/installation-pinned transport without temporarily
setting enabled=True. A durable hold prevents normal enable/Start paths from
racing deployment. The subsequent explicit activation coordinator must release
that hold only after revalidation; this module never releases it.
"""
from __future__ import annotations

import hashlib
import json
import re
import time

from dark_policy import PolicyError
from node_installations import NodeInstallations


class DeploymentRejected(PolicyError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def assert_deployment_allows(db, node_id, action):
    """Called inside the caller's write transaction; no network or secrets."""
    from node_recovery import assert_recovery_allows
    assert_recovery_allows(db,node_id,action)
    if action not in {'enable', 'start', 'restart'}:
        return
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND "
                      "name='remote_node_replacement_deployments'").fetchone():
        return
    row = db.execute('SELECT 1 FROM remote_node_replacement_deployments d '
                     'JOIN remote_node_installations i ON i.binding_id=d.binding_id '
                     'WHERE d.node_id=? AND i.retired_at=0 AND d.activation_hold=1', (node_id,)).fetchone()
    if row:
        raise PolicyError('Replacement activation is held; complete explicit cutover before enable/Start')


class ReplacementDeployment:
    def __init__(self, registry, replacements, payload_provider, refresh_policy=lambda: None):
        self.registry, self.store = registry, registry.store
        self.replacements = replacements
        self.payload_provider, self.refresh_policy = payload_provider, refresh_policy
        with self.store.lock:
            self.store.db.execute('''CREATE TABLE IF NOT EXISTS remote_node_replacement_deployments(
                attempt_id TEXT PRIMARY KEY,node_id TEXT NOT NULL,binding_id TEXT NOT NULL UNIQUE,
                installation_id TEXT NOT NULL,origin TEXT NOT NULL,credential_hash TEXT NOT NULL,
                operation_revision INTEGER NOT NULL,control_revision INTEGER NOT NULL,command_id TEXT NOT NULL,
                phase TEXT NOT NULL DEFAULT 'pending',last_error TEXT NOT NULL DEFAULT '',
                source_hash TEXT NOT NULL DEFAULT '',desired_revision INTEGER NOT NULL DEFAULT 0,
                desired_hash TEXT NOT NULL DEFAULT '',validated_hash TEXT NOT NULL DEFAULT '',
                activation_hold INTEGER NOT NULL DEFAULT 1 CHECK(activation_hold IN (0,1)),
                verified_at REAL NOT NULL DEFAULT 0,updated_at REAL NOT NULL)''')

    @staticmethod
    def _credential_hash(binding):
        # Detect a changed credential without retaining a second decryptable copy.
        return hashlib.sha256(binding['token_enc'].encode()).hexdigest()

    def _source_hash(self, db, node_id):
        """Pure local snapshot of configuration/policy inputs; never collect stats.

        Conservative invalidation includes other clients/owners. TLS file bytes
        are checked by the payload compiler during stage, not by this GET hash.
        """
        queries = (
            ('inbounds', 'SELECT id,body FROM core_inbounds ORDER BY id', ()),
            ('clients', 'SELECT email,body,inbounds FROM core_clients ORDER BY email', ()),
            ('sections', 'SELECT name,body FROM core_sections ORDER BY name', ()),
            ('policy', 'SELECT * FROM clients ORDER BY id', ()),
            ('owners', 'SELECT * FROM owners ORDER BY id', ()),
            ('assignments', 'SELECT local_inbound_id FROM remote_node_inbounds WHERE node_id=? '
                            'ORDER BY local_inbound_id', (node_id,)),
        )
        value = {name: [tuple(row) for row in db.execute(query, args)] for name, query, args in queries}
        return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                         allow_nan=False).encode()).hexdigest()

    def _row(self, node_id, attempt_id):
        if not isinstance(attempt_id, str) or not re.fullmatch('[0-9a-f]{32}', attempt_id):
            raise PolicyError('Invalid replacement attempt identity')
        with self.store.lock:
            row = self.store.db.execute('SELECT * FROM remote_node_replacement_deployments '
                                        'WHERE node_id=? AND attempt_id=?', (node_id, attempt_id)).fetchone()
        return dict(row) if row else None

    def _assert(self, db, row, binding):
        self.registry.installations.assert_current(db, binding)
        current = db.execute('SELECT * FROM remote_node_replacement_deployments WHERE attempt_id=?',
                             (row['attempt_id'],)).fetchone()
        node = db.execute('SELECT enabled,token_enc FROM remote_nodes WHERE id=?', (row['node_id'],)).fetchone()
        control = self.registry.commands.status(row['node_id'])
        if (not current or current['operation_revision'] != row['operation_revision']
                or current['binding_id'] != row['binding_id'] or current['activation_hold'] != 1):
            raise DeploymentRejected('deployment_superseded')
        if not node or node['enabled'] or self._credential_hash(node) != row['credential_hash']:
            raise DeploymentRejected('node_or_credential_changed')
        if (control['action'] != 'stop' or control['revision'] != row['control_revision']
                or control['command_id'] != row['command_id']):
            raise DeploymentRejected('control_changed')

    def _exchange(self, row, binding, path, method='GET', body=None, timeout=12.0):
        from nodes import node_https_request
        with self.store.lock:
            self._assert(self.store.db, row, binding)
        try:
            credential = self.registry.cipher.decrypt(binding['token_enc'].encode()).decode()
        except Exception as exc:
            raise DeploymentRejected('credential_unavailable') from exc
        try:
            result = node_https_request(binding['origin'], credential, path, method, body, timeout,
                                        agent_id=binding['agent_id'], installation_id=binding['installation_id'])
        except (PolicyError, OSError, ValueError) as exc:
            # The shared ACK publisher persists errors in desired-state metadata.
            # Sanitize HERE too, before an untrusted remote body can reach it.
            raise DeploymentRejected('target_contact_or_identity_failed') from exc
        with self.store.lock:
            self._assert(self.store.db, row, binding)
        return result

    @staticmethod
    def _stopped(health, binding):
        if NodeInstallations.descriptor(health) != (binding['agent_id'], binding['installation_id']):
            raise DeploymentRejected('installation_mismatch')
        capabilities, core, control = health.get('capabilities', {}), health.get('core'), health.get('run_control')
        version = capabilities.get('ordered_control')
        if (type(version) is not int or version != 1 or health.get('writes_enabled') is not True
                or not isinstance(core, dict) or core.get('state') != 'stopped' or core.get('last_error')
                or not isinstance(control, dict) or control.get('effective_running') is not False
                or control.get('manual_stop') is not True):
            raise DeploymentRejected('target_not_durably_stopped')
        if (health.get('maintenance') or {}).get('last_error'):
            raise DeploymentRejected('target_maintenance_error')

    def _begin(self, node_id, attempt_id, binding_id):
        with self.store.lock:
            if self.store.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='remote_node_replacement_activations'").fetchone():
                active=self.store.db.execute('SELECT phase FROM remote_node_replacement_activations WHERE attempt_id=?',(attempt_id,)).fetchone()
                if active and active['phase'] in {'starting','stopping'}:
                    raise PolicyError('Resolve pending activation with retry or pause before restaging')
        receipt = self.replacements._terminal(node_id, attempt_id)
        if (not receipt or receipt['phase'] != 'committed' or not receipt['binding_current']
                or receipt['committed_binding_id'] != binding_id):
            raise PolicyError('Confirm the current committed replacement binding')
        with self.store.transaction() as db:
            binding = self.registry.installations.capture(node_id)
            if (binding['binding_id'] != binding_id or binding['origin'] != receipt['target_origin']
                    or binding['installation_id'] != receipt['target_installation_id'] or binding['enabled']):
                raise PolicyError('Replacement must be the committed, disabled installation')
            control = self.registry.commands.status(node_id)
            if not control['persisted'] or control['action'] != 'stop':
                raise PolicyError('A durable Stop intent is required before staging')
            old = self._row(node_id, attempt_id)
            revision = int(old['operation_revision'] if old else 0) + 1
            if revision >= 2**63:
                raise PolicyError('Deployment operation sequence exhausted')
            db.execute('''INSERT INTO remote_node_replacement_deployments
                (attempt_id,node_id,binding_id,installation_id,origin,credential_hash,operation_revision,
                 control_revision,command_id,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(attempt_id) DO UPDATE SET operation_revision=excluded.operation_revision,
                credential_hash=excluded.credential_hash,control_revision=excluded.control_revision,
                command_id=excluded.command_id,phase='pending',last_error='',verified_at=0,
                updated_at=excluded.updated_at''',
                (attempt_id,node_id,binding_id,binding['installation_id'],binding['origin'],
                 self._credential_hash(binding),revision,control['revision'],control['command_id'],time.time()))
        return self._row(node_id, attempt_id), binding

    def status(self, node_id, attempt_id):
        receipt = self.replacements._terminal(node_id, attempt_id)
        if not receipt or receipt['phase'] != 'committed':
            raise PolicyError('Committed replacement not found')
        row = self._row(node_id, attempt_id)
        result = {'attempt_id':attempt_id, 'node_id':node_id, 'status_scope':'stopped_configuration_snapshot',
                  'phase':'not_started', 'configuration_staged':False, 'snapshot_current':False,
                  'service_activated':False, 'cutover_performed':False, 'requires_revalidation':True,
                  'old_stop_confirmed':False, 'traffic_tail_complete':False}
        if row is None:
            return result
        public = ('binding_id','installation_id','phase','last_error','desired_revision','desired_hash',
                  'validated_hash','verified_at','updated_at')
        result.update({key:row[key] for key in public})
        result['activation_held'] = bool(row['activation_hold'])
        with self.store.lock:
            try:
                binding = self.registry.installations.capture(node_id)
                self._assert(self.store.db, row, binding)
                desired = self.store.db.execute('SELECT * FROM remote_node_desired_state WHERE node_id=?',
                                                (node_id,)).fetchone()
                current = (receipt['binding_current'] and binding['binding_id'] == row['binding_id']
                    and binding['installation_id'] == row['installation_id'] and binding['origin'] == row['origin']
                    and desired is not None and desired['revision'] == row['desired_revision']
                    and desired['applied_revision'] == row['desired_revision']
                    and desired['desired_hash'] == row['desired_hash'] == desired['applied_hash']
                    and not desired['last_error'] and self._source_hash(self.store.db, node_id) == row['source_hash'])
            except PolicyError:
                current = False
        result['snapshot_current'] = bool(current)
        result['configuration_staged'] = row['phase'] == 'staged' and not row['last_error'] and bool(current)
        return result

    def stage(self, node_id, attempt_id, *, binding_id):
        if not isinstance(binding_id, str) or not re.fullmatch('[0-9a-f]{32}', binding_id):
            raise PolicyError('Invalid replacement binding confirmation')
        with self.registry._node_operation(node_id):
            row, binding = self._begin(node_id, attempt_id, binding_id)
            try:
                with self.registry.installations.operation(node_id):
                    self._stage(row, binding)
            except Exception as exc:
                # No remote exception text or payload (which may contain keys) in the journal.
                error = exc.code if isinstance(exc, DeploymentRejected) else 'deployment_verification_failed'
                with self.store.transaction() as db:
                    db.execute("UPDATE remote_node_replacement_deployments SET phase='pending',last_error=?,"
                               'updated_at=? WHERE attempt_id=? AND operation_revision=?',
                               (error,time.time(),attempt_id,row['operation_revision']))
            return self.status(node_id, attempt_id)

    def _stage(self, row, binding):
        node_id = row['node_id']
        request = lambda path, method='GET', body=None, timeout=12.0: self._exchange(row,binding,path,method,body,timeout)
        # Always re-prove the same Stop, including after a lost apply response.
        command = {'nodeId':binding['agent_id'], 'revision':row['control_revision'],
                   'commandId':row['command_id'], 'action':'stop'}
        ack, _ = request('/node/api/v1/control', 'POST', command)
        if (not isinstance(ack,dict) or ack.get('service')!='DARK XRAY NODE' or ack.get('node_id')!=binding['agent_id']
                or type(ack.get('revision')) is not int or ack['revision']!=command['revision']
                or ack.get('commandId')!=command['commandId'] or ack.get('action')!='stop'
                or ack.get('applied') is not True or not isinstance(ack.get('engine'),dict)
                or ack['engine'].get('state')!='stopped'):
            raise DeploymentRejected('stop_unacknowledged')
        health, _ = request('/node/api/health')
        self._stopped(health, binding)
        with self.store.transaction() as db:
            self._assert(db,row,binding)
            db.execute("UPDATE remote_node_control SET applied_revision=revision,applied_at=?,last_error='' WHERE node_id=?",
                       (time.time(),node_id))
        self.refresh_policy()
        with self.store.lock:
            source_hash = self._source_hash(self.store.db,node_id)
        payload = self.payload_provider(node_id)
        with self.store.lock:
            self._assert(self.store.db,row,binding)
            if self._source_hash(self.store.db,node_id)!=source_hash:
                raise DeploymentRejected('configuration_changed')
        self.registry.set_desired_state(node_id,payload)
        state = self.registry.desired_state(node_id)
        if not state['payload'].get('assignments') or state['payload'].get('desiredRunning') is not False:
            raise DeploymentRejected('configuration_not_staged_safe')
        with self.store.transaction() as db:
            self._assert(db,row,binding)
            db.execute('UPDATE remote_node_replacement_deployments SET source_hash=?,desired_revision=?,desired_hash=? '
                       'WHERE attempt_id=?', (source_hash,state['revision'],state['hash'],row['attempt_id']))
        # Reuse the ordinary exact-ACK/assignment transaction. Only its transport
        # is specialized: no temporary enable and no mutation of registry._request.
        self.registry._sync_desired_state_locked(node_id,state,
            _requester=lambda _node,path,method='GET',body=None,timeout=8.0: request(path,method,body,timeout))
        validation, _ = request('/node/api/core/validate','POST',{})
        proof = validation.get('engine') if isinstance(validation,dict) else None
        if (not isinstance(proof,dict) or proof.get('validated') is not True
                or not isinstance(proof.get('hash'),str) or not re.fullmatch('[0-9a-f]{64}',proof['hash'])):
            raise DeploymentRejected('configuration_validation_failed')
        traffic, _ = request('/node/api/mirrors/traffic')
        expected = {client['sourceEmail'] for assignment in state['payload']['assignments'] for client in assignment['clients']}
        items = traffic.get('items') if isinstance(traffic,dict) else None
        # A confirmed compensating Stop may have metered real activation bytes.
        # Keep strict zero checks for never-started candidates, not for a paused
        # activation. No counters are reset; accept/account the stopped snapshot.
        with self.store.lock:
            has_activation=self.store.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='remote_node_replacement_activations'").fetchone()
            paused=self.store.db.execute("SELECT 1 FROM remote_node_replacement_activations WHERE attempt_id=? AND binding_id=? AND phase='paused'",(row['attempt_id'],binding['binding_id'])).fetchone() if has_activation else None
        if (not isinstance(items,list) or any(not isinstance(x,dict) or set(x)!={'sourceEmail','up','down'}
                or not isinstance(x['sourceEmail'],str) or type(x['up']) is not int or x['up']<0
                or type(x['down']) is not int or x['down']<0
                or (not paused and (x['up']!=0 or x['down']!=0)) for x in items)
                or len(items)!=len(expected) or {x['sourceEmail'] for x in items}!=expected):
            raise DeploymentRejected('unexpected_target_counters')
        if paused:
            with self.store.lock:
                self._assert(self.store.db,row,binding)
                self.registry.apply_traffic_snapshot(node_id,items)
        health, _ = request('/node/api/health')
        self._stopped(health,binding)
        desired, receipt = health.get('desired_state'), health.get('control_receipt')
        if (not isinstance(desired,dict) or type(desired.get('appliedRevision')) is not int
                or desired['appliedRevision']!=state['revision'] or desired.get('appliedHash')!=state['hash']
                or desired.get('lastError') or not isinstance(receipt,dict) or receipt.get('phase')!='applied'
                or type(receipt.get('revision')) is not int or receipt['revision']!=row['control_revision']
                or receipt.get('command_id')!=row['command_id'] or receipt.get('action')!='stop'
                or type(health.get('inbounds')) is not int or health['inbounds']!=len(state['payload']['assignments'])
                or type(health.get('managed_clients')) is not int or health['managed_clients']!=len(expected)):
            raise DeploymentRejected('target_state_unverified')
        self.refresh_policy()
        # Catch edited input and changed TLS bytes; never call the compiler while
        # holding a DB transaction or use an old payload as proof of current policy.
        final_payload = self.payload_provider(node_id)
        self.registry.set_desired_state(node_id,final_payload)
        with self.store.transaction() as db:
            self._assert(db,row,binding)
            current = db.execute('SELECT * FROM remote_node_desired_state WHERE node_id=?',(node_id,)).fetchone()
            if (self._source_hash(db,node_id)!=source_hash or current['revision']!=state['revision']
                    or current['desired_hash']!=state['hash'] or current['applied_revision']!=state['revision']
                    or current['applied_hash']!=state['hash'] or current['last_error']):
                raise DeploymentRejected('configuration_changed')
            db.execute("UPDATE remote_node_replacement_deployments SET phase='staged',last_error='',validated_hash=?,"
                       'verified_at=?,updated_at=? WHERE attempt_id=?',
                       (proof['hash'],time.time(),time.time(),row['attempt_id']))
