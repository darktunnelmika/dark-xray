# Explicit stale-Hub sequence recovery — checkpoint 2D1

This checkpoint repairs command/configuration sequence conflicts on an existing,
**already pinned installation**. It is not a full disaster-recovery engine,
automatic restore detection, an enrolment flow, or a customer-data restore.
No recovery UI is introduced in this checkpoint.

## Owner API

All routes use the existing interactive-owner dependency. Mutating requests
retain CSRF checks and `writes_enabled`. API keys and resellers cannot use them.

- `POST /api/nodes/{node}/recovery/review`: read the local checkpoint without
  calling the mutating desired-state getter; obtain a new checkpoint from the
  pinned Agent. No local writes, credential rotation, or remote mutation.
- `GET /api/nodes/{node}/recovery/current`: saved receipt only; no Agent call,
  credential decryption or database write. It does not prove current liveness.
- `POST /api/nodes/{node}/recovery/stop`: strict `bindingId`, `reviewHash`,
  `acknowledgeServiceInterruption=true`, `acknowledgeBackupMayBeStale=true`.
  Recheck the review before saving a new recovery operation. Unknown fields and
  truthy strings are rejected. A matching existing operation is retried, not
  recreated; a completed receipt never disables/stops a later-running Node.
- `POST /api/nodes/{node}/recovery/{attempt}/retry`: continue the saved Stop.
  A lost first response can be recovered through `current` and this endpoint.

Review detects an Agent command/configuration ahead of the saved Hub, equal
sequence numbers with different identities/hashes, and Agent state behind
previously acknowledged Hub state. Ordinary pending work is not automatically
called a restore. An interrupted recovery is explicitly reviewable again even
when its latest Stop has already removed the original sequence conflict.

## Transaction and transport boundaries

Before remote mutation, one Hub transaction records a Stop strictly newer than
both known command counters, saves its identity and expected Agent checkpoint,
and disables the Node. While pending, normal enable/Start/Restart is blocked.
All network calls use the existing verified HTTPS/DNS-pinned transport with
expected Agent and installation identity. No transaction remains open over I/O.
The recovery path never uses a legacy mutation as a fallback.

`GET /node/api/v1/recovery/state` observes command/configuration checkpoints under
the Agent engine lock. `POST /node/api/v1/recovery/stop` uses the same final token
authentication/lock as configuration and token rotation, compares both reviewed
checkpoints, and only calls the existing ordered Stop. A later command or
configuration rejects the delayed request. Exact pending/failed Stop retries are
allowed, while a committed Stop receipt is not physically replayed. Existing
final-traffic-flush safety remains in force.

The Hub requires an exact Stop acknowledgement **and a separate stopped-state
observation**. Binding/origin/credential and local command/configuration changes
are rechecked after I/O and inside the final transaction. Finalization acknowledges
Stop and re-numbers the saved local configuration above both known configuration
counters, with `desiredRunning=false`, cleared assignment/configuration ACKs and
pending synchronization. The Node remains disabled. No configuration is sent and
no Start/Restart or token rotation is performed by recovery.

A failed final transaction retains the original journal and pending configuration.
Retry uses the same Stop identity. A new, explicit review can supersede stale
local inputs rather than requiring journal deletion. Status/error/audit output
never includes credentials, user UUIDs or untrusted remote error bodies.

## What this does not recover

Central users, owner allocations, UUIDs, expiry, hosts, cumulative usage, raw usage
checkpoints and traffic ledger are not rewritten. Agent counters are not reset.
However, preserving a restored value does NOT recreate data missing from an old
backup. In particular, traffic across intervening resets, missing users/policies,
missing encryption keys, unknown installations and lost token-rotation journals
need their own recovery decisions. `usage_reconciled=false` is intentional.

The operator must review the restored customer/configuration data before later
explicit synchronization and Start. Recovery does not automatically grant either.
A running Node can continue serving while review/pending contact fails; disabling
a registry entry is not a firewall and Stop is not reported confirmed without proof.

Open replacement journals or a held replacement deployment must be resolved via
their own lifecycle first; recovery does not override those operations. A missing
Hub configuration or missing installation pin is rejected rather than guessed.
Installation identity is not hardware attestation; a full Agent database clone
copies that identity as well. Arbitrary simultaneous restores of both peers and
physical power-loss exactly-once guarantees are not claimed.

## Packaging and tests

The lightweight installer/updater explicitly ship `node_recovery_protocol.py`,
not the Hub-only `node_recovery.py`. The test runner includes `test_node_recovery.py`.
Tests use real FastAPI/SQLite/files and the real Hub request encoder/identity
checks; Xray is a fixture and socket/TLS is replaced by TestClient. They do not
establish provider-WAN, public TLS, or real-VPS disaster-recovery acceptance.
