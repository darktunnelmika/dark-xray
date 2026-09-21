# Durable ordinary Pair Code enrollment — checkpoint 2E1

The ordinary `POST /api/nodes/pair` now records encrypted bootstrap and candidate
credentials **before** any Agent mutation. An incomplete enrollment is stored in
`remote_node_pairings`, not published as an enabled registered node. The existing
Pair Code schema, strict parsing and verified HTTPS/SSRF transport are reused.

## Protocol and publication

1. Validate the Pair Code and reserve its logical node ID and management origin.
   An identical pending submission reuses its existing attempt and candidate;
   conflicting metadata/credentials are rejected. A code for an already registered
   node still returns 409. At most 100 pending enrollments may be retained.
2. Authenticate the bootstrap health and persist the installation identity before
   sending a credential change. Reject unknown/retired/elsewhere reserved identities.
3. Try the **saved candidate** credential first. Only the transport's verified 401
   permits checking the bootstrap and then rotating. Timeout, TLS or installation
   errors do not authorize this fallback. A POST reply alone is never sufficient:
   a subsequent health request with the candidate must match the pinned installation.
4. Enrollment requires a fresh, unassigned, identity-capable Agent. Reuse the
   existing Agent `/node/api/v1/replacement/rotate-token` endpoint, whose freshness
   condition and final authentication are checked under the engine lock. Its name
   predates this shared bootstrap use. No generic unguarded fallback is used.
5. Register the new node/binding and a secret-free receipt and remove the active
   credential journal in a **single SQLite transaction**. This transaction performs
   no network I/O. Failure rolls back registration and retains both encrypted
   credentials. Only the new active token remains in the normal encrypted node
   record after success; neither credential is kept in the historical receipt.

A newly registered node preserves the previous ordinary Pair behavior: enabled
in the Hub with **no inbound assignments**. No Stop, Start, Restart or configuration
apply is issued by this enrollment. An already-running empty installer stays empty;
registration is not a usable customer-route claim. Existing users, quotas, expiry,
usage, public endpoints and other nodes are not modified. The monitor and ordinary
inbound assignment workflow handle subsequent configuration, separately.

## Resumption, collisions and errors

SQLite triggers protect pending IDs/origins/installations from ordinary Add/Edit,
installation pinning and replacement preparation, including another registry
instance. The replacement coordinator also reinstalls these guards when it is
initialized after pairing. Publication temporarily removes the reservation only
within its all-or-nothing transaction. The journal's operation revision fences
obsolete local responses; every retry uses the same candidate credential. Agent
final authentication prevents a delayed bootstrap request overwriting a new token.

A lost first HTTP response can be found through the pending list. A lost rotation
reply, final database failure, Hub recreation or Agent restart is recoverable with
the retained credentials and identity. Restoring an encrypted backup **that contains
this journal and its key** can resume the same handoff. A backup from before the
operation cannot reconstruct its new token. No such missing-data recovery is claimed.

All surfaced handoff errors are local allowlisted codes, not untrusted remote
exception bodies. `paired=false` plus HTTP 200 denotes saved pending work; it is not
success. A completed receipt is historical, with `registration_current` describing
whether its binding is still current, not Agent liveness. Repeating a completed
retry only reads that receipt; it never re-enables/recreates a deleted/disabled node.

## Owner API and existing Add Node dialog

All endpoints retain interactive-owner authentication; mutation retains CSRF and
`writes_enabled`. API keys and resellers are not enrollment administrators.

- `POST /api/nodes/pair`: strict `{code}`; initiate/resume an identical pending code.
- `GET /api/nodes/pairings`: pending operations only, with no credentials.
- `GET /api/nodes/pairings/{attempt}`: pending state or historical receipt.
- `POST /api/nodes/pairings/{attempt}/retry`: strict `{confirmRetry:true}`.

GET endpoints do not decrypt credentials, contact Agents or write the database.
The existing bilingual Add Node dialog reads the pending list on opening. Select a
saved connection, leave the Pair Code empty and explicitly check retry consent.
Nothing auto-retries. Controls/response validation prevent HTTP-200 pending work or
another attempt's reply from being displayed as a successful connection. Read-only
owners have no submit handler. No new browser storage of credentials/consent is used.
Closing the dialog does not undo an already accepted server-side request.

## Scope and remaining work

This checkpoint repairs **first enrollment**, not `NodeRegistry.rotate_token` for
editing an already registered node. That generic credential-edit path still needs
its own journal/migration. Pending first enrollments have no discard/cancel endpoint
here; never delete a rotating/uncertain journal to make the UI look clean. Explicit
safe disposal of abandoned targets is subsequent work. Old/used Agents must use an
appropriate update/adoption workflow; this code does not reset them implicitly.

Tests use actual FastAPI/SQLite/files and the real Hub request encoder/identity
checks, with an in-process Agent transport and fake Xray. JavaScript tests execute
the real dialog function with an API/DOM harness; they are not real-browser HTTP or
provider-WAN acceptance. A successful credential observation cannot stop independent
root/other-Hub changes after that observation. Database cloning is not hardware
identity, and physical power-loss exactly-once behavior is not asserted.
