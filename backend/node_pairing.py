"""Durable first enrollment of a fresh Agent through the ordinary Pair Code API.

Pending targets are NOT registered Nodes. Save encrypted credentials and pin the
installation before mutation, then verify the new credential before one atomic
registration/receipt transaction. A saved receipt is not a live health check.
"""
from __future__ import annotations

import json
import re
import secrets
import time
import uuid

from fastapi import Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from dark_policy import PolicyError
from node_installations import NodeInstallations
from node_replacement import parse_pair_code
from node_pairing_cancellation import PairingCancellation


class PairingRejected(PolicyError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def install_pairing_reservations(store):
    """SQLite enforces reservations across registries and older Add/Edit paths.

    Both coordinators call this after their tables exist. Completion removes the
    pending reservation INSIDE the same transaction that registers the binding.
    """
    with store.lock:
        exists = lambda table: store.db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
        if not exists('remote_node_pairings'):
            return
        targets = [
            ('remote_nodes', 'id,origin', 'p.node_id=NEW.id OR p.origin=NEW.origin'),
            ('remote_node_installations', 'installation_id',
             "NEW.installation_id!='' AND p.installation_id=NEW.installation_id"),
        ]
        if exists('remote_node_replacements'):
            targets.append(('remote_node_replacements', 'target_origin,target_installation_id',
                "p.origin=NEW.target_origin OR (NEW.target_installation_id!='' "
                "AND p.installation_id=NEW.target_installation_id)"))
        for table, columns, predicate in targets:
            for kind in ('INSERT', 'UPDATE OF ' + columns):
                suffix = 'insert' if kind == 'INSERT' else 'update'
                store.db.execute(f'''CREATE TRIGGER IF NOT EXISTS pairing_reserves_{table}_{suffix}
                    BEFORE {kind} ON {table}
                    WHEN EXISTS(SELECT 1 FROM remote_node_pairings p WHERE {predicate})
                    BEGIN SELECT RAISE(ABORT,'Target reserved by pending Node pairing'); END''')


class NodePairing(PairingCancellation):
    def __init__(self, registry):
        self.registry, self.store, self.cipher = registry, registry.store, registry.cipher
        with self.store.lock:
            self.store.db.executescript('''
                CREATE TABLE IF NOT EXISTS remote_node_pairings(
                    attempt_id TEXT PRIMARY KEY,node_id TEXT NOT NULL UNIQUE,name TEXT NOT NULL,
                    origin TEXT NOT NULL UNIQUE,installation_id TEXT NOT NULL DEFAULT '',
                    data_address TEXT NOT NULL,priority INTEGER NOT NULL,failover_enabled INTEGER NOT NULL,
                    bootstrap_enc TEXT NOT NULL,candidate_enc TEXT NOT NULL,
                    operation_revision INTEGER NOT NULL DEFAULT 0,
                    phase TEXT NOT NULL DEFAULT 'pending' CHECK(phase IN ('pending','rotating')),
                    last_error TEXT NOT NULL DEFAULT '',created_at REAL NOT NULL,updated_at REAL NOT NULL);
                CREATE UNIQUE INDEX IF NOT EXISTS pairing_reserved_installation
                    ON remote_node_pairings(installation_id) WHERE installation_id!='';
                CREATE TABLE IF NOT EXISTS remote_node_pair_history(
                    attempt_id TEXT PRIMARY KEY,node_id TEXT NOT NULL,binding_id TEXT NOT NULL,
                    installation_id TEXT NOT NULL,name TEXT NOT NULL,origin TEXT NOT NULL,
                    created_at REAL NOT NULL,completed_at REAL NOT NULL);
            ''')
        self._init_cancellation()
        install_pairing_reservations(self.store)

    def _open(self, sealed):
        try:
            return self.cipher.decrypt(sealed.encode()).decode()
        except Exception as exc:
            raise PairingRejected('pairing_credential_unavailable') from exc

    def _pending(self, attempt_id):
        if not isinstance(attempt_id, str) or not re.fullmatch('[0-9a-f]{32}', attempt_id):
            raise PolicyError('Invalid Node pairing attempt')
        with self.store.lock:
            row = self.store.db.execute('SELECT * FROM remote_node_pairings WHERE attempt_id=?',
                                        (attempt_id,)).fetchone()
        return dict(row) if row else None

    @staticmethod
    def _public(row):
        return {**{key: row[key] for key in ('attempt_id','node_id','name','origin','installation_id',
                                             'last_error','created_at','updated_at')},
                'phase': 'cancelling' if row['resolution'] == 'cancelling' else row['phase'],
                'cancelled': False, 'credentials_retained_in_journal': True, 'reservation_released': False,
                'paired': False, 'pair_code_consumed': None, 'registration_current': False,
                'status_scope': 'saved_pairing_operation', 'live_state_verified': False}

    def status(self, attempt_id):
        with self.store.lock:
            row = self._pending(attempt_id)
            if row:
                return self._public(row)
            cancelled = self._cancelled(attempt_id)
            if cancelled is not None:
                return cancelled
            receipt = self.store.db.execute('SELECT * FROM remote_node_pair_history WHERE attempt_id=?',
                                            (attempt_id,)).fetchone()
            if not receipt:
                raise PolicyError('Node pairing attempt not found')
            receipt = dict(receipt)
            current = self.store.db.execute('SELECT i.binding_id,i.installation_id,n.origin FROM '
                'remote_node_installations i JOIN remote_nodes n ON n.id=i.node_id '
                'WHERE i.node_id=? AND i.retired_at=0', (receipt['node_id'],)).fetchone()
            matches = bool(current and current['binding_id'] == receipt['binding_id']
                           and current['installation_id'] == receipt['installation_id']
                           and current['origin'] == receipt['origin'])
            return {**receipt, 'phase': 'paired', 'paired': True, 'pair_code_consumed': True,
                    'registration_current': matches, 'status_scope': 'saved_pairing_receipt',
                    'live_state_verified': False, 'last_error': '',
                    'node': self.registry.get(receipt['node_id']) if matches else None}

    def pending(self):
        with self.store.lock:
            rows = self.store.db.execute('SELECT * FROM remote_node_pairings ORDER BY created_at,attempt_id').fetchall()
            return {'items': [self._public(dict(row)) for row in rows], 'live_state_verified': False}

    def _available(self, db, row):
        if db.execute('SELECT 1 FROM remote_nodes WHERE id=? OR origin=?',
                      (row['node_id'], row['origin'])).fetchone():
            raise PairingRejected('pairing_target_already_registered')
        identity = row['installation_id']
        if identity and db.execute('SELECT 1 FROM remote_node_installations WHERE installation_id=?',
                                   (identity,)).fetchone():
            raise PairingRejected('pairing_installation_already_known')
        if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='remote_node_replacements'").fetchone():
            if db.execute("SELECT 1 FROM remote_node_replacements WHERE target_origin=? "
                          "OR (?!='' AND target_installation_id=?)", (row['origin'], identity, identity)).fetchone():
                raise PairingRejected('pairing_target_reserved_for_replacement')

    def begin(self, code):
        doc = parse_pair_code(code)
        with self.store.transaction() as db:
            if db.execute('SELECT 1 FROM remote_nodes WHERE id=? OR origin=?',
                          (doc['nodeId'], doc['origin'])).fetchone():
                raise HTTPException(409, 'Node ID or Origin is already registered')
            prior = db.execute('SELECT * FROM remote_node_pairings WHERE node_id=? OR origin=?',
                               (doc['nodeId'], doc['origin'])).fetchone()
            if prior:
                expected = (doc['nodeId'],doc['name'],doc['origin'],doc['dataAddress'],doc['priority'],int(doc['failoverEnabled']))
                if (tuple(prior[k] for k in ('node_id','name','origin','data_address','priority','failover_enabled')) != expected
                        or not secrets.compare_digest(self._open(prior['bootstrap_enc']), doc['token'])):
                    raise PolicyError('A different Node pairing owns this identity or endpoint; resume the saved operation')
                return self._public(dict(prior))
            if db.execute('SELECT COUNT(*) FROM remote_node_pairings').fetchone()[0] >= 100:
                raise PolicyError('Resolve pending Node pairings before adding more')
            self._available(db, {'node_id':doc['nodeId'], 'origin':doc['origin'], 'installation_id':''})
            now, attempt = time.time(), uuid.uuid4().hex
            seal = lambda value: self.cipher.encrypt(value.encode()).decode()
            db.execute('''INSERT INTO remote_node_pairings(attempt_id,node_id,name,origin,data_address,
                priority,failover_enabled,bootstrap_enc,candidate_enc,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?)''', (attempt,doc['nodeId'],doc['name'],doc['origin'],doc['dataAddress'],
                doc['priority'],int(doc['failoverEnabled']),seal(doc['token']),seal('dkn_'+secrets.token_urlsafe(48)),now,now))
        return self.status(attempt)

    def _assert(self, row):
        latest = self._pending(row['attempt_id'])
        if not latest or latest['operation_revision'] != row['operation_revision']:
            raise PairingRejected('pairing_operation_superseded')
        if any(latest[k] != row[k] for k in ('node_id','origin','installation_id','bootstrap_enc','candidate_enc','resolution','disposal_enc')):
            raise PairingRejected('pairing_operation_changed')
        self._available(self.store.db, row)

    def _exchange(self, row, credential, path='/node/api/health', method='GET', body=None):
        from nodes import node_https_request
        with self.store.lock:
            self._assert(row)
        result, _ = node_https_request(row['origin'],credential,path,method,body,8.0,
            agent_id=row['node_id'],installation_id=row['installation_id'],allow_auth_failure=True)
        with self.store.lock:
            self._assert(row)
        return result

    def _health(self, row, health):
        try:
            agent_id, installation = NodeInstallations.validate_fresh(health, allow_running=True)
        except PolicyError as exc:
            raise PairingRejected('pairing_fresh_agent_required') from exc
        capability = health.get('capabilities', {}).get('replacement_prepare')
        if type(capability) is not int or capability != 1:
            raise PairingRejected('pairing_guarded_rotation_unsupported')
        if agent_id != row['node_id'] or (row['installation_id'] and row['installation_id'] != installation):
            raise PairingRejected('pairing_installation_changed')
        if health['core'].get('last_error') or (health.get('maintenance') or {}).get('last_error'):
            raise PairingRejected('pairing_agent_not_healthy')
        return installation

    def _finish(self, row):
        """All-or-nothing publication; no network, callback or config deployment."""
        now = time.time()
        with self.store.transaction() as db:
            self._assert(row)
            if row['resolution']:
                raise PairingRejected('pairing_cancellation_in_progress')
            # Release only within this transaction; failures roll this back too.
            db.execute('DELETE FROM remote_node_pairings WHERE attempt_id=?', (row['attempt_id'],))
            db.execute('''INSERT INTO remote_nodes(id,name,origin,token_enc,enabled,created_at,updated_at,
                data_address,priority,failover_enabled) VALUES(?,?,?,?,1,?,?,?,?,?)''',
                (row['node_id'],row['name'],row['origin'],row['candidate_enc'],now,now,
                 row['data_address'],row['priority'],row['failover_enabled']))
            binding = db.execute('SELECT binding_id FROM remote_node_installations WHERE node_id=? AND retired_at=0',
                                 (row['node_id'],)).fetchone()['binding_id']
            db.execute('UPDATE remote_node_installations SET installation_id=? WHERE binding_id=?',
                       (row['installation_id'],binding))
            db.execute('''INSERT INTO remote_node_pair_history
                (attempt_id,node_id,binding_id,installation_id,name,origin,created_at,completed_at)
                VALUES(?,?,?,?,?,?,?,?)''', (row['attempt_id'],row['node_id'],binding,row['installation_id'],
                                           row['name'],row['origin'],row['created_at'],now))

    def retry(self, attempt_id):
        with self._pair_operation(attempt_id):
            with self.store.transaction() as db:
                row = self._pending(attempt_id)
                if row is None:
                    return self.status(attempt_id)  # Historical receipt, never re-enable or contact the target.
                if row['resolution']:
                    raise PairingRejected('pairing_cancellation_in_progress')
                self._available(db, row)
                if row['operation_revision'] >= 2**63 - 2:
                    raise PolicyError('Node pairing operation sequence exhausted')
                row['operation_revision'] += 1
                db.execute('UPDATE remote_node_pairings SET operation_revision=?,updated_at=? WHERE attempt_id=?',
                           (row['operation_revision'],time.time(),attempt_id))
            try:
                bootstrap, candidate = self._open(row['bootstrap_enc']), self._open(row['candidate_enc'])
                if not row['installation_id']:
                    health = self._exchange(row, bootstrap)
                    installation = self._health(row, health)
                    with self.store.transaction() as db:
                        self._assert(row)
                        enriched = {**row, 'installation_id': installation}
                        self._available(db, enriched)
                        db.execute('UPDATE remote_node_pairings SET installation_id=? WHERE attempt_id=?',
                                   (installation,attempt_id))
                        row = enriched
                from nodes import NodeHTTPError
                try:
                    # Only a verified HTTPS 401 permits use of the saved bootstrap.
                    # Timeouts/TLS errors/identity mismatch are never that evidence.
                    health = self._exchange(row, candidate)
                except NodeHTTPError as exc:
                    if exc.status != 401:
                        raise
                    self._health(row, self._exchange(row, bootstrap))
                    with self.store.transaction() as db:
                        self._assert(row)
                        db.execute("UPDATE remote_node_pairings SET phase='rotating',updated_at=? WHERE attempt_id=?",
                                   (time.time(),attempt_id))
                    # Reuse Agent's freshness-checked rotation, not unguarded legacy.
                    self._exchange(row,bootstrap,'/node/api/v1/replacement/rotate-token','POST',{'token':candidate})
                    health = self._exchange(row, candidate)
                self._health(row, health)
                self._finish(row)
            except Exception as exc:
                error = exc.code if isinstance(exc,PairingRejected) else 'pairing_contact_or_verification_failed'
                with self.store.transaction() as db:
                    db.execute('UPDATE remote_node_pairings SET last_error=?,updated_at=? '
                               'WHERE attempt_id=? AND operation_revision=?',
                               (error,time.time(),attempt_id,row['operation_revision']))
            return self.status(attempt_id)


class PairCodeBody(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    code: str = Field(min_length=16, max_length=4096)


class PairRetryBody(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    confirmRetry: bool


class PairCancelBody(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    confirmCancel: bool
    acknowledgeCredentialReset: bool


def install_hub_pairing(app, registry, owner, writable, audit):
    pairing = NodePairing(registry)
    app.state.pairing = pairing

    @app.post('/api/nodes/pair')
    def pair(body: PairCodeBody, p=Depends(owner)):
        writable()
        saved = pairing.begin(body.code)
        result = pairing.retry(saved['attempt_id'])
        audit(p.actor,p.actor.id,'node.pair',result['node_id'],result['phase'])
        return result

    @app.get('/api/nodes/pairings')
    def pending(p=Depends(owner)):
        return pairing.pending()

    @app.get('/api/nodes/pairings/{attempt_id}')
    def status(attempt_id: str, p=Depends(owner)):
        return pairing.status(attempt_id)

    @app.post('/api/nodes/pairings/{attempt_id}/retry')
    def retry(attempt_id: str, body: PairRetryBody, p=Depends(owner)):
        writable()
        if body.confirmRetry is not True:
            raise PolicyError('Explicit Node pairing retry confirmation required')
        result = pairing.retry(attempt_id)
        audit(p.actor,p.actor.id,'node.pair.retry',result['node_id'],result['phase'])
        return result

    @app.post('/api/nodes/pairings/{attempt_id}/cancel')
    def cancel(attempt_id: str, body: PairCancelBody, p=Depends(owner)):
        writable()
        result = pairing.cancel(attempt_id, confirm_cancel=body.confirmCancel,
                                acknowledge_credential_reset=body.acknowledgeCredentialReset)
        audit(p.actor,p.actor.id,'node.pair.cancel',result['node_id'],result['phase'])
        return result
