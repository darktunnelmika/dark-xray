"""Explicit replacement activation. A receipt is not a WAN/data-plane proof.

Keep the Node disabled and the durable hold closed until exact Start, config,
identity, current policy and accounting checks pass. Retries reuse a journaled
Start; never Restart. A compensating/owner-requested Stop is journaled too.
DNS, tunnels, public host addresses and the retired VPS are never modified.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
import uuid

from dark_policy import PolicyError
from node_installations import NodeInstallations


class ActivationRejected(PolicyError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     allow_nan=False).encode()).hexdigest()


class ReplacementActivation:
    def __init__(self, deployment):
        self.deployment = deployment
        self.registry, self.store = deployment.registry, deployment.store
        with self.store.lock:
            self.store.db.execute('''CREATE TABLE IF NOT EXISTS remote_node_replacement_activations(
                attempt_id TEXT PRIMARY KEY,node_id TEXT NOT NULL,binding_id TEXT NOT NULL UNIQUE,
                installation_id TEXT NOT NULL,origin TEXT NOT NULL,credential_hash TEXT NOT NULL,
                deployment_revision INTEGER NOT NULL,review_hash TEXT NOT NULL,
                source_hash TEXT NOT NULL,endpoint_hash TEXT NOT NULL,
                desired_revision INTEGER NOT NULL,desired_hash TEXT NOT NULL,validated_hash TEXT NOT NULL,
                start_revision INTEGER NOT NULL,start_id TEXT NOT NULL,
                stop_revision INTEGER NOT NULL DEFAULT 0,stop_id TEXT NOT NULL DEFAULT '',
                phase TEXT NOT NULL,last_error TEXT NOT NULL DEFAULT '',
                updated_at REAL NOT NULL,completed_at REAL NOT NULL DEFAULT 0)''')

    def _row(self, node, attempt):
        if not isinstance(attempt, str) or not re.fullmatch('[0-9a-f]{32}', attempt):
            raise PolicyError('Invalid activation attempt')
        with self.store.lock:
            row = self.store.db.execute('SELECT * FROM remote_node_replacement_activations '
                                        'WHERE node_id=? AND attempt_id=?', (node, attempt)).fetchone()
        return dict(row) if row else None

    def _endpoints(self, node):
        """Expose only public endpoint fields, not UUIDs/private keys or bearer tokens."""
        with self.store.lock:
            saved = self.store.db.execute('SELECT data_address,priority,failover_enabled FROM remote_nodes '
                                          'WHERE id=?', (node,)).fetchone()
            if not saved:
                raise PolicyError('Node not found')
            assignments = [int(r[0]) for r in self.store.db.execute(
                'SELECT local_inbound_id FROM remote_node_inbounds WHERE node_id=? ORDER BY local_inbound_id', (node,))]
            raw = self.store.db.execute("SELECT body FROM core_sections WHERE name='hosts'").fetchone()
            hosts = json.loads(raw[0]) if raw else []
            public_hosts = [{k: h[k] for k in ('inboundId','runtime','address','port','enable','remark','endpointType') if k in h}
                            for h in hosts if h.get('inboundId') in assignments]
            ports = []
            for inbound_id in assignments:
                r = self.store.db.execute('SELECT body FROM core_inbounds WHERE id=?', (inbound_id,)).fetchone()
                if not r:
                    raise PolicyError('Assigned inbound is missing')
                inbound = json.loads(r[0])
                ports.append({'inbound_id': inbound_id, 'port': inbound['port'],
                              'protocol': inbound['protocol'], 'enabled': inbound.get('enable', True)})
        return {'node_data_address': saved['data_address'], 'priority': saved['priority'],
                'failover_enabled': bool(saved['failover_enabled']), 'inbounds': ports, 'hosts': public_hosts,
                'addresses_changed_automatically': False, 'dns_and_tunnel_verified': False,
                'operator_must_verify_dns_tunnel_and_client_addresses': True,
                'direct_clients_may_connect_as_soon_as_start_is_sent': True}

    def _review_data(self, node, attempt):
        state = self.deployment.status(node, attempt)
        if not state['configuration_staged']:
            raise PolicyError('Stage the stopped replacement configuration before activation review')
        row = self.deployment._row(node, attempt)
        endpoints = self._endpoints(node)
        proof = {k: row[k] for k in ('binding_id','installation_id','origin','credential_hash',
                                    'source_hash','desired_revision','desired_hash','validated_hash',
                                    'control_revision','command_id')}
        proof['endpoint_hash'] = _hash(endpoints)
        return row, endpoints, _hash(proof)

    def review(self, node, attempt, *, binding_id):
        with self.registry._node_operation(node):
            old = self._row(node, attempt)
            if old and old['phase'] in {'starting','stopping','activated'}:
                raise PolicyError('Resolve the existing activation; do not restage or issue another Start')
            staged = self.deployment.stage(node, attempt, binding_id=binding_id)
            if not staged['configuration_staged']:
                return {'review_ready': False, 'deployment': staged, 'service_activated': False}
            row, endpoints, digest = self._review_data(node, attempt)
            binding = self.registry.installations.capture(node)
            health, _ = self.deployment._exchange(row, binding, '/node/api/health')
            version = (health.get('capabilities') or {}).get('conditional_activation')
            if type(version) is not int or version != 1:
                raise PolicyError('Agent must support conditional activation v1; update the Agent first')
            # Health I/O may have raced an endpoint/config/credential edit.
            if self._review_data(node, attempt)[2] != digest:
                raise PolicyError('Activation review changed; review the replacement again')
            return {'review_ready': True, 'review_hash': digest, 'binding_id': binding_id,
                    'attempt_id': attempt, 'endpoints': endpoints, 'service_activated': False,
                    'network_verified': False, 'old_stop_confirmed': False, 'traffic_tail_complete': False}

    def _base_assert(self, db, row, binding):
        self.registry.installations.assert_current(db, binding)
        active = db.execute('SELECT * FROM remote_node_replacement_activations WHERE attempt_id=?',
                            (row['attempt_id'],)).fetchone()
        deploy = db.execute('SELECT * FROM remote_node_replacement_deployments WHERE attempt_id=?',
                            (row['attempt_id'],)).fetchone()
        node = db.execute('SELECT enabled,token_enc FROM remote_nodes WHERE id=?', (row['node_id'],)).fetchone()
        if (not active or active['start_id'] != row['start_id'] or active['phase'] != row['phase']
                or not deploy or deploy['binding_id'] != row['binding_id'] or deploy['activation_hold'] != 1
                or deploy['operation_revision'] != row['deployment_revision']):
            raise ActivationRejected('activation_superseded')
        if (binding['binding_id'] != row['binding_id'] or binding['installation_id'] != row['installation_id']
                or binding['origin'] != row['origin'] or not node or node['enabled']
                or self.deployment._credential_hash(node) != row['credential_hash']):
            raise ActivationRejected('installation_or_credential_changed')
        return deploy

    def _assert(self, db, row, binding, *, inputs=True):
        self._base_assert(db, row, binding)
        control = self.registry.commands.status(row['node_id'])
        action = 'start' if row['phase'] == 'starting' else 'stop'
        if (control['action'] != action or control['revision'] != row[action+'_revision']
                or control['command_id'] != row[action+'_id']):
            raise ActivationRejected('control_changed')
        if inputs:
            desired = db.execute('SELECT * FROM remote_node_desired_state WHERE node_id=?', (row['node_id'],)).fetchone()
            if (not desired or desired['revision'] != row['desired_revision']
                    or desired['applied_revision'] != row['desired_revision']
                    or desired['desired_hash'] != row['desired_hash'] or desired['applied_hash'] != row['desired_hash']
                    or desired['last_error'] or self.deployment._source_hash(db,row['node_id']) != row['source_hash']
                    or _hash(self._endpoints(row['node_id'])) != row['endpoint_hash']):
                raise ActivationRejected('activation_inputs_changed')

    def _fresh_inputs(self, row, binding):
        self.deployment.refresh_policy()
        payload = self.deployment.payload_provider(row['node_id'])
        payload = {**payload, 'nodeId': binding['agent_id'], 'desiredRunning': False}
        if self.registry._desired_payload(payload)[1] != row['desired_hash']:
            raise ActivationRejected('activation_inputs_changed')
        with self.store.lock:
            self._assert(self.store.db, row, binding)

    def _exchange(self, row, binding, path, method='GET', body=None):
        from nodes import node_https_request
        with self.store.lock:
            self._assert(self.store.db,row,binding,inputs=row['phase']=='starting')
        try:
            credential = self.registry.cipher.decrypt(binding['token_enc'].encode()).decode()
            result = node_https_request(binding['origin'], credential, path, method, body, 12.0,
                                        agent_id=binding['agent_id'], installation_id=binding['installation_id'])
        except (PolicyError,OSError,ValueError) as exc:
            raise ActivationRejected('target_contact_or_identity_failed') from exc
        with self.store.lock:
            self._assert(self.store.db,row,binding,inputs=row['phase']=='starting')
        return result

    @staticmethod
    def _ack(doc, command, state):
        if (not isinstance(doc,dict) or doc.get('service')!='DARK XRAY NODE'
                or doc.get('node_id')!=command['nodeId'] or type(doc.get('revision')) is not int
                or doc['revision']!=command['revision'] or doc.get('commandId')!=command['commandId']
                or doc.get('action')!=command['action'] or doc.get('applied') is not True
                or not isinstance(doc.get('engine'),dict) or doc['engine'].get('state')!=state):
            raise ActivationRejected('command_unacknowledged')

    def _begin(self, node, attempt, digest):
        with self.store.transaction() as db:
            deploy, endpoints, current = self._review_data(node, attempt)
            if current != digest:
                raise PolicyError('Activation review is stale; review the replacement again')
            binding = self.registry.installations.capture(node)
            self.deployment._assert(db, deploy, binding)
            revision = self.registry.commands.status(node)['revision'] + 1
            if revision >= 2**63:
                raise PolicyError('Node command sequence exhausted')
            # Staging proved a stopped zero-counter target (or metered a
            # confirmed pause). Seed newly assigned users before first Start so
            # their first report is not discarded as an unknown baseline.
            payload=self.registry.desired_state(node)['payload']
            emails={c['sourceEmail'] for a in payload['assignments'] for c in a['clients']}
            for email in emails:
                db.execute('''INSERT INTO remote_node_client_usage
                    (node_id,client_id,raw_up,raw_down,initialized,last_seen) VALUES(?,?,0,0,1,?)
                    ON CONFLICT(node_id,client_id) DO UPDATE SET
                    raw_up=CASE WHEN initialized=0 THEN 0 ELSE raw_up END,
                    raw_down=CASE WHEN initialized=0 THEN 0 ELSE raw_down END,initialized=1''',
                    (node,email,time.time()))
            identity, now = uuid.uuid4().hex, time.time()
            # This is the only authorized Start insertion while the hold is closed.
            # Persist it BEFORE I/O. Do not release the hold just to call record().
            db.execute("UPDATE remote_node_control SET action='start',revision=?,command_id=?,"
                       "updated_at=?,last_error='' WHERE node_id=?", (revision,identity,now,node))
            db.execute('DELETE FROM remote_node_replacement_activations WHERE attempt_id=?', (attempt,))
            db.execute('''INSERT INTO remote_node_replacement_activations
                (attempt_id,node_id,binding_id,installation_id,origin,credential_hash,deployment_revision,
                 review_hash,source_hash,endpoint_hash,desired_revision,desired_hash,validated_hash,
                 start_revision,start_id,phase,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'starting',?)''',
                (attempt,node,binding['binding_id'],binding['installation_id'],binding['origin'],
                 deploy['credential_hash'],deploy['operation_revision'],digest,deploy['source_hash'],_hash(endpoints),
                 deploy['desired_revision'],deploy['desired_hash'],deploy['validated_hash'],revision,identity,now))
        return self._row(node,attempt), binding

    def _error(self, row, code):
        with self.store.transaction() as db:
            db.execute('UPDATE remote_node_replacement_activations SET last_error=?,updated_at=? '
                       'WHERE attempt_id=? AND start_id=? AND phase=?',
                       (code,time.time(),row['attempt_id'],row['start_id'],row['phase']))

    def _traffic(self, row, binding):
        traffic, _ = self._exchange(row,binding,'/node/api/mirrors/traffic')
        payload = self.registry.desired_state(row['node_id'])['payload']
        expected = {c['sourceEmail'] for a in payload.get('assignments',[]) for c in a['clients']}
        items = traffic.get('items') if isinstance(traffic,dict) else None
        if (not isinstance(items,list) or any(not isinstance(x,dict) or set(x)!={'sourceEmail','up','down'}
                or not isinstance(x['sourceEmail'],str) or type(x['up']) is not int or type(x['down']) is not int
                or x['up']<0 or x['down']<0 for x in items)
                or len(items)!=len(expected) or {x['sourceEmail'] for x in items}!=expected):
            raise ActivationRejected('target_counters_unverified')
        # Meter without holding a transaction over I/O. Accept only our own local
        # accounting change as the new snapshot; other edits are still fenced.
        with self.store.lock:
            self._assert(self.store.db,row,binding,inputs=row['phase']=='starting')
            self.registry.apply_traffic_snapshot(row['node_id'],items)
            updated = self.deployment._source_hash(self.store.db,row['node_id'])
            with self.store.transaction() as db:
                self._base_assert(db,row,binding)
                db.execute('UPDATE remote_node_replacement_activations SET source_hash=? WHERE attempt_id=?',
                           (updated,row['attempt_id']))
            row['source_hash'] = updated

    def _run(self, row, binding):
        self._fresh_inputs(row,binding)
        command = {'nodeId':binding['agent_id'],'revision':row['start_revision'],
                   'commandId':row['start_id'],'action':'start','desiredRevision':row['desired_revision'],
                   'desiredHash':row['desired_hash'],'validatedHash':row['validated_hash']}
        ack, _ = self._exchange(row,binding,'/node/api/v1/control/activate','POST',command)
        self._ack(ack,command,'running')
        if (ack['engine'].get('applied_hash')!=row['validated_hash'] or ack['engine'].get('dirty') is not False
                or ack['engine'].get('last_error') or ack['engine'].get('statistics_error')):
            raise ActivationRejected('target_state_unverified')
        self._traffic(row,binding)
        self._fresh_inputs(row,binding)
        health, ms = self._exchange(row,binding,'/node/api/health')
        if NodeInstallations.descriptor(health) != (binding['agent_id'],binding['installation_id']):
            raise ActivationRejected('target_state_unverified')
        core, desired, receipt, control = (health.get(k) or {} for k in
                                         ('core','desired_state','control_receipt','run_control'))
        state = self.registry.desired_state(row['node_id'])
        expected = {c['sourceEmail'] for a in state['payload']['assignments'] for c in a['clients']}
        if (core.get('state')!='running' or core.get('dirty') is not False or core.get('last_error')
                or control.get('effective_running') is not True or control.get('manual_stop') is not False
                or type(desired.get('appliedRevision')) is not int or desired['appliedRevision']!=row['desired_revision']
                or desired.get('appliedHash')!=row['desired_hash'] or desired.get('lastError')
                or receipt.get('phase')!='applied' or receipt.get('action')!='start'
                or type(receipt.get('revision')) is not int or receipt['revision']!=row['start_revision']
                or receipt.get('command_id')!=row['start_id'] or health.get('writes_enabled') is not True
                or (health.get('maintenance') or {}).get('last_error')
                or type(health.get('inbounds')) is not int or health['inbounds']!=len(state['payload']['assignments'])
                or type(health.get('managed_clients')) is not int or health['managed_clients']!=len(expected)):
            raise ActivationRejected('target_state_unverified')
        self._fresh_inputs(row,binding)
        # One local transaction publishes the receipt, Start ACK, hold release
        # and Node enable. A later retry reads the receipt, never enables again.
        # Store a constructed proof, not arbitrary keys or diagnostic text
        # returned by a remote HTTP peer. Normal monitoring can refresh metrics.
        safe_health={'service':'DARK XRAY NODE','agent_only':True,'node_id':binding['agent_id'],
            'installation_id':binding['installation_id'],
            'capabilities':{'ordered_control':1,'installation_identity':1,'conditional_activation':1},
            'core':{'state':'running','dirty':False,'last_error':''},
            'run_control':{'effective_running':True,'manual_stop':False,'desired_running':True},
            'desired_state':{'appliedRevision':row['desired_revision'],'appliedHash':row['desired_hash'],'lastError':''},
            'control_receipt':{'phase':'applied','action':'start','revision':row['start_revision'],'command_id':row['start_id']},
            'inbounds':len(state['payload']['assignments']),'managed_clients':len(expected),'writes_enabled':True}
        self._finish(row,binding,safe_health,ms)

    def _finish(self, row, binding, health, ms):
        with self.store.transaction() as db:
            self._assert(db,row,binding)
            now = time.time()
            db.execute("UPDATE remote_node_control SET applied_revision=revision,applied_at=?,last_error='' WHERE node_id=?", (now,row['node_id']))
            db.execute("UPDATE remote_node_replacement_deployments SET activation_hold=0,phase='activated',updated_at=? WHERE attempt_id=?", (now,row['attempt_id']))
            db.execute("UPDATE remote_node_replacement_activations SET phase='activated',last_error='',completed_at=?,updated_at=? WHERE attempt_id=?", (now,now,row['attempt_id']))
            db.execute("UPDATE remote_nodes SET enabled=1,last_health=?,last_seen=?,last_latency_ms=?,last_error='',failure_count=0,updated_at=? WHERE id=?", (json.dumps(health),now,max(1,int(ms)),now,row['node_id']))

    def activate(self, node, attempt, *, binding_id, review_hash, confirm_start,
                 accept_endpoint_responsibility, accept_unconfirmed_old_server, accept_unreported_traffic):
        if (not isinstance(binding_id,str) or not re.fullmatch('[0-9a-f]{32}',binding_id)
                or not isinstance(review_hash,str) or not re.fullmatch('[0-9a-f]{64}',review_hash)
                or any(v is not True for v in (confirm_start,accept_endpoint_responsibility,
                                               accept_unconfirmed_old_server,accept_unreported_traffic))):
            raise PolicyError('Explicit Start, endpoint, old-server and traffic acknowledgements are required')
        with self.registry._node_operation(node):
            row = self._row(node,attempt)
            if row and row['phase'] in {'activated','starting','stopping'}:
                if row['binding_id']!=binding_id or row['review_hash']!=review_hash:
                    raise PolicyError('Confirm the same activation attempt; do not create another Start')
                if row['phase']=='activated':
                    return self.status(node,attempt)
                if row['phase']=='stopping':
                    return self.pause(node,attempt,binding_id=binding_id,confirm_stop=True)
                binding = self.registry.installations.capture(node)
            else:
                if self._review_data(node,attempt)[2]!=review_hash:
                    raise PolicyError('Activation review is stale; review the replacement again')
                checked = self.review(node,attempt,binding_id=binding_id)
                if not checked['review_ready'] or checked['review_hash']!=review_hash:
                    raise PolicyError('Activation inputs changed; review the replacement again')
                row, binding = self._begin(node,attempt,review_hash)
            try:
                with self.registry.installations.operation(node):
                    self._run(row,binding)
            except Exception as exc:
                code = exc.code if isinstance(exc,ActivationRejected) else 'activation_verification_failed'
                self._error(row,code)
                if code in {'activation_inputs_changed','control_changed'}:
                    # New intent/policy wins. Keep the Node unpublished and
                    # journal a compensating Stop before any attempt to stop.
                    return self.pause(node,attempt,binding_id=binding_id,confirm_stop=True)
            return self.status(node,attempt)

    def pause(self, node, attempt, *, binding_id, confirm_stop):
        if confirm_stop is not True:
            raise PolicyError('Explicit Stop confirmation is required')
        with self.registry._node_operation(node):
            row = self._row(node,attempt)
            if not row or row['binding_id']!=binding_id:
                raise PolicyError('Activation binding not found')
            if row['phase']=='activated':
                raise PolicyError('Activation already completed; use the ordinary Node Stop control')
            if row['phase']=='paused':
                return self.status(node,attempt)
            binding = self.registry.installations.capture(node)
            with self.store.transaction() as db:
                self._base_assert(db,row,binding)
                control = self.registry.commands.status(node)
                if row['phase']=='starting':
                    if control['action']=='stop':
                        revision, identity = control['revision'], control['command_id']
                    elif control['command_id']==row['start_id'] and control['revision']==row['start_revision']:
                        revision, identity = control['revision']+1, uuid.uuid4().hex
                        if revision>=2**63:raise PolicyError('Node command sequence exhausted')
                        db.execute("UPDATE remote_node_control SET action='stop',revision=?,command_id=?,updated_at=?,last_error='' WHERE node_id=?", (revision,identity,time.time(),node))
                    else:
                        raise PolicyError('A newer command superseded this activation')
                    db.execute("UPDATE remote_node_replacement_activations SET phase='stopping',stop_revision=?,stop_id=?,updated_at=? WHERE attempt_id=?", (revision,identity,time.time(),attempt))
            row = self._row(node,attempt)
            try:
                with self.registry.installations.operation(node):
                    command = {'nodeId':binding['agent_id'],'revision':row['stop_revision'],
                               'commandId':row['stop_id'],'action':'stop'}
                    ack, _ = self._exchange(row,binding,'/node/api/v1/control','POST',command)
                    self._ack(ack,command,'stopped')
                    health, _ = self._exchange(row,binding,'/node/api/health')
                    self.deployment._stopped(health,binding)
                    self._traffic(row,binding)
                    with self.store.transaction() as db:
                        self._assert(db,row,binding,inputs=False)
                        db.execute("UPDATE remote_node_control SET applied_revision=revision,applied_at=?,last_error='' WHERE node_id=?", (time.time(),node))
                        db.execute("UPDATE remote_node_replacement_activations SET phase='paused',last_error='',updated_at=? WHERE attempt_id=?", (time.time(),attempt))
            except Exception as exc:
                code = exc.code if isinstance(exc,ActivationRejected) else 'pause_unconfirmed'
                self._error(row,code)
            return self.status(node,attempt)

    def status(self, node, attempt):
        row = self._row(node,attempt)
        if not row:
            raise PolicyError('Activation has not started')
        public = {k:row[k] for k in ('attempt_id','node_id','binding_id','phase','last_error',
                                    'start_revision','start_id','stop_revision','stop_id','updated_at','completed_at')}
        current = self.registry.installations.capture(node)
        control = self.registry.commands.status(node)
        live = self.registry.get(node)
        binding_current = current['binding_id']==row['binding_id'] and current['installation_id']==row['installation_id'] and current['origin']==row['origin']
        try:
            health = json.loads(live.get('last_health') or '{}')
        except (ValueError,TypeError):
            health = {}
        reported = health.get('core') or {} if isinstance(health,dict) else {}
        runtime_block = self.registry._runtime_block_reason({**live,'health':health,'control':control,
            'desired_state':self.registry.desired_state(node,include_payload=False)})
        public.update(status_scope='activation_receipt_and_last_observation',
            activation_completed=row['phase']=='activated',binding_current=binding_current,
            service_activated=bool(row['phase']=='activated' and binding_current and live['enabled']
                and control['command_id']==row['start_id'] and not control['pending']
                and not live['last_error'] and time.time()-float(live['last_seen'])<180
                and reported.get('state')=='running' and not runtime_block),
            activation_held=row['phase']!='activated',requires_retry=row['phase'] in {'starting','stopping'},
            target_may_be_running=row['phase'] in {'starting','stopping','activated'},
            requires_new_review=row['phase']=='paused',network_verified=False,
            dns_or_tunnel_changed=False,old_stop_confirmed=False,traffic_tail_complete=False)
        return public
