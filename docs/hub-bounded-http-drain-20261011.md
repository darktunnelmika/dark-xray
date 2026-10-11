# HUB bounded graceful HTTP drain — issue #146

## Incident
On 2026-10-11 the production HUB entered "Waiting for connections to close"
during a reviewed code-only restart. Uvicorn waited for still-connected HTTP
clients without an explicit graceful drain timeout. Systemd then reached
TimeoutStopSec=60s, SIGKILLed the panel and its Xray child, and subsequently
started the updated service.

A separate read-only backup/after comparison found unchanged client UUIDs,
inbound state, Restore records, Node assignments, SWAP routes and monotonic
stored usage. This does not prove that the final few unflushed Xray bytes made
it to SQLite before SIGKILL.

## Reviewed code change
The production Hub uvicorn.run invocation now uses
timeout_graceful_shutdown=10.0, leaving up to 50s of the existing systemd
service timeout for FastAPI lifespan cleanup: Telegram workers, Node monitors,
Manager reconciliation and final owned-Xray statistics checkpoint.

TLS certificate/key, workers=1, ws=none, proxy_headers=false, panel route,
SQLite schema, Node lease enforcement, and customer configuration are unchanged.
No network firewall or service unit is weakened.

## Acceptance coverage
- Direct option test pins the production 10-second timer and all original
  listener/TLS/proxy settings.
- A real disposable Uvicorn child holds an HTTP streaming response indefinitely.
  SIGTERM must cancel the stuck HTTP request, run the FastAPI lifespan cleanup,
  and finish lifespan teardown. Uvicorn 0.48 deliberately re-raises the
  original SIGTERM after cleanup; this is not a SIGKILL.
- Existing real-Xray, update-rollback, database integrity, node security and
  strict source checksum tests are still mandatory.

## Limitations and rollout
Bounded HTTP drain is not a complete accounting correctness guarantee.
Shutdown may still fail if the core stats API, database, or independent
background worker never returns. Check final stats and account counters
before/after staging and one reviewed live HUB restart. Never restore an
older usage database over a newer production one.

This PR is code-only. It does not authorize a production restart, migration,
or any automatic fleet update.
