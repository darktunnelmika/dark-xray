"""Durable preparation of a replacement Agent, without changing the live Node.

Only a Pair Code is accepted from the owner. Identity/health come from pinned,
authenticated HTTPS, never from a browser descriptor. An encrypted credential
journal survives lost replies and Hub recreation. Preparation alone does not
replace the live binding, deploy configuration, move endpoints or stop the old
VPS. The companion resolution module implements explicit binding commit and
candidate disposal; service activation and endpoint cutover remain separate.
"""
from __future__ import annotations

import base64
import json
import re
import secrets
import time
import uuid

from dark_policy import NAME_RE, PolicyError
from node_installations import NodeInstallations
from node_replacement_resolution import ReplacementResolution


class PreparationChanged(PolicyError):
    pass


class PreparationRejected(PolicyError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate Pair Code field')
        result[key] = value
    return result


def parse_pair_code(code: str) -> dict:
    if not isinstance(code, str):
        raise PolicyError('Invalid DARK Node pair code')
    code = code.strip()
    if not code.startswith('DXN1.') or not re.fullmatch(r'[A-Za-z0-9_-]{1,3800}', code[5:]):
        raise PolicyError('Invalid DARK Node pair code')
    try:
        encoded = code[5:]
        raw = base64.b64decode(encoded + '=' * (-len(encoded) % 4), altchars=b'-_', validate=True)
        doc = json.loads(raw.decode('utf-8'), object_pairs_hook=_unique_object)
    except (ValueError, UnicodeError) as exc:
        raise PolicyError('Malformed DARK Node pair code') from exc
    required = {'schema','nodeId','name','origin','token','dataAddress','priority','failoverEnabled'}
    if (not isinstance(doc, dict) or set(doc) != required or type(doc['schema']) is not int
            or doc['schema'] != 1 or not isinstance(doc['nodeId'], str)
            or not NAME_RE.fullmatch(doc['nodeId']) or not isinstance(doc['name'], str)
            or not 1 <= len(doc['name']) <= 128 or not isinstance(doc['origin'], str)
            or not isinstance(doc['dataAddress'], str) or type(doc['priority']) is not int
            or not 1 <= doc['priority'] <= 1000 or type(doc['failoverEnabled']) is not bool
            or not isinstance(doc['token'], str)
            or not re.fullmatch(r'dkn_[A-Za-z0-9_-]{36,252}', doc['token'])):
        raise PolicyError('Invalid DARK Node pair payload')
    from nodes import validate_origin, validate_data_address
    doc['origin'] = validate_origin(doc['origin'])
    doc['dataAddress'] = validate_data_address(doc['dataAddress'], doc['origin'])
    return doc


class NodeReplacement(ReplacementResolution):
    def __init__(self, registry):
        self.registry, self.store, self.cipher = registry, registry.store, registry.cipher
        with self.store.lock:
            self.store.db.executescript('''
            CREATE TABLE IF NOT EXISTS remote_node_replacements(
              attempt_id TEXT PRIMARY KEY,node_id TEXT NOT NULL UNIQUE,
              source_binding_id TEXT NOT NULL,source_origin TEXT NOT NULL,
              target_origin TEXT NOT NULL UNIQUE,target_agent_id TEXT NOT NULL,
              target_installation_id TEXT NOT NULL DEFAULT '',data_address TEXT NOT NULL,
              bootstrap_enc TEXT NOT NULL,candidate_enc TEXT NOT NULL,
              phase TEXT NOT NULL DEFAULT 'pending' CHECK(phase IN ('pending','rotating','prepared')),
              operation_revision INTEGER NOT NULL DEFAULT 0,
              created_at REAL NOT NULL,updated_at REAL NOT NULL,prepared_at REAL NOT NULL DEFAULT 0,
              last_error TEXT NOT NULL DEFAULT '');
            CREATE UNIQUE INDEX IF NOT EXISTS replacement_reserved_installation
              ON remote_node_replacements(target_installation_id) WHERE target_installation_id!='';
            ''')
        self._init_resolution()

    def begin(self, node_id: str, code: str) -> dict:
        doc = parse_pair_code(code)
        snapshot = self.registry.installations.capture(node_id)
        with self.store.transaction() as db:
            self.registry.installations.assert_current(db, snapshot)
            existing = db.execute('SELECT * FROM remote_node_replacements WHERE node_id=?', (node_id,)).fetchone()
            if existing:
                # A repeated consumed Pair Code is an identity lookup, not a new
                # credential change. Never generate another token for this retry.
                row = dict(existing)
                if (row['source_binding_id'] != snapshot['binding_id'] or row['source_origin'] != snapshot['origin']
                        or row['target_origin'] != doc['origin'] or row['target_agent_id'] != doc['nodeId']
                        or row['data_address'] != doc['dataAddress']
                        or not secrets.compare_digest(self._open(row['bootstrap_enc']), doc['token'])):
                    raise PolicyError('A different replacement preparation already exists; resolve it before starting another')
                return self._public(row)
            if db.execute('SELECT 1 FROM remote_nodes WHERE origin=?', (doc['origin'],)).fetchone():
                raise PolicyError('Replacement endpoint already belongs to a registered Node')
            if db.execute('SELECT 1 FROM remote_node_replacements WHERE target_origin=?', (doc['origin'],)).fetchone():
                raise PolicyError('Replacement endpoint is reserved by another preparation')
            now = time.time()
            attempt_id = uuid.uuid4().hex
            candidate = 'dkn_' + secrets.token_urlsafe(48)
            db.execute('''INSERT INTO remote_node_replacements
                (attempt_id,node_id,source_binding_id,source_origin,target_origin,target_agent_id,
                 data_address,bootstrap_enc,candidate_enc,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?)''',
                (attempt_id,node_id,snapshot['binding_id'],snapshot['origin'],doc['origin'],doc['nodeId'],
                 doc['dataAddress'],self._seal(doc['token']),self._seal(candidate),now,now))
        return self.status(node_id, attempt_id)

    def _seal(self, value):
        return self.cipher.encrypt(value.encode()).decode()

    def _open(self, value):
        try:
            return self.cipher.decrypt(value.encode()).decode()
        except Exception as exc:
            raise PreparationRejected('credential_unavailable') from exc

    def _get(self, node_id, attempt_id):
        if not isinstance(attempt_id, str) or not re.fullmatch(r'[0-9a-f]{32}', attempt_id):
            raise PolicyError('Invalid replacement attempt identity')
        with self.store.lock:
            row = self.store.db.execute('SELECT * FROM remote_node_replacements WHERE attempt_id=? AND node_id=?',
                                        (attempt_id,node_id)).fetchone()
        if not row:
            raise PolicyError('Replacement preparation not found')
        return dict(row)

    @staticmethod
    def _public(row):
        fields = ('attempt_id','node_id','source_binding_id','target_origin','target_agent_id',
                  'target_installation_id','data_address','phase','created_at','updated_at','prepared_at','last_error')
        return {**{key:row[key] for key in fields},
                'phase':row.get('resolution') or row['phase'],
                'binding_committed':False,'service_activated':False,
                'prepared':row['phase'] == 'prepared' and not row.get('resolution') and not row['last_error'],
                'cutover_performed':False,'requires_cutover_confirmation':not bool(row.get('resolution')),
                'credentials_retained_in_journal':True}

    def status(self, node_id, attempt_id):
        # Pure read, including when authentication to either VPS is impossible.
        terminal=self._terminal(node_id,attempt_id)
        if terminal is not None:return terminal
        try:row=self._get(node_id,attempt_id)
        except PolicyError:
            terminal=self._terminal(node_id,attempt_id)
            if terminal is not None:return terminal
            raise
        result=self._public(row)
        with self.store.lock:
            active=self.store.db.execute('SELECT i.binding_id,n.origin FROM remote_node_installations i '
                'JOIN remote_nodes n ON n.id=i.node_id WHERE i.node_id=? AND i.retired_at=0',(node_id,)).fetchone()
        source_current=bool(active and active['binding_id']==row['source_binding_id'] and active['origin']==row['source_origin'])
        return {**result,'source_current':source_current,'prepared':result['prepared'] and source_current,
                'requires_revalidation':True}

    def _assert_current(self, db, row):
        active = db.execute('SELECT i.binding_id,n.origin FROM remote_node_installations i '
                            'JOIN remote_nodes n ON n.id=i.node_id WHERE i.node_id=? AND i.retired_at=0',
                            (row['node_id'],)).fetchone()
        if row.get('resolution')!='cancelling' and (not active or active['binding_id'] != row['source_binding_id'] or active['origin'] != row['source_origin']):
            raise PreparationRejected('source_changed')
        current = db.execute('SELECT operation_revision FROM remote_node_replacements WHERE attempt_id=?',
                             (row['attempt_id'],)).fetchone()
        if not current or current['operation_revision'] != row['operation_revision']:
            raise PreparationChanged('A newer preparation operation superseded this result')

    def _update(self, row, **values):
        allowed = {'phase','last_error','target_installation_id','prepared_at'}
        if not set(values) <= allowed:
            raise ValueError('Invalid preparation update')
        with self.store.transaction() as db:
            self._assert_current(db, row)
            identity=values.get('target_installation_id',row['target_installation_id'])
            if identity and (self.store.db.execute('SELECT 1 FROM remote_node_installations WHERE installation_id=?',(identity,)).fetchone()
                    or self.store.db.execute('SELECT 1 FROM remote_nodes WHERE origin=?',(row['target_origin'],)).fetchone()
                    or self.store.db.execute('SELECT 1 FROM remote_node_replacements WHERE target_installation_id=? AND attempt_id<>?',
                                            (identity,row['attempt_id'])).fetchone()):
                raise PreparationRejected('target_already_registered')
            assignments = ','.join(key + '=?' for key in values)
            db.execute('UPDATE remote_node_replacements SET ' + assignments + ',updated_at=? WHERE attempt_id=?',
                       (*values.values(),time.time(),row['attempt_id']))
        row.update(values)

    def _exchange(self, row, credential, path='/node/api/health', method='GET', body=None):
        with self.store.lock:
            self._assert_current(self.store.db, row)
        from nodes import node_https_request
        document, _ms = node_https_request(row['target_origin'], credential, path, method, body, 8.0,
                                          agent_id=row['target_agent_id'],
                                          installation_id=row['target_installation_id'],allow_auth_failure=True)
        with self.store.lock:
            self._assert_current(self.store.db, row)
        return document

    def _validate_health(self, row, health, *, require_idle=False):
        try:
            agent_id, installation_id = NodeInstallations.validate_fresh(health, allow_running=not require_idle)
        except PolicyError as exc:
            raise PreparationRejected('target_not_fresh_or_unsupported') from exc
        if (agent_id != row['target_agent_id'] or
                (row['target_installation_id'] and installation_id != row['target_installation_id'])):
            raise PreparationRejected('target_identity_changed')
        capability=health.get('capabilities',{}).get('replacement_prepare')
        if type(capability) is not int or capability!=1:
            raise PreparationRejected('target_preparation_unsupported')
        # A stopped process with autostart intent is not a safe idle candidate.
        run_control = health.get('run_control')
        if (not isinstance(run_control, dict) or type(run_control.get('effective_running')) is not bool
                or (require_idle and run_control['effective_running'])
                or health['core'].get('last_error')
                or (health.get('maintenance') or {}).get('last_error')):
            raise PreparationRejected('target_not_idle')
        with self.store.lock:
            self._assert_current(self.store.db, row)
            used = self.store.db.execute('SELECT 1 FROM remote_node_installations WHERE installation_id=?',
                                         (installation_id,)).fetchone()
            reserved = self.store.db.execute('SELECT 1 FROM remote_node_replacements '
                                            'WHERE target_installation_id=? AND attempt_id<>?',
                                            (installation_id,row['attempt_id'])).fetchone()
            endpoint = self.store.db.execute('SELECT 1 FROM remote_nodes WHERE origin=?',
                                            (row['target_origin'],)).fetchone()
            if used or reserved or endpoint:
                raise PreparationRejected('target_already_registered')
        return installation_id

    def resume(self, node_id, attempt_id):
        # Single-Hub operations serialize; operation_revision also fences delayed
        # writes from another registry instance. Competing retries use the SAME
        # persisted candidate token; Agent CAS prevents old-token overwrites.
        with self.registry._node_operation('replacement:' + attempt_id):
            terminal=self._terminal(node_id,attempt_id)
            if terminal is not None:return terminal
            with self.store.transaction() as db:
                stored=db.execute('SELECT * FROM remote_node_replacements WHERE attempt_id=? AND node_id=?',
                                  (attempt_id,node_id)).fetchone()
                if stored is None:
                    terminal=self._terminal(node_id,attempt_id)
                    if terminal is not None:return terminal
                    raise PolicyError('Replacement preparation not found')
                row=dict(stored)
                if not row['resolution']:
                    if int(row['operation_revision'])+1>=2**63:
                        raise PolicyError('Replacement operation sequence exhausted')
                    db.execute('UPDATE remote_node_replacements SET operation_revision=operation_revision+1 '
                               'WHERE attempt_id=?', (attempt_id,))
                    row = dict(db.execute('SELECT * FROM remote_node_replacements WHERE attempt_id=?',
                                          (attempt_id,)).fetchone())
            if row['resolution']=='committing':
                return self.commit(node_id,attempt_id,**json.loads(row['confirmation_json']))
            if row['resolution']=='cancelling':
                return self.cancel(node_id,attempt_id,discard_candidate=True)
            try:
                with self.store.lock:
                    self._assert_current(self.store.db, row)
                bootstrap = self._open(row['bootstrap_enc'])
                candidate = self._open(row['candidate_enc'])
                if not row['target_installation_id']:
                    health = self._exchange(row, bootstrap)
                    identity = self._validate_health(row, health)
                    self._update(row, target_installation_id=identity)
                from nodes import NodeHTTPError
                try:
                    # Recover a lost rotation response or Hub crash first. This
                    # request is installation-pinned and uses the journaled token.
                    health = self._exchange(row, candidate)
                except NodeHTTPError as exc:
                    if exc.status != 401:
                        raise
                    # A timeout, TLS error, redirect, or mismatched identity is
                    # NOT evidence that the old credential is still authoritative.
                    health = self._exchange(row, bootstrap)
                    self._validate_health(row, health)
                    self._update(row, phase='rotating', last_error='')
                    self._exchange(row, bootstrap, '/node/api/v1/replacement/rotate-token', 'POST', {'token':candidate})
                    # A positive POST response alone is insufficient proof.
                    health = self._exchange(row, candidate)
                self._validate_health(row, health)
                # Fresh installers may autostart an empty Xray. The Agent must
                # atomically recheck emptiness before a durable idle operation;
                # never issue a generic Stop to an unverified candidate.
                if health['core']['state']!='stopped' or health['run_control']['effective_running']:
                    self._exchange(row,candidate,'/node/api/v1/replacement/idle','POST',{})
                    health=self._exchange(row,candidate)
                self._validate_health(row, health, require_idle=True)
                self._update(row, phase='prepared', prepared_at=time.time(), last_error='')
            except PreparationChanged:
                pass  # A stale operation cannot overwrite the newer diagnostics.
            except Exception as exc:
                # Never persist raw remote detail or exception strings: they may
                # contain Pair Codes, tokens, or credentials from a hostile peer.
                error = exc.code if isinstance(exc, PreparationRejected) else 'target_contact_or_verification_failed'
                with self.store.transaction() as db:
                    db.execute('UPDATE remote_node_replacements SET last_error=?,updated_at=? '
                               'WHERE attempt_id=? AND operation_revision=?',
                               (error,time.time(),attempt_id,row['operation_revision']))
            return self.status(node_id, attempt_id)
