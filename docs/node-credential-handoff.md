# Registered Node credential handoff — checkpoint 2E2

This is a **token-only** operation on an existing, already pinned Agent installation.
It does not enroll/replace a server, stop/start/restart Xray, change endpoints,
assign clients, synchronize configurations, or reset usage. Initial Pair Code
recovery remains in the separate pairing coordinator.

## Owner API and Manage Node

- `GET /api/nodes/{node}/credentials/current` reads the newest saved operation or
  receipt. It does not decrypt credentials, contact the Agent, or write data.
- `POST /api/nodes/{node}/credentials/rotate` requires `bindingId`, `token` and
  strict `confirmRotate=true`; unknown fields and truthy strings are rejected.
- `POST /api/nodes/{node}/credentials/{attempt}/retry` requires strict
  `confirmRetry=true` and resumes that saved operation without a new token.

All API routes retain interactive-owner authentication, CSRF and local
`writes_enabled` on mutations. Agent capability `credential_rotation: 1` is
required; upgrade the Agent before using this feature. There is no legacy fallback.
The registered Agent accepts the request only with the expected installation
headers and reauthenticates the bearer under its existing mutation/rotation locks.

The original PATCH route now validates inbound selection and requires unchanged
name, origin, data address, enabled state, priority, failover and assignments when
it contains a replacement token. **Save other settings separately.** Its token
branch returns a saved handoff result, including pending outcomes, instead of a
misleading settings-saved node document. This branch never sends configuration.

Manage Node fetches saved status on opening. A pending handoff can be explicitly
resumed with an empty token field. Retry changes only the credential, not other
form edits. Consent is not preselected and is cleared before retry; token text is
cleared after a submitted token edit. Pending/malformed/mismatched results do not
produce a success message. Concurrent submissions, a disconnected form, changed
owner or revoked write permission cannot act through a stale form. No token or
consent is written to browser storage. This is not cancellation: closing the form
cannot undo a request already accepted by the Agent.

## Durable state and publication

Before any remote mutation, SQLite saves the current encrypted credential and a
single encrypted candidate, installation/Agent/binding/origin identities, and an
operation identity. Pending credentials survive process reconstruction and a
backup **that contains the journal and its encryption key**. The established
registration, enabled flag, run intent, users, expiry, limits, host references,
configuration and traffic checkpoints remain unchanged.

Retries probe the saved candidate first. Only an explicit HTTP 401 over the
existing verified-TLS, DNS-pinned transport permits using the saved old token.
TLS failures, wrong identity headers, other HTTP errors and malformed capability
responses do not trigger that fallback. With the old token, capability and remote
write permission are checked before a rotation. A positive rotation response is
not sufficient: an independent request using the new token must authenticate the
same installation. Confirming an already-applied candidate remains possible when
the Agent has subsequently entered read-only mode, because it needs no mutation.

After verification, one transaction publishes the candidate in the registered
node and writes the completed receipt, clearing the journal's decryptable copies.
Failure rolls back the entire transaction and retains the saved candidates. The
credential's original and candidate hashes remain internal to reject known token
reuse on that binding. Completed retries only read the receipt; they never rotate
again or re-enable an independently disabled node. Status explicitly describes a
saved receipt, not current data-plane readiness or a permanent credential proof.

SQL reservations prevent competing credential/endpoint edits, node deletion,
installation retirement or replacement while a handoff is pending. Normal Stop
intent and disabling a node are not blocked; neither is running Xray interrupted
by credential rotation. Active replacement/recovery workflows cannot be replaced
by a token operation. Late results are checked against the current journal version
and pinned registration. Network I/O is outside SQLite transactions.

Remote error bodies are not persisted: status and audit use bounded local codes,
not token contents. A candidate accepted remotely but not yet published locally
may temporarily interrupt management polling; the old registration credential is
kept until independent verification. This is not a firewall or a no-interruption
SLA for management commands, and it does not reconstruct missing backup data.

## Deliberate remaining boundaries

There is no automatic deletion or cancellation of a handoff with an unknown
outcome. Safe abandonment of initial pairings is also separate work. Losing both
retained keys/journals, changing the token independently on the VPS, cloning an
Agent database, or restoring arbitrarily old state may require explicit operator
recovery. No public-network/VPS acceptance or physical exactly-once guarantee is
claimed. DNS, tunnels, old-server shutdown and production rollout are untouched.

The dedicated CI workflow runs API regressions and the browser against the full
Hub HTTP shell under `/control`. Local managed Chromium may prohibit loopback;
explicit bridge-mode DOM/API tests are labelled separately and are not full HTTP
or WAN evidence. Assertions are not weakened to accommodate that restriction.

## Same-process coordinator overlap

Credential operations on the same node now share a per-node lock through their
live SQLite Store, even when separate NodeRegistry objects were constructed.
A second coordinator waits for the first remote handoff instead of superseding
its operation revision while I/O is in flight. If the first one completes, the
waiter reads its saved receipt without another rotation. The Store lock is not
held while waiting or contacting an Agent; other nodes and ordinary disabling
can still proceed. These locks are process-local and not a multi-process lease.
The persisted operation revisions, identity checks, independent candidate-token
verification and atomic publication remain authoritative after restart/restore.
