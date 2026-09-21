# Replacement preparation / آماده‌سازی سرور جایگزین

Checkpoint 2C2. **Prepared is not transferred, deployed, or approved for traffic.**
The existing logical Node, origin, active token, assignments, accounts, consumption,
Hosts and customer endpoints are not changed by these endpoints.

## API contract

Interactive owner session and CSRF are required. Mutations obey `writes_enabled`.

- `POST /api/nodes/{node_id}/replacement/prepare` with `{ "code": "DXN1.…" }`
  persists a preparation journal before remote mutations, then attempts preparation.
- `GET /api/nodes/{node_id}/replacement/{attempt_id}` only reads the journal.
- `POST /api/nodes/{node_id}/replacement/{attempt_id}/retry` resumes with the
  same saved credential; never generates a new token merely because a reply was lost.

HTTP 200 means a preparation status was returned, not that preparation completed.
Use `prepared`, `phase`, `last_error`, `source_current`, and `prepared_at`.
`cutover_performed` is always false and `requires_revalidation` is always true.
No replacement/cutover button is added in this checkpoint.

## Safety and recovery

The target must have a separate management HTTPS origin not registered to another
Node (including the old Node). Customer data addresses are independent: reusing a
customer domain later is a cutover decision, not an Agent pairing operation.
Pair Codes are strictly parsed; a browser cannot supply a trusted Health descriptor.
The existing HTTPS certificate verification, public-address policy, DNS-pinned
connection, redirect rejection, and byte limits are shared with normal Node requests.

Both bootstrap and candidate credentials are encrypted with the existing Hub cipher
and retained in SQLite before rotation. Codes/tokens are absent from status and audit
records. Errors saved in the journal are bounded local codes, never remote messages.
On retry, the candidate credential is checked first using the pinned installation.
Only a typed HTTP 401 permits probing the bootstrap credential. A timeout, TLS error,
redirect, malformed response, or identity mismatch is not permission to fall back.
A positive rotation response alone is insufficient: candidate-authenticated Health
must succeed. A reply lost after rotation, idle, or Hub recreation is recoverable.
A backup containing this journal and its cipher key preserves this recovery state.
A backup **predating** the journal cannot recover a token it never recorded.

The Agent advertises `replacement_prepare: 1`. Its replacement-specific rotation
and idle endpoints recheck freshness under the same lock as configuration/control.
A target that gained an inbound/client, applied state, or ordered command receipt is
rejected, even if an earlier Health was empty. Fresh installers may start an empty
Xray automatically; the idle endpoint durably pauses only an empty candidate. It
neither assigns an inbound nor creates an ordered command receipt. Existing services
are never stopped based only on an earlier Health response.

Token replacement uses a private uniquely named temporary file, file fsync, atomic
rename and parent-directory fsync. Old buffered credentials are checked again under
the rotation lock. If directory fsync fails after rename, in-memory authentication
tracks the replaced file but the request does not report success. This is fault
injection coverage, not a physical power-loss guarantee.

Source binding/origin changes and superseded preparation operations fence journal
publication. The target installation/origin is checked for concurrent registration
before publishing readiness. Prepared status is a recorded observation: a future
cutover MUST authenticate again and revalidate current identity, emptiness, source
binding, quota/config versions, target readiness and endpoint decisions.

## Boundaries remaining for the next checkpoint

There is no call to `NodeInstallations.replace_verified` from this preparation API.
No configuration is deployed, no DNS/tunnel target is changed, and the old VPS is not
stopped. Commit/cancel/abandon semantics, credential cleanup, history retention and
UI are subsequent work. One unresolved preparation reserves its source Node and
target endpoint; ordinary Node Add/Edit/Pair refuses that reserved origin; do not delete a journal whose remote credential outcome is uncertain.

The existing initial-add `/api/nodes/pair` and general Hub `rotate_token` flow have
NOT been converted to this replacement journal. The Agent token-file hardening
applies to them, but automatic Hub lost-reply recovery for those legacy flows is not
claimed. Restore revision repair, source traffic-tail recovery, physical reboot,
provider WAN, real multi-VPS cutover and ACME renewal remain separate acceptance.

Tests use real FastAPI/SQLite/files and a fake Xray fixture; the real request encoder
and Agent API are bridged by TestClient, not a verified provider network connection.
