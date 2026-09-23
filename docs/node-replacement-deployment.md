# Replacement deployment — checkpoint 2C4A

## Scope

This step stages current Hub configuration on an already **committed** replacement
binding. The Node stays disabled in the Hub and durably stopped on the Agent. No
Start/Restart, endpoint change, DNS change, tunnel update, old-server shutdown or
customer traffic cutover is performed. A successful stage is a stopped, validated
configuration snapshot, **not** a live data-plane or provider-WAN acceptance result.

## Interactive owner API

- `POST /api/nodes/{node_id}/replacement/{attempt_id}/stage`
  takes exactly `{"bindingId":"<current committed binding ID>"}`.
- `GET /api/nodes/{node_id}/replacement/{attempt_id}/deployment`
  reads stored proof and local invalidation state. It performs no Node I/O,
  configuration compilation, credential decryption or policy refresh.

Both require an interactive owner session; the POST also requires CSRF and
`writes_enabled`. As elsewhere in this API, HTTP 200 can describe a pending
operation. Check `configuration_staged`, `phase` and `last_error`; never infer
success from HTTP status alone. Repeating POST is the explicit retry path.

The prepare/commit/cancel receipt remains separate and historical. This does not
change the meaning of the earlier `binding_committed` flag into service activation.

## Ordering and safety

1. Verify the committed receipt, current binding/installation/origin, disabled
   state and durable Stop intent. Persist an operation revision and activation
   hold before any remote I/O.
2. Re-send the exact stored Stop identity. Require its matching acknowledgement
   and independently verify authenticated health, installation identity and the
   durable paused control state. Never set `enabled=True` to reach the target.
3. Refresh Hub policy, compile the **current** assigned inbounds, clients, limits,
   sections and managed TLS files. Fence changes to local inputs across compile.
   Address the replacement Agent identity, preserve the logical Node identity
   and force the existing stable `desiredRunning=False` fallback.
4. Deliver desired state using the ordinary exact-ACK/assignment publisher, with
   a narrowly scoped, installation-pinned HTTPS requester for the disabled target.
   Revalidate live binding, credential, Stop identity and operation revision before
   and after each exchange. TLS, address pinning and SSRF validation are retained.
5. Run Xray's configuration validation without starting it. Require all expected
   mirrored traffic identities exactly once with integer zero counters; never
   discard, reset or manufacture usage to accommodate a nonzero target.
6. Independently re-read authenticated health. Require the applied revision/hash,
   Stop receipt, stopped state and inbound/client counts to match. Refresh policy
   and compile again; changed settings or changed TLS bytes invalidate the result.
   Publish only after checking the same operation and inputs in SQLite.

The raw installed counters are checked, not imported as a replacement for the
Hub's accumulated usage. The replacement transaction's known-zero accounting
baseline and retained ledger remain unchanged. Routine policy refresh may advance
Hub observation sequences or legitimately disable a newly expired/exhausted user.

## Activation hold

After staging has begun, ordinary enable (including Edit/Save) and Start/Restart
recording are rejected for that binding, both while pending and after a successful
stage. The hold is stored in the Hub database and survives registry recreation and
an encrypted full backup/restore. Stop remains permitted and a newer Stop makes an
older in-flight deployment result stale. Other Nodes/bindings are not held.

This checkpoint has **no hold-release or activation route**. The subsequent
explicit activation coordinator must revalidate current configuration, credentials,
policy, target runtime and endpoint plans before releasing the hold. Do not remove
it with a database edit. Cancelling a preparation does not undo a committed binding.

## Failure and retry

A lost Stop or apply acknowledgement does not imply success. The durable pending
record is retained. Retry reuses the stored Stop identity and unchanged desired
revision; Agent receipts and idempotent apply handle already completed effects.
An operation revision prevents an older concurrent retry from overwriting newer
proof. Agent restarts retain the paused state and installation identity.

Remote exception text may contain credentials. The specialized transport replaces
it with a bounded local error code **before** it can reach either the deployment
journal or ordinary desired-state error metadata. No token or payload appears in
the public deployment proof. TLS/identity/authentication errors never trigger old
credential fallback here.

Partial remote effects are possible (e.g. configuration applied, final health reply
lost), but the target stays stopped and the Hub stays disabled. Rechecking/retrying
is required; no automatic rollback of a remote effect is claimed.

## Reading proof correctly

`phase=staged` is historical. `snapshot_current` checks known **local database**
inputs, binding/credential, Stop and desired-state acknowledgements. Local edits
invalidate `configuration_staged` without doing network work. These flags are not
current remote liveness: they cannot detect a later physical outage, root edits on
the Agent, changed external TLS file contents, or passage of expiry time without a
policy refresh. `requires_revalidation=true` is always explicit. A stopped staged
core may report `dirty=true` because no active running config has been installed;
this is not proof that it is ready for customer traffic.

Remaining work: controlled activation/hold release, endpoint/DNS/tunnel review,
replacement UI, restored revision repair, general pairing/token handoff recovery,
real multi-VPS acceptance and ACME renewal. Unreported old-server traffic and
unconfirmed old-server shutdown remain explicitly unverified.
