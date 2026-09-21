"""Durable credential handoff for an already pinned Node, not enrollment.

The current registration and run intent stay intact. Only a verified candidate
credential is published; ambiguous results retain both encrypted credentials.
No network call runs in a SQLite transaction. Saved status is not live health.
"""
from __future__ import annotations

import hashlib
import re
import secrets
import threading
import time
import uuid

from fastapi import Depends
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictStr
from dark_policy import PolicyError
from node_installations import NodeInstallations


def fingerprint(value):
    return hashlib.sha256(value.encode()).hexdigest()


class CredentialRejected(PolicyError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


class NodeCredentials:
    def __init__(self, registry):
        self.registry, self.store, self.cipher = registry, registry.store, registry.cipher
        with self.store.lock:
            # Multiple registry objects may share this live SQLite Store. Their
            # registry-local locks do not serialize the same credential journal.
            # Keep only process-local locks here; durable recovery is still SQL.
            if not hasattr(self.store, '_node_credential_locks'):
                self.store._node_credential_locks = {}
            self._credential_locks = self.store._node_credential_locks
            self.store.db.executescript('''
                CREATE TABLE IF NOT EXISTS remote_node_credentials(
                    attempt_id TEXT PRIMARY KEY,node_id TEXT NOT NULL,binding_id TEXT NOT NULL,
                    agent_id TEXT NOT NULL,installation_id TEXT NOT NULL,origin TEXT NOT NULL,
                    original_enc TEXT NOT NULL,candidate_enc TEXT NOT NULL,candidate_hash TEXT NOT NULL,
                    original_hash TEXT NOT NULL,
                    published_hash TEXT NOT NULL DEFAULT '',operation_revision INTEGER NOT NULL DEFAULT 0,
                    phase TEXT NOT NULL CHECK(phase IN ('pending','rotating','completed')),
                    last_error TEXT NOT NULL DEFAULT '',created_at REAL NOT NULL,updated_at REAL NOT NULL,
                    completed_at REAL NOT NULL DEFAULT 0);
                CREATE UNIQUE INDEX IF NOT EXISTS node_one_credential_handoff
                    ON remote_node_credentials(node_id) WHERE phase!='completed';
                CREATE TRIGGER IF NOT EXISTS credential_handoff_node_update
                    BEFORE UPDATE OF id,origin,token_enc ON remote_nodes
                    WHEN (NEW.id!=OLD.id OR NEW.origin!=OLD.origin OR NEW.token_enc!=OLD.token_enc)
                    AND EXISTS(SELECT 1 FROM remote_node_credentials WHERE node_id=OLD.id AND phase!='completed')
                    BEGIN SELECT RAISE(ABORT,'Resolve pending Node credential handoff before editing its identity'); END;
                CREATE TRIGGER IF NOT EXISTS credential_handoff_node_delete BEFORE DELETE ON remote_nodes
                    WHEN EXISTS(SELECT 1 FROM remote_node_credentials WHERE node_id=OLD.id AND phase!='completed')
                    BEGIN SELECT RAISE(ABORT,'Resolve pending Node credential handoff before deletion'); END;
                CREATE TRIGGER IF NOT EXISTS credential_handoff_installation_update
                    BEFORE UPDATE OF binding_id,node_id,agent_id,installation_id,retired_at ON remote_node_installations
                    WHEN EXISTS(SELECT 1 FROM remote_node_credentials WHERE binding_id=OLD.binding_id AND phase!='completed')
                    BEGIN SELECT RAISE(ABORT,'Resolve pending Node credential handoff before replacement'); END;
                CREATE TRIGGER IF NOT EXISTS credential_handoff_installation_delete BEFORE DELETE ON remote_node_installations
                    WHEN EXISTS(SELECT 1 FROM remote_node_credentials WHERE binding_id=OLD.binding_id AND phase!='completed')
                    BEGIN SELECT RAISE(ABORT,'Resolve pending Node credential handoff before deletion'); END;
            ''')
        self.install_workflow_guards()

    def _credential_operation(self, node_id):
        # Release the Store lock before waiting or performing any network I/O.
        # Different nodes get independent locks. This is not a cross-process lease.
        with self.store.lock:
            return self._credential_locks.setdefault(node_id, threading.RLock())

    def install_workflow_guards(self):
        # Tables may be initialized after the registry, before the first handoff.
        with self.store.lock:
            for table in ('remote_node_replacements', 'remote_node_recoveries'):
                if not self.store.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
                    continue
                for event in ('INSERT', 'UPDATE'):
                    self.store.db.execute(f'''CREATE TRIGGER IF NOT EXISTS credential_handoff_{table}_{event.lower()}
                        BEFORE {event} ON {table}
                        WHEN EXISTS(SELECT 1 FROM remote_node_credentials WHERE node_id=NEW.node_id AND phase!='completed')
                        BEGIN SELECT RAISE(ABORT,'Resolve pending Node credential handoff first'); END''')

    def _open(self, value):
        try:
            return self.cipher.decrypt(value.encode()).decode()
        except Exception as exc:
            raise CredentialRejected('credential_key_unavailable') from exc

    def _row(self, node_id, attempt_id=None):
        if attempt_id is not None and (not isinstance(attempt_id,str) or not re.fullmatch('[0-9a-f]{32}',attempt_id)):
            raise PolicyError('Invalid credential handoff attempt')
        with self.store.lock:
            if attempt_id:
                row = self.store.db.execute('SELECT * FROM remote_node_credentials WHERE node_id=? AND attempt_id=?',
                                            (node_id,attempt_id)).fetchone()
            else:
                row = self.store.db.execute("SELECT * FROM remote_node_credentials WHERE node_id=? "
                    "ORDER BY (phase!='completed') DESC,created_at DESC,attempt_id DESC LIMIT 1", (node_id,)).fetchone()
        return dict(row) if row else None

    def status(self, node_id, attempt_id=None):
        with self.store.lock:
            binding = self.registry.installations.capture(node_id)
            row = self._row(node_id,attempt_id)
            result = {'node_id':node_id,'phase':'not_started','rotated':False,'pending':False,
                      'binding_id':binding['binding_id'],'binding_current':True,'credential_current':False,
                      'status_scope':'saved_credential_handoff','live_state_verified':False}
            if not row:
                if attempt_id:raise PolicyError('Credential handoff not found')
                return result
            result.update({k:row[k] for k in ('attempt_id','binding_id','installation_id','origin','phase',
                                             'last_error','created_at','updated_at','completed_at')})
            result['pending'] = row['phase']!='completed'
            result['rotated'] = row['phase']=='completed'
            result['binding_current'] = all(binding[k]==row[k] for k in ('binding_id','agent_id','installation_id','origin'))
            result['credential_current'] = (result['rotated'] and result['binding_current']
                                             and fingerprint(binding['token_enc'])==row['published_hash'])
            return result

    def _no_conflict(self, node_id):
        db = self.store.db
        queries = (
            ('remote_node_replacements','SELECT 1 FROM remote_node_replacements WHERE node_id=?'),
            ('remote_node_replacement_deployments','SELECT 1 FROM remote_node_replacement_deployments d '
             'JOIN remote_node_installations i ON i.binding_id=d.binding_id '
             'WHERE d.node_id=? AND i.retired_at=0 AND d.activation_hold=1'),
            ('remote_node_recoveries',"SELECT 1 FROM remote_node_recoveries r JOIN remote_node_installations i "
             "ON i.binding_id=r.binding_id WHERE r.node_id=? AND i.retired_at=0 AND r.phase='pending'"),
        )
        for table,query in queries:
            if (db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(table,)).fetchone()
                    and db.execute(query,(node_id,)).fetchone()):
                raise CredentialRejected('credential_conflicting_workflow')

    def begin(self, node_id, candidate, *, binding_id):
        if not isinstance(candidate,str) or not re.fullmatch(r'dkn_[A-Za-z0-9_-]{36,252}',candidate):
            raise PolicyError('Invalid replacement DARK node token')
        self.install_workflow_guards()
        with self._credential_operation(node_id), self.registry._node_operation(node_id), self.store.transaction() as db:
            binding = self.registry.installations.capture(node_id)
            if not binding['installation_id'] or binding['binding_id']!=binding_id:
                raise CredentialRejected('credential_pinned_installation_required')
            self._no_conflict(node_id)
            existing = self._row(node_id)
            if existing and existing['phase']!='completed':
                if (existing['binding_id']!=binding_id or not secrets.compare_digest(self._open(existing['candidate_enc']),candidate)):
                    raise CredentialRejected('credential_resume_saved_operation')
                return self.status(node_id,existing['attempt_id'])
            current = self._open(binding['token_enc'])
            candidate_hash = fingerprint(candidate)
            if secrets.compare_digest(current,candidate):
                if (existing and existing['binding_id']==binding_id and existing['candidate_hash']==candidate_hash):
                    return self.status(node_id,existing['attempt_id'])
                raise CredentialRejected('credential_already_current')
            if db.execute('SELECT 1 FROM remote_node_credentials WHERE binding_id=? AND (candidate_hash=? OR original_hash=?)',
                          (binding_id,candidate_hash,candidate_hash)).fetchone():
                raise CredentialRejected('credential_reuse_refused')
            now, attempt = time.time(), uuid.uuid4().hex
            db.execute('''INSERT INTO remote_node_credentials(attempt_id,node_id,binding_id,agent_id,
                installation_id,origin,original_enc,candidate_enc,candidate_hash,original_hash,phase,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,'pending',?,?)''',
                (attempt,node_id,binding_id,binding['agent_id'],binding['installation_id'],binding['origin'],
                 binding['token_enc'],self.cipher.encrypt(candidate.encode()).decode(),candidate_hash,fingerprint(current),now,now))
        return self.status(node_id,attempt)

    def _assert(self, row):
        latest = self._row(row['node_id'],row['attempt_id'])
        binding = self.registry.installations.capture(row['node_id'])
        if (not latest or latest['phase']=='completed' or latest['operation_revision']!=row['operation_revision']
                or any(latest[k]!=row[k] for k in ('binding_id','installation_id','original_enc','candidate_enc','origin'))
                or any(binding[k]!=row[k] for k in ('binding_id','agent_id','installation_id','origin'))
                or binding['token_enc']!=row['original_enc']):
            raise CredentialRejected('credential_operation_changed')
        self._no_conflict(row['node_id'])

    def _exchange(self, row, credential, path='/node/api/health', body=None):
        from nodes import node_https_request
        with self.store.lock:self._assert(row)
        reply,_ = node_https_request(row['origin'],credential,path,'GET' if body is None else 'POST',body,12.0,
            agent_id=row['agent_id'],installation_id=row['installation_id'],allow_auth_failure=True)
        with self.store.lock:self._assert(row)
        return reply

    @staticmethod
    def _health(row, health, *, for_mutation=False):
        if NodeInstallations.descriptor(health)!=(row['agent_id'],row['installation_id']):
            raise CredentialRejected('credential_installation_changed')
        version = health.get('capabilities',{}).get('credential_rotation')
        if type(version) is not int or version!=1 or (for_mutation and health.get('writes_enabled') is not True):
            raise CredentialRejected('credential_rotation_unsupported_or_readonly')
        # Rotation is not a health repair; stopped/error Xray is still permitted.
        # Independent authenticated identity, not data-plane readiness, is proved.

    def _finish(self, row):
        now = time.time()
        with self.store.transaction() as db:
            self._assert(row)
            db.execute("UPDATE remote_node_credentials SET phase='completed',original_enc='',candidate_enc='',"
                "published_hash=?,last_error='',completed_at=?,updated_at=? WHERE attempt_id=?",
                (fingerprint(row['candidate_enc']),now,now,row['attempt_id']))
            changed = db.execute("UPDATE remote_nodes SET token_enc=?,updated_at=?,last_seen=0,last_latency_ms=0,"
                "last_health='{}',last_error='' WHERE id=? AND token_enc=?",
                (row['candidate_enc'],now,row['node_id'],row['original_enc']))
            if changed.rowcount!=1:raise CredentialRejected('credential_operation_changed')

    def retry(self, node_id, attempt_id):
        from nodes import NodeHTTPError
        with self._credential_operation(node_id), self.registry._node_operation(node_id):
            with self.store.transaction() as db:
                row = self._row(node_id,attempt_id)
                if not row:raise PolicyError('Credential handoff not found')
                if row['phase']=='completed':return self.status(node_id,attempt_id)
                if row['operation_revision']>=2**63-2:raise PolicyError('Credential handoff sequence exhausted')
                self._assert(row)
                row['operation_revision']+=1
                db.execute('UPDATE remote_node_credentials SET operation_revision=?,updated_at=? WHERE attempt_id=?',
                           (row['operation_revision'],time.time(),attempt_id))
            try:
                candidate = self._open(row['candidate_enc'])
                try:
                    self._health(row,self._exchange(row,candidate))
                except NodeHTTPError as exc:
                    if exc.status!=401:raise
                    original = self._open(row['original_enc'])
                    self._health(row,self._exchange(row,original),for_mutation=True)
                    with self.store.transaction() as db:
                        self._assert(row)
                        db.execute("UPDATE remote_node_credentials SET phase='rotating' WHERE attempt_id=?",(attempt_id,))
                    ack = self._exchange(row,original,'/node/api/v1/token/rotate',{'token':candidate})
                    if not isinstance(ack,dict) or ack.get('service')!='DARK XRAY NODE' or ack.get('rotated') is not True:
                        raise CredentialRejected('credential_rotation_unacknowledged')
                    self._health(row,self._exchange(row,candidate))
                self._finish(row)
            except Exception as exc:
                # The remote exception body may contain the credential. Never persist it.
                code = exc.code if isinstance(exc,CredentialRejected) else 'credential_contact_or_verification_failed'
                with self.store.transaction() as db:
                    db.execute("UPDATE remote_node_credentials SET last_error=?,updated_at=? "
                        "WHERE attempt_id=? AND operation_revision=? AND phase!='completed'",
                        (code,time.time(),attempt_id,row['operation_revision']))
            return self.status(node_id,attempt_id)


class RotateBody(BaseModel):
    model_config = ConfigDict(extra='forbid')
    bindingId: StrictStr = Field(pattern='^[0-9a-f]{32}$')
    token: StrictStr = Field(min_length=40,max_length=256)
    confirmRotate: StrictBool


class RetryBody(BaseModel):
    model_config = ConfigDict(extra='forbid')
    confirmRetry: StrictBool


def install_hub_credentials(app, registry, owner, writable, audit):
    coordinator = NodeCredentials(registry)
    app.state.node_credentials = coordinator

    @app.get('/api/nodes/{node_id}/credentials/current')
    def current(node_id: str, p=Depends(owner)):
        return coordinator.status(node_id)

    @app.post('/api/nodes/{node_id}/credentials/rotate')
    def rotate(node_id: str, body: RotateBody, p=Depends(owner)):
        writable()
        if body.confirmRotate is not True:raise PolicyError('Explicit token rotation confirmation required')
        saved = coordinator.begin(node_id,body.token,binding_id=body.bindingId)
        result = coordinator.retry(node_id,saved['attempt_id'])
        audit(p.actor,p.actor.id,'node.credential.rotate',node_id,'attempt='+result['attempt_id']+'; phase='+result['phase'])
        return result

    @app.post('/api/nodes/{node_id}/credentials/{attempt_id}/retry')
    def retry(node_id: str, attempt_id: str, body: RetryBody, p=Depends(owner)):
        writable()
        if body.confirmRetry is not True:raise PolicyError('Explicit token rotation retry confirmation required')
        result = coordinator.retry(node_id,attempt_id)
        audit(p.actor,p.actor.id,'node.credential.retry',node_id,'attempt='+attempt_id+'; phase='+result['phase'])
        return result
