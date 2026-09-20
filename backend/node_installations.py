"""Installation fencing behind stable logical Node IDs (Hub only).

This is the storage/transport foundation, NOT a public replacement endpoint.
Enrollment, credential handoff, endpoint cutover and crash recovery of those
steps belong to the following checkpoint. Do not call replace_verified with
untrusted browser input: its descriptor must come from authenticated HTTPS.
"""
from __future__ import annotations

import contextlib
import contextvars
import functools
import json
import re
import time
import uuid

from dark_policy import NAME_RE, PolicyError


class StaleInstallation(PolicyError):
    """The response belongs to a retired installation; do not publish it."""


def installation_operation(function):
    @functools.wraps(function)
    def wrapped(self, node_id, *args, **kwargs):
        registry = getattr(self, 'registry', self)
        with registry.installations.operation(node_id):
            return function(self, node_id, *args, **kwargs)
    return wrapped


class NodeInstallations:
    def __init__(self, registry):
        self.registry, self.store = registry, registry.store
        self._contexts = contextvars.ContextVar('dark_node_installations', default=None)
        with self.store.lock:
            self.store.db.executescript('''
            CREATE TABLE IF NOT EXISTS remote_node_installations(
              binding_id TEXT PRIMARY KEY,node_id TEXT NOT NULL,generation INTEGER NOT NULL,
              agent_id TEXT NOT NULL,installation_id TEXT NOT NULL DEFAULT '',
              created_at REAL NOT NULL,retired_at REAL NOT NULL DEFAULT 0,
              retirement_json TEXT NOT NULL DEFAULT '{}',UNIQUE(node_id,generation));
            CREATE UNIQUE INDEX IF NOT EXISTS node_one_active_installation
              ON remote_node_installations(node_id) WHERE retired_at=0;
            CREATE UNIQUE INDEX IF NOT EXISTS node_known_installation
              ON remote_node_installations(installation_id) WHERE installation_id!='';
            CREATE TRIGGER IF NOT EXISTS node_installation_on_insert AFTER INSERT ON remote_nodes
            BEGIN
              INSERT INTO remote_node_installations(binding_id,node_id,generation,agent_id,created_at)
              VALUES(lower(hex(randomblob(16))),NEW.id,
                (SELECT COALESCE(MAX(generation),0)+1 FROM remote_node_installations WHERE node_id=NEW.id),
                NEW.id,NEW.created_at);
            END;
            ''')
        with self.store.transaction() as db:
            for row in db.execute('SELECT id FROM remote_nodes').fetchall():
                self.ensure(db, row['id'])

    @staticmethod
    def ensure(db, node_id):
        row = db.execute('SELECT * FROM remote_node_installations WHERE node_id=? AND retired_at=0',
                         (node_id,)).fetchone()
        if row:
            return dict(row)
        generation = db.execute('SELECT COALESCE(MAX(generation),0)+1 FROM remote_node_installations WHERE node_id=?',
                                (node_id,)).fetchone()[0]
        db.execute('INSERT INTO remote_node_installations(binding_id,node_id,generation,agent_id,created_at) VALUES(?,?,?,?,?)',
                   (uuid.uuid4().hex, node_id, generation, node_id, time.time()))
        return dict(db.execute('SELECT * FROM remote_node_installations WHERE node_id=? AND retired_at=0',
                               (node_id,)).fetchone())

    def capture(self, node_id):
        with self.store.lock:
            node = self.store.db.execute('SELECT * FROM remote_nodes WHERE id=?', (node_id,)).fetchone()
            active = self.store.db.execute('SELECT * FROM remote_node_installations WHERE node_id=? AND retired_at=0',
                                           (node_id,)).fetchone()
            if not node or not active:
                raise StaleInstallation('Node installation is no longer registered')
            return {**dict(node), **dict(active)}

    def current(self, node_id):
        contexts = self._contexts.get() or {}
        return contexts.get(node_id) or self.capture(node_id)

    def public_status(self, node_id):
        row = self.capture(node_id)
        return {key: row[key] for key in ('binding_id','generation','agent_id','installation_id')}

    def assert_current(self, db, expected):
        row = db.execute('SELECT binding_id,installation_id FROM remote_node_installations '
                         'WHERE node_id=? AND retired_at=0', (expected['node_id'],)).fetchone()
        if not row or row['binding_id'] != expected['binding_id'] or row['installation_id'] != expected['installation_id']:
            raise StaleInstallation('Retired or changed Node installation; discard this response and retry')
        node=db.execute('SELECT origin FROM remote_nodes WHERE id=?',(expected['node_id'],)).fetchone()
        if not node or node['origin']!=expected['origin']:
            raise StaleInstallation('Node installation endpoint changed; discard this response')

    @contextlib.contextmanager
    def operation(self, node_id):
        contexts = self._contexts.get() or {}
        if node_id in contexts:
            with self.store.lock:
                self.assert_current(self.store.db, contexts[node_id])
            yield contexts[node_id]
            return
        snapshot = self.capture(node_id)
        token = self._contexts.set({**contexts, node_id:snapshot})
        try:
            yield snapshot
            with self.store.lock:
                self.assert_current(self.store.db, self.current(node_id))
        finally:
            self._contexts.reset(token)

    @contextlib.contextmanager
    def transaction(self, node_id):
        expected = self.current(node_id)
        with self.store.transaction() as db:
            self.assert_current(db, expected)
            yield db

    @staticmethod
    def descriptor(health):
        capabilities = health.get('capabilities') if isinstance(health, dict) else None
        version = capabilities.get('installation_identity') if isinstance(capabilities, dict) else None
        agent_id = health.get('node_id') if isinstance(health, dict) else None
        installation_id = health.get('installation_id') if isinstance(health, dict) else None
        if (not isinstance(health, dict) or health.get('service')!='DARK XRAY NODE'
            or health.get('agent_only') is not True or type(version) is not int or version!=1
            or not isinstance(agent_id,str) or not NAME_RE.fullmatch(agent_id)
            or not isinstance(installation_id,str) or not re.fullmatch(r'[0-9a-f]{32}',installation_id)):
            raise PolicyError('Agent lacks a valid installation identity v1')
        return agent_id, installation_id

    def observe(self, node_id, health):
        """Pin first authenticated identity. Never auto-adopt a changed identity."""
        expected = self.current(node_id)
        capabilities = health.get('capabilities') if isinstance(health, dict) else None
        offered = isinstance(capabilities, dict) and 'installation_identity' in capabilities
        if not expected['installation_id'] and not offered:
            return  # Explicit legacy compatibility, not installation protection.
        agent_id, installation_id = self.descriptor(health)
        if agent_id != expected['agent_id']:
            raise PolicyError('Agent identity mismatch; explicit Node replacement required')
        if expected['installation_id'] and installation_id != expected['installation_id']:
            raise PolicyError('Node installation changed; explicit Node replacement required')
        if expected['installation_id']:
            return
        with self.transaction(node_id) as db:
            used = db.execute('SELECT 1 FROM remote_node_installations WHERE installation_id=? AND binding_id<>?',
                              (installation_id, expected['binding_id'])).fetchone()
            if used:
                raise PolicyError('Installation already belongs to a registered or retired Node')
            db.execute('UPDATE remote_node_installations SET installation_id=? WHERE binding_id=?',
                       (installation_id, expected['binding_id']))
        # Enrich this operation's same binding after successful first pinning.
        # Other requests that started unpinned are now stale, not silently trusted.
        contexts = self._contexts.get()
        if contexts is not None and node_id in contexts:
            self._contexts.set({**contexts, node_id:{**expected,'installation_id':installation_id}})

    def history(self, node_id):
        with self.store.lock:
            return [dict(row) for row in self.store.db.execute(
                'SELECT * FROM remote_node_installations WHERE node_id=? ORDER BY generation', (node_id,))]

    def replace_verified(self, node_id, *, expected_binding_id, health, origin, token, data_address):
        """Commit a verified *fresh/stopped* installation, disabled for cutover.

        INTERNAL foundation only: callers must authenticate the descriptor,
        complete credential handoff and validate target TLS before invoking.
        This method does not perform enrollment, rotate tokens, stop the old VPS,
        change DNS, activate subscriptions or promise recovery of missing traffic.
        """
        agent_id, installation_id = self.descriptor(health)
        core, desired = health.get('core'), health.get('desired_state')
        receipt=health.get('control_receipt')
        ordered=health.get('capabilities',{}).get('ordered_control')
        if (health.get('writes_enabled') is not True or type(ordered) is not int or ordered!=1
            or not isinstance(receipt,dict) or receipt.get('persisted') is not False
            or not isinstance(core,dict) or core.get('state')!='stopped'
            or not isinstance(desired,dict) or type(desired.get('appliedRevision')) is not int
            or desired['appliedRevision']!=0 or type(health.get('inbounds')) is not int or health['inbounds']!=0
            or type(health.get('managed_clients')) is not int or health['managed_clients']!=0):
            raise PolicyError('Replacement must be a fresh, stopped, unassigned Node installation')
        # Existing URL/TLS/SSRF validation is retained, not relaxed for replacement.
        from nodes import validate_origin, validate_data_address
        origin = validate_origin(origin)
        data_address = validate_data_address(data_address, origin)
        if not isinstance(token,str) or not token.startswith('dkn_') or not 40<=len(token)<=256 or not token.isascii():
            raise PolicyError('Invalid replacement Node credential')
        credential = self.registry.cipher.encrypt(token.encode()).decode()
        now = time.time()
        with self.store.transaction() as db:
            old = self.capture(node_id)
            if old['binding_id']!=expected_binding_id:
                raise StaleInstallation('Replacement was prepared for an obsolete installation')
            if db.execute('SELECT 1 FROM remote_node_installations WHERE installation_id=?', (installation_id,)).fetchone():
                raise PolicyError('Cannot reuse a registered or retired installation')
            control = db.execute('SELECT * FROM remote_node_control WHERE node_id=?', (node_id,)).fetchone()
            state = db.execute('SELECT * FROM remote_node_desired_state WHERE node_id=?', (node_id,)).fetchone()
            usage = [dict(r) for r in db.execute('SELECT * FROM remote_node_client_usage WHERE node_id=?', (node_id,))]
            retirement = {'origin':old['origin'],'data_address':old['data_address'],
                          'last_seen':old['last_seen'],'usage':usage,
                          'control':dict(control) if control else {},
                          'desired_revision':int(state['revision']) if state else 0,
                          'traffic_tail_complete':False,'old_stop_confirmed':False}
            generation = int(old['generation'])+1
            if generation>=2**63:
                raise PolicyError('Node installation generation exhausted')
            db.execute('UPDATE remote_node_installations SET retired_at=?,retirement_json=? WHERE binding_id=?',
                       (now,json.dumps(retirement),old['binding_id']))
            db.execute('INSERT INTO remote_node_installations(binding_id,node_id,generation,agent_id,installation_id,created_at) '
                       'VALUES(?,?,?,?,?,?)', (uuid.uuid4().hex,node_id,generation,agent_id,installation_id,now))
            db.execute("UPDATE remote_nodes SET origin=?,token_enc=?,data_address=?,enabled=0,last_seen=0,last_latency_ms=0,"
                       "last_health='{}',last_error='',updated_at=? WHERE id=?", (origin,credential,data_address,now,node_id))
            # Keep central accumulated usage AND ledger event sequence. The new
            # freshly empty installation begins at zero; count its first byte.
            db.execute('UPDATE remote_node_client_usage SET raw_up=0,raw_down=0,initialized=1,last_seen=0 WHERE node_id=?', (node_id,))
            # A client assigned but never sampled on the old Node also starts
            # from a known zero on this clean replacement, not a first-use gap.
            if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='managed_clients'").fetchone():
                for email in self.registry._allowed_traffic_clients(node_id):
                    db.execute('INSERT OR IGNORE INTO remote_node_client_usage(node_id,client_id,initialized) VALUES(?,?,1)',
                               (node_id,email))
            for table in ('remote_node_ips','remote_node_devices','remote_node_security_state'):
                db.execute('DELETE FROM '+table+' WHERE node_id=?', (node_id,))
            db.execute("UPDATE remote_node_inbounds SET remote_inbound_id=0,last_sync=0,last_error='',updated_at=? WHERE node_id=?", (now,node_id))
            # Never carry a queued old Restart/Start into a different installation.
            db.execute('DELETE FROM remote_node_control WHERE node_id=?', (node_id,))
            db.execute("INSERT INTO remote_node_control(node_id,revision,command_id,action,updated_at) VALUES(?,1,?,'stop',?)",
                       (node_id,uuid.uuid4().hex,now))
            if state:
                value = self.registry._open_desired_payload(state['desired_json'])
                value = {**value,'nodeId':agent_id,'desiredRunning':False}
                _, digest = self.registry._desired_payload(value)
                revision = int(state['revision'])+1
                if revision>=2**63:
                    raise PolicyError('Node desired state sequence exhausted')
                sealed = self.registry._seal_desired_payload(value)
                db.execute("UPDATE remote_node_desired_state SET revision=?,desired_hash=?,desired_json=?,updated_at=?,"
                           "applied_revision=0,applied_hash='',applied_at=0,last_error='' WHERE node_id=?",
                           (revision,digest,sealed,now,node_id))
        return self.public_status(node_id)
