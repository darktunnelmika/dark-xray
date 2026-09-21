"""Explicit withdrawal of a first-enrollment journal, never Node deletion.

An intent that has never reached the rotation boundary can be withdrawn locally.
Once rotation may have been sent, revoke its credentials on the pinned, still
empty Agent and confirm durable idle before releasing reservations. Ambiguous
outcomes retain the encrypted journal. Receipts describe past work, not health.
"""
from __future__ import annotations

import secrets
import threading
import time

from dark_policy import PolicyError


class PairingCancellation:
    def _init_cancellation(self):
        with self.store.transaction() as db:
            columns = {row[1] for row in db.execute('PRAGMA table_info(remote_node_pairings)')}
            for name in ('resolution', 'disposal_enc'):
                if name not in columns:
                    db.execute(f"ALTER TABLE remote_node_pairings ADD COLUMN {name} TEXT NOT NULL DEFAULT ''")
            db.execute('''CREATE TABLE IF NOT EXISTS remote_node_pair_cancellations(
                attempt_id TEXT PRIMARY KEY,node_id TEXT NOT NULL,name TEXT NOT NULL,
                origin TEXT NOT NULL,installation_id TEXT NOT NULL,
                outcome TEXT NOT NULL CHECK(outcome IN ('withdrawn_before_rotation','discarded_after_rotation')),
                created_at REAL NOT NULL,completed_at REAL NOT NULL)''')
            # Multiple coordinator objects on one live Store must not race the
            # same journal. SQL revisions still fence stale responses. This is
            # process-local coordination, not a multi-process execution lease.
            if not hasattr(self.store, '_node_pairing_locks'):
                self.store._node_pairing_locks = {}
            self._pairing_locks = self.store._node_pairing_locks

    def _pair_operation(self, attempt_id):
        with self.store.lock:
            return self._pairing_locks.setdefault(str(attempt_id), threading.RLock())

    def _cancelled(self, attempt_id):
        with self.store.lock:
            row = self.store.db.execute('SELECT * FROM remote_node_pair_cancellations WHERE attempt_id=?',
                                        (attempt_id,)).fetchone()
        if row is None:
            return None
        result = dict(row)
        discarded = result['outcome'] == 'discarded_after_rotation'
        return {**result, 'phase': 'cancelled', 'cancelled': True, 'paired': False,
                'pair_code_consumed': discarded, 'registration_current': False,
                'candidate_discarded': discarded, 'candidate_requires_reinstall': discarded,
                'credential_revocation_confirmed': discarded, 'remote_idle_confirmed': discarded,
                'credentials_retained_in_journal': False, 'reservation_released': True,
                'status_scope': 'saved_pairing_cancellation_receipt', 'live_state_verified': False,
                'last_error': ''}

    def _finish_cancel(self, db, row, outcome):
        # Caller owns the transaction. Roll back reservation release if writing
        # the secret-free receipt fails; no token belongs in a terminal receipt.
        self._assert(row)
        db.execute('''INSERT INTO remote_node_pair_cancellations
            (attempt_id,node_id,name,origin,installation_id,outcome,created_at,completed_at)
            VALUES(?,?,?,?,?,?,?,?)''', (row['attempt_id'],row['node_id'],row['name'],row['origin'],
                                        row['installation_id'],outcome,row['created_at'],time.time()))
        db.execute('DELETE FROM remote_node_pairings WHERE attempt_id=? AND operation_revision=?',
                   (row['attempt_id'],row['operation_revision']))

    def _require_idle(self, row, health):
        from node_pairing import PairingRejected
        self._health(row, health)
        control = health.get('run_control')
        if (health['core'].get('state') != 'stopped' or not isinstance(control, dict)
                or control.get('effective_running') is not False or control.get('manual_stop') is not True):
            raise PairingRejected('pairing_target_not_durably_idle')

    def cancel(self, attempt_id, *, confirm_cancel, acknowledge_credential_reset):
        from node_pairing import PairingRejected
        from nodes import NodeHTTPError
        if confirm_cancel is not True or acknowledge_credential_reset is not True:
            raise PolicyError('Confirm cancellation and possible reinstall or local token reset before reuse')
        with self._pair_operation(attempt_id):
            with self.store.transaction() as db:
                row = self._pending(attempt_id)  # validates the attempt ID first
                if row is None:
                    receipt = self._cancelled(attempt_id)
                    if receipt is not None:
                        return receipt  # never contact a reused endpoint
                    if self.status(attempt_id)['paired']:
                        raise PairingRejected('pairing_already_registered_cannot_cancel')
                    raise PolicyError('Node pairing attempt not found')
                self._available(db, row)
                if row['operation_revision'] >= 2**63 - 2:
                    raise PolicyError('Node pairing operation sequence exhausted')
                revision = row['operation_revision'] + 1
                if row['phase'] == 'pending' and not row['resolution']:
                    # The retry path persists phase=rotating BEFORE dispatch.
                    # Old health responses are fenced by deletion; old retries
                    # must assert their revision before recording that boundary.
                    # No decryption or Agent contact is needed for this branch.
                    row['operation_revision'] = revision
                    db.execute('UPDATE remote_node_pairings SET operation_revision=? WHERE attempt_id=?',
                               (revision,attempt_id))
                    self._finish_cancel(db, row, 'withdrawn_before_rotation')
                    return self._cancelled(attempt_id)
                if row['resolution'] not in ('', 'cancelling') or row['phase'] != 'rotating':
                    raise PairingRejected('pairing_cancellation_state_invalid')
                if not row['installation_id']:
                    raise PairingRejected('pairing_cancellation_identity_missing')
                sealed = row['disposal_enc'] or self.cipher.encrypt(
                    ('dkn_' + secrets.token_urlsafe(48)).encode()).decode()
                db.execute("UPDATE remote_node_pairings SET resolution='cancelling',disposal_enc=?,"
                           "operation_revision=?,last_error='',updated_at=? WHERE attempt_id=?",
                           (sealed,revision,time.time(),attempt_id))
                row = self._pending(attempt_id)
            try:
                disposal = self._open(row['disposal_enc'])
                current = None
                # A timeout, TLS failure or identity mismatch is NOT a 401 and
                # must never expose an earlier credential to another endpoint.
                for sealed in (row['disposal_enc'], row['candidate_enc'], row['bootstrap_enc']):
                    credential = self._open(sealed)
                    try:
                        health = self._exchange(row, credential)
                    except NodeHTTPError as exc:
                        if exc.status != 401:
                            raise
                        continue
                    self._health(row, health)
                    current = credential
                    break
                if current is None:
                    raise PairingRejected('pairing_cancellation_credential_rejected')
                # Revoke FIRST: buffered enrollment requests authenticated with
                # either earlier token cannot mutate after Agent's locked reauth.
                if current != disposal:
                    self._exchange(row,current,'/node/api/v1/replacement/rotate-token','POST',{'token':disposal})
                health = self._exchange(row, disposal)
                self._health(row, health)
                control = health.get('run_control')
                if (health['core'].get('state') != 'stopped' or not isinstance(control, dict)
                        or control.get('effective_running') is not False or control.get('manual_stop') is not True):
                    self._exchange(row,disposal,'/node/api/v1/replacement/idle','POST',{})
                    health = self._exchange(row, disposal)
                self._require_idle(row, health)
                with self.store.transaction() as db:
                    self._finish_cancel(db, row, 'discarded_after_rotation')
            except Exception as exc:
                error = exc.code if isinstance(exc, PairingRejected) else 'pairing_cancellation_contact_or_verification_failed'
                with self.store.transaction() as db:
                    db.execute('UPDATE remote_node_pairings SET last_error=?,updated_at=? '
                               "WHERE attempt_id=? AND operation_revision=? AND resolution='cancelling'",
                               (error,time.time(),attempt_id,row['operation_revision']))
            return self.status(attempt_id)
