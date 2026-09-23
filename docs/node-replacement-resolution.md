# Replacement resolution checkpoint 2C3

This builds on 2C1 installation fencing and 2C2 preparation. It adds explicit
owner-only binding commit and candidate disposal. It does **not** activate the
new service, deploy configurations to it, modify DNS/tunnels/Host definitions,
stop the old VPS, recover its unreported traffic, or implement the UI wizard.
No production deployment is authorized by this checkpoint.

## API contract

All writes use the existing interactive owner session, CSRF and writes-enabled
checks. API keys/resellers cannot bypass them. Request models are strict and
reject extra fields, including browser-provided Health or credential assertions.

`POST /api/nodes/{node_id}/replacement/{attempt_id}/commit`:

```json
{
  "sourceBindingId": "<the 32-hex source_binding_id from preparation>",
  "acceptUnconfirmedOldServer": true,
  "acceptUnreportedTraffic": true
}
```

The two confirmations acknowledge known limitations; they do **not** turn
`old_stop_confirmed` or `traffic_tail_complete` into true. Committing contacts
the same pinned installation with the persisted **candidate** credential, with
no bootstrap fallback. Fresh/empty and durably idle state is revalidated.

The binding/retirement/checkpoint change, terminal receipt, and deletion of the
active credential journal occur in one SQLite transaction. Any failure rolls
back all local changes. Network work and DNS validation are outside it.

Success is `phase=committed` and `binding_committed=true`, NOT HTTP 200 alone.
The Node remains disabled with a fresh pending Stop intent and unapplied desired
state. Assigned users, their credentials, quotas, expiry, Hosts and central
recorded consumption remain intact. The administrative origin and node data
address now describe the replacement, but `service_activated=false` and
`cutover_performed=false`: clients are not being deliberately switched live.
The subsequent deployment/endpoint/activation stage requires its own checks.

`POST /api/nodes/{node_id}/replacement/{attempt_id}/cancel`:

```json
{"discardCandidate": true}
```

**The UI must explain this before obtaining consent:** disposal invalidates
both the consumed Pair Code and the staged Hub credential on the empty target.
It leaves the target idle and requires a reinstall or locally authorized token
reset before reuse. It does not remove software and never touches the source
Node. This is not a silent return to the insecure reusable bootstrap token.

A separate unpredictable disposal credential is encrypted and journaled before
any remote change. Retry tests it first to recover a lost reply. Only a typed
HTTP 401 permits testing the prior candidate/bootstrap credentials. Timeout,
TLS, redirect and installation mismatch never justify that fallback.

The target must still be fresh; a target that has gained users or assignments
is not stopped/rotated by disposal. Freshness is rechecked in the Agent under
its engine lock for replacement-only idle/rotation. New-token Health must
independently prove successful disposal and the idle state before finalization.
Then a secret-free terminal receipt replaces the journal and releases its
origin/installation reservations. A failed or ambiguous disposal stays
`phase=cancelling`; credentials and reservations are retained for recovery.
Cancellation can clean up a candidate after the source binding/origin changes,
but refuses a target now registered for another operation. Once cancellation
starts, the same attempt cannot be committed.

## Retry, concurrency, and credential boundaries

`GET .../replacement/{attempt_id}` remains read-only, including terminal states.
`POST .../replacement/{attempt_id}/retry` resumes the **persisted** resolution
intent/consent, or the original preparation when no resolution was requested.
Terminal retries are reads: no second binding retirement or credential rotation.
Opposite explicit terminal actions fail. A terminal `status_scope` of
`resolution_receipt` records that action's outcome, not current core liveness;
`binding_current` reports whether its committed binding is still active.

Operation revisions fence delayed writes across registry instances. An active
credential journal is never deleted separately from its terminal receipt. The
internal replacement storage method cannot steal another pending preparation's
reserved target. Secret-free history permits another attempt without losing
old receipts. Restored journals include the original consent and credentials;
backups predating those records cannot reconstruct newly generated secrets.

All Agent HTTP POST handlers now reauthenticate under the shared engine lock
immediately before mutation, including generic configuration/control/update and
token handlers. A request authenticated before waiting for that lock cannot
modify the Agent after its credential has been revoked. The early ASGI auth and
whole-body limits remain in place. Direct root access or a fully cloned Agent
database is not a distinct, hardware-attested identity.

## Remaining limits and acceptance

Credential cleanup deletes active logical records; it is not forensic erasure
of old SQLite pages/WAL, backup archives or filesystem snapshots. On commit the
active Node's encrypted credential remains in its canonical registry record.
On disposal the random final credential is deliberately not returned in API,
audit or terminal history. Consent must explain the required local recovery.

This is not a distributed atomic service cutover. A successful Health is a
verified observation, not proof against root/another authorized manager changing
the target afterwards. The future activation path must revalidate installation,
configuration and traffic checkpoints; only one Hub may control a candidate.

Binding receipt recovery does not solve stale command/config revisions after an
old Hub restore, ordinary initial-pair/token journaling, final tail accounting,
old-VPS shutdown, DNS/tunnel migration, provider WAN/multi-Node acceptance, ACME
renewal, real power-loss acceptance, or physical exactly-once Restart.

Regression tests use real SQLite, FastAPI and local token files, fake Xray, and
in-process HTTP replacing socket/TLS. They test encrypted backup round-trips,
late replies and transactional failure injection; they are not real WAN proof.
