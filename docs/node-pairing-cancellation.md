# 2E3 — Cancel an unfinished first Node enrollment

This is **not registered Node deletion**, replacement rollback, or production
deployment approval. Only the interactive owner with writes enabled and valid
CSRF can use `POST /api/nodes/pairings/{attempt_id}/cancel`. The strict body is:

```json
{"confirmCancel":true,"acknowledgeCredentialReset":true}
```

## Two different receipts

* `withdrawn_before_rotation`: the durable journal has never recorded dispatch
  of rotation. Remove it and its reservation in the receipt transaction without
  contacting the Agent or decrypting credentials. Do not claim revocation,
  shutdown, or live health. The original Pair Code remains unchanged and may
  start a new attempt if the target still accepts it.
* `discarded_after_rotation`: rotation may have been dispatched. First persist a
  separate encrypted disposal credential and the irreversible cancellation
  intent. Verify the same pinned, still-empty Agent; recover with the disposal
  credential first. Only a typed HTTP 401 on the identity-verified HTTPS path
  permits trying candidate then bootstrap. Revoke those credentials using the
  Agent's freshness-guarded rotation, then confirm manual Stop and stopped core.
  Finalize the secret-free receipt and remove the journal atomically. Reuse of
  that installation requires reinstall or an authorized local token reset.

`live_state_verified=false` on both receipts: evidence describes completion at
that time, not a continuing health observation. Repeating a cancelled attempt
reads its receipt without network access, even if its address has since been
reused. A completed enrollment is never cancelled through this endpoint.

## Ambiguity, concurrency and backup

Timeouts, TLS/identity errors, a used target, failed Stop or failed final database
commit do not release a possibly-consumed target. The visible phase remains
`cancelling`; encrypted recovery material and reservations stay in the journal.
Retry `/cancel` with both confirmations. Ordinary `/retry` cannot restart
registration once cancellation has begun. There is no force-delete endpoint.

Same-Store coordinators share per-attempt process-local locks, not a global
network lock. Persisted operation revisions fence older observations. Before
any original rotation POST, `phase=rotating` is stored. Thus a late read cannot
cross the local-withdrawal boundary. The Agent reauthenticates buffered mutations
under its engine/rotation lock, preventing an invalidated credential from later
restoring an enrollment token. This does not implement a multi-process lease or
promise physically exactly-once network effects.

Backups preserve pending cancellation only if they include that journal and the
matching encryption key. A backup predating disposal may not know the accepted
credential. Deleting a journal is not secure erasure of SQLite pages or older
backup files. Registered Nodes, users, usage and ledger rows are not rewritten.

## UI

In Add Node, select the pending connection, choose **Cancel / continue
cancellation**, leave Pair Code empty, and explicitly accept both warnings.
Enrollment retry consent is separate. Changing the selection or action clears
consent. Requests clear submitted Pair Code text and consent; no credentials or
operation IDs are persisted in browser storage. Opening/reopening only reads
saved operations, never resends a cancellation or enrollment automatically.

An unconfirmed response is not a success toast. A response for a different
attempt, Node, origin or outcome is rejected. Closing the view or changing
owner/writes capability prevents late results from affecting a new dialog;
it does not undo a request already accepted by the server.

## Evidence boundaries

`tests/test_node_pairing_cancellation.py` uses real FastAPI, SQLite and files,
with fixture HTTPS transport and fake Xray. The existing pairing UI workflow
includes cancellation API cases and real Chromium against the full panel over
HTTP in `/control`; its Agent transport and Xray remain fixtures. It does not
replace a real-provider multi-VPS/WAN acceptance run, ACME renewal testing, or
explicit release/deployment approval. No previous test gate is removed.
