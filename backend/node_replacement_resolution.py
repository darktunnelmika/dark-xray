"""Explicit, durable resolution of a replacement preparation (Hub only).

Committing a binding leaves the logical Node disabled; it is NOT service
activation. Discarding a candidate revokes all journaled credentials, confirms
its idle state, then forgets those credentials. It requires reinstall/local
credential reset before reuse. Incomplete disposal retains the encrypted
journal and endpoint reservation. No network call runs inside a DB transaction.
"""
from __future__ import annotations

import json
import re
import secrets
import time

from dark_policy import PolicyError


class ReplacementResolution:
    def _init_resolution(self):
        with self.store.transaction() as db:
            columns={row[1] for row in db.execute('PRAGMA table_info(remote_node_replacements)')}
            for name, default in (('resolution',''),('resolution_token_enc',''),('confirmation_json','{}')):
                if name not in columns:
                    db.execute(f"ALTER TABLE remote_node_replacements ADD COLUMN {name} TEXT NOT NULL DEFAULT '{default}'")
            db.execute('''CREATE TABLE IF NOT EXISTS remote_node_replacement_history(
                attempt_id TEXT PRIMARY KEY,node_id TEXT NOT NULL,summary_json TEXT NOT NULL)''')

    def _terminal(self, node_id, attempt_id):
        if not isinstance(attempt_id,str) or not re.fullmatch(r'[0-9a-f]{32}',attempt_id):
            raise PolicyError('Invalid replacement attempt identity')
        with self.store.lock:
            row=self.store.db.execute('SELECT summary_json FROM remote_node_replacement_history '
                                      'WHERE node_id=? AND attempt_id=?',(node_id,attempt_id)).fetchone()
            if not row:return None
            result=json.loads(row['summary_json'])
            current=self.store.db.execute('SELECT binding_id FROM remote_node_installations '
                                         'WHERE node_id=? AND retired_at=0',(node_id,)).fetchone()
        result['binding_current']=bool(current and current['binding_id']==result.get('committed_binding_id'))
        return result

    def _resolve(self, node_id, attempt_id, action, confirmation=None):
        """Fence every older preparation/resolution before performing any I/O."""
        with self.store.transaction() as db:
            row=self._get(node_id,attempt_id)
            if row['resolution']=='cancelling' and action!='cancelling':
                raise PolicyError('Candidate disposal has started; it cannot be committed')
            if action=='committing':
                if row['phase']!='prepared':raise PolicyError('Prepare the candidate before committing')
                self._assert_current(db,row)
            token=row['resolution_token_enc']
            if action=='cancelling' and not token:
                token=self._seal('dkn_'+secrets.token_urlsafe(48))
            revision=int(row['operation_revision'])+1
            if revision>=2**63:raise PolicyError('Replacement operation sequence exhausted')
            db.execute('UPDATE remote_node_replacements SET resolution=?,resolution_token_enc=?, '
                       'confirmation_json=?,operation_revision=?,updated_at=?,last_error=\'\' WHERE attempt_id=?',
                       (action,token,json.dumps(confirmation or {}),revision,time.time(),attempt_id))
            return self._get(node_id,attempt_id)

    def _resolution_error(self, row, exc):
        from node_replacement import PreparationChanged, PreparationRejected
        if isinstance(exc,PreparationChanged):return
        # Remote exception bodies can contain credentials: persist only local codes.
        error=exc.code if isinstance(exc,PreparationRejected) else 'target_contact_or_verification_failed'
        with self.store.transaction() as db:
            db.execute('UPDATE remote_node_replacements SET last_error=?,updated_at=? '
                       'WHERE attempt_id=? AND operation_revision=? AND resolution=?',
                       (error,time.time(),row['attempt_id'],row['operation_revision'],row['resolution']))

    def _finish(self, db, row, phase, binding=None):
        """Secret-free receipt and journal removal share the binding transaction."""
        result=self._public(row)
        result.update(phase=phase,status_scope='resolution_receipt',prepared=False,last_error='',updated_at=time.time(),
            completed_at=time.time(),binding_committed=phase=='committed',
            committed_binding_id=binding['binding_id'] if binding else '',
            generation=binding['generation'] if binding else None,
            service_activated=False,cutover_performed=False,requires_cutover_confirmation=phase=='committed',
            requires_revalidation=phase=='committed',candidate_discarded=phase=='cancelled',
            candidate_requires_reinstall=phase=='cancelled',credentials_retained_in_journal=False,
            old_stop_confirmed=False,traffic_tail_complete=False)
        if binding:result['owner_acknowledgements']=json.loads(row['confirmation_json'])
        db.execute('INSERT INTO remote_node_replacement_history(attempt_id,node_id,summary_json) VALUES(?,?,?)',
                   (row['attempt_id'],row['node_id'],json.dumps(result)))
        db.execute('DELETE FROM remote_node_replacements WHERE attempt_id=? AND operation_revision=?',
                   (row['attempt_id'],row['operation_revision']))

    def commit(self, node_id, attempt_id, *, source_binding_id, accept_unconfirmed_old_server, accept_unreported_traffic):
        """Switch only the binding after fresh new-token proof; leave Node disabled."""
        if (not isinstance(source_binding_id,str) or not re.fullmatch(r'[0-9a-f]{32}',source_binding_id)
                or accept_unconfirmed_old_server is not True or accept_unreported_traffic is not True):
            raise PolicyError('Confirm source binding, unconfirmed old-server shutdown and incomplete traffic tail')
        confirmation=dict(source_binding_id=source_binding_id,
                          accept_unconfirmed_old_server=True,accept_unreported_traffic=True)
        with self.registry._node_operation('replacement:'+attempt_id):
            terminal=self._terminal(node_id,attempt_id)
            if terminal is not None:
                if terminal['phase']!='committed' or terminal['source_binding_id']!=source_binding_id:
                    raise PolicyError('Replacement already resolved with a different outcome')
                return terminal
            try:
                row=self._get(node_id,attempt_id)
                if row['source_binding_id']!=source_binding_id:
                    raise PolicyError('Confirmation refers to a different source installation')
                row=self._resolve(node_id,attempt_id,'committing',confirmation)
            except PolicyError:
                # Another registry/process may finish between our receipt read
                # and journal lookup. Re-read the durable outcome, never replay.
                if self._terminal(node_id,attempt_id) is not None:
                    return self.commit(node_id,attempt_id,**confirmation)
                raise
            try:
                credential=self._open(row['candidate_enc'])
                # No bootstrap fallback during commit: new-token possession and
                # the same pinned, fresh, durably idle installation must be proved.
                health=self._exchange(row,credential)
                self._validate_health(row,health,require_idle=True)
                from nodes import validate_origin, validate_data_address
                origin=validate_origin(row['target_origin'])
                address=validate_data_address(row['data_address'],origin)
                with self.registry._node_operation(node_id):
                    with self.store.transaction() as db:
                        self._assert_current(db,row)
                        binding=self.registry.installations._replace_locked(db,node_id,
                            expected_binding_id=row['source_binding_id'],agent_id=row['target_agent_id'],
                            installation_id=row['target_installation_id'],origin=origin,
                            credential=row['candidate_enc'],data_address=address,attempt_id=attempt_id)
                        self._finish(db,row,'committed',binding)
            except Exception as exc:self._resolution_error(row,exc)
            return self.status(node_id,attempt_id)

    def cancel(self, node_id, attempt_id, *, discard_candidate):
        """Discard an empty candidate without touching the current logical Node.

        Confirmation explicitly authorizes making its consumed Pair Code and
        journal credentials unusable. Offline/ambiguous results remain pending.
        """
        if discard_candidate is not True:
            raise PolicyError('Confirm candidate disposal; reinstall or local token reset is required before reuse')
        with self.registry._node_operation('replacement:'+attempt_id):
            terminal=self._terminal(node_id,attempt_id)
            if terminal is not None:
                if terminal['phase']!='cancelled':raise PolicyError('Committed binding cannot be cancelled as preparation')
                return terminal
            try:row=self._resolve(node_id,attempt_id,'cancelling')
            except PolicyError:
                if self._terminal(node_id,attempt_id) is not None:
                    return self.cancel(node_id,attempt_id,discard_candidate=True)
                raise
            try:
                from nodes import NodeHTTPError
                disposal=self._open(row['resolution_token_enc'])
                current=None
                # Recover a lost disposal reply FIRST. Only a typed 401 permits
                # testing the prior credential, never a TLS/identity/timeout error.
                for sealed in (row['resolution_token_enc'],row['candidate_enc'],row['bootstrap_enc']):
                    credential=self._open(sealed)
                    try:health=self._exchange(row,credential)
                    except NodeHTTPError as exc:
                        if exc.status!=401:raise
                        continue
                    identity=self._validate_health(row,health)
                    if not row['target_installation_id']:self._update(row,target_installation_id=identity)
                    current=credential
                    break
                if current is None:raise PolicyError('No journal credential authenticates')
                if health['core']['state']!='stopped' or health['run_control']['effective_running']:
                    self._exchange(row,current,'/node/api/v1/replacement/idle','POST',{})
                    health=self._exchange(row,current)
                self._validate_health(row,health,require_idle=True)
                if current!=disposal:
                    self._exchange(row,current,'/node/api/v1/replacement/rotate-token','POST',{'token':disposal})
                health=self._exchange(row,disposal)
                self._validate_health(row,health,require_idle=True)
                with self.store.transaction() as db:
                    self._assert_current(db,row)
                    self._finish(db,row,'cancelled')
            except Exception as exc:self._resolution_error(row,exc)
            return self.status(node_id,attempt_id)
