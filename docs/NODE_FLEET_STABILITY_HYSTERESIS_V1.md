# DARK XRAY — Node Fleet Stability V1 (2026-10-09)

## Problem
Nodes were alternating between LIVE and STALE on the owner page despite Xray and Hub lease being active. All four enabled Nodes had prior Agent Watchdog restarts; Netherlands additionally had heavy CPU/load and thousands of connections. No node or inbound association had been deleted in the inspected live database.

## Fix

- Fresh telemetry uses **45 seconds** (three optional 15-second Node health probe periods) in the Hub and both Node views, instead of 20 seconds.
- Short HTTPS timeouts do not immediately mark a healthy Node offline or cause routing to flap: after 1–2 consecutive failures it is still reachable/degraded, while 3 failures confirm offline. Fatal installation identity, token authorization or TLS verification errors still block immediately. A genuinely old report (>180 seconds) is offline.
- Recovery/offline timestamps count confirmed transitions, not single transient misses. Actual Xray-not-running and invalid Hub accounting lease remain independent immediate health alerts.
- Strict Hub lease renewal no longer sends a **second full Node apply** when the current desired revision/hash already has a clean durable acknowledgement. Changed, pending, or failed configs MUST be reapplied and acknowledged; otherwise no lease is granted.
- This avoids unnecessary fleet-wide request pressure without relaxing metered checkpoints, quotas, the lease protocol, fail-closed security or the Node Watchdog.

## What does NOT change
No Node, client, UUID, inbound association, subscription, traffic ledger, Direct/Tunnel/SWAP mapping, agent binary, or Watchdog service setting is deleted or altered.

## QA
- New Python regressions for transient and confirmed outage, fatal TLS/auth/identity, 45-second threshold, persisted assignment retention, strict lease issuance under applied/pending/broken state.
- Node.js source contract checks keep both UIs consistent.
- Existing Hub lease, parallel monitor and Node API tests must continue to pass.
- Before rollout: full SHA256SUMS and CI, read-only backup of production assignment counts and client counts. Hub Python changes require a controlled panel-service restart; remote agents must not be restarted.

## Rollback
Restore backed-up four Python/JS files and restart only the Hub panel in a controlled window. The database and deployment state must not be rolled back or recreated.
