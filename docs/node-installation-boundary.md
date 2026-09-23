# Node replacement — checkpoint 2C1

This checkpoint adds installation identity and protects the stable logical Node
record. It is **not** a complete replacement wizard or permission to deploy.

## Identity and publication boundary

`remote_nodes.id` remains the logical key used by inbound assignments, public
Host runtime references, account ownership and cumulative traffic. Each active
binding has a random binding ID, a monotonic generation, the physical Agent ID
and, for supporting Agents, a pinned installation ID. Agent identity is stored
in `node_runtime_identity`: restarting the Agent keeps it; a fresh state directory
gets another ID. Cloning the whole Agent database also clones the identity: this
is not hardware attestation and must not be used as a cloning/enrollment API.

Existing logical nodes migrate in place. The database insert trigger also gives
new records a binding in the same transaction, including valid direct inserts.
Legacy Agents remain explicitly unpinned until a valid authenticated v1 health
response is observed. A pinned identity cannot silently downgrade to legacy or
change to another installation, even at the same domain/IP.

Supporting Hub requests carry the expected physical Agent ID and installation
ID. The Agent checks these optional headers after authentication and before
reading the body. Authenticated responses identify the installation in headers,
including responses with a list body. The Hub checks those headers and captures
the target before I/O. Traffic, security, configuration acknowledgements, command
receipts and credential-rotation acknowledgements publish through an
installation-checked SQLite transaction. A delayed result cannot write into a
new binding. Changing the management origin also fences old-origin responses.
Network I/O does not run inside a SQLite transaction.

Identity headers supplement HTTPS and bearer authentication; they replace
neither. Older Hubs without them remain compatible. Downgrading a pinned Agent
to a version without identity support is deliberately blocked, not silently
accepted as a working replacement.

## Internal replacement transaction

`NodeInstallations.replace_verified` is an internal primitive with no public
HTTP route or UI button. A future enrollment coordinator must authenticate the
new endpoint, verify TLS, establish its final credential and revalidate fresh
state before invoking it. Never pass a browser-supplied health document directly
to this function. The descriptor must identify a writable, stopped, unassigned
Agent with no desired revision or ordered-command receipt.

The transaction retains the logical ID, name, user UUIDs, owner assignments,
quota/expiry records, inbound definitions, Host references and existing traffic
ledger. It archives the old binding and its cumulative accounting checkpoints,
not a second copy to sum into consumption. The active cumulative usage and ledger
sequence remain intact. Raw counters for the confirmed fresh target start at
zero so its first reported bytes are counted, including assigned users never
sampled on the old Node.

Old command intent is archived, not replayed on the new installation. The new
binding has a new pending Stop identity; configuration targets the new physical
Agent, loses previous deployment acknowledgements and has a stopped fallback.
The logical node remains **disabled**, not exposed as a ready subscription route,
after this primitive commits. Failed validation or storage changes roll back
together. This is not an automatic rollback after a successful network cutover.

Nodes with retained replacement history or accounted consumption cannot be
hard-deleted through the existing delete path. Disabling remains available.
Empty failed bootstrap registrations can still be cleaned up.

## Explicitly unfinished

The Pair Code replacement flow, two-phase/persisted credential handoff and
recovery from a lost token-rotation response, prepared-target orchestration,
endpoint/DNS/tunnel cutover, user-facing history and replacement UI remain
subsequent work. A restored older binding is refused by a fresh Agent; this is
safe rejection, **not automatic repair of restored command/config revisions**.

Retiring a record does not stop the old VPS. Retirement records explicitly keep
`old_stop_confirmed=false` and `traffic_tail_complete=false` until a later
coordinator can establish evidence. Unreported traffic cannot be invented.
This change does not implement leases that expire an unreachable old VPS.
No physical exactly-once Restart, provider-WAN, real multi-VPS handover, ACME
renewal, physical power-loss acceptance or production deployment is claimed.

## Tests

`tests/test_node_installations.py` exercises migration, retained account/Host/
usage state, first-byte accounting, rejected stale responses, header validation,
actual Hub request encoding against in-process Agent APIs, identity stability,
explicit Start after a rebuilt Agent, and encrypted central backup/restore.
FastAPI and SQLite are real; the sockets/TLS transport is replaced and Xray uses
the existing fake-core fixture. Separate CI tests are required for installed
services, real Xray and browser behavior on the exact published commit.
