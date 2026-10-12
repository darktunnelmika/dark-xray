# DARK XRAY Dashboard FastPath V2 — hardening / acceptance

**Scope:** dashboard read model, independent cards and safe page hydration.
**Status:** Draft / awaiting full CI and owner-approved canary. **Not Production-deployed.**

## Production facts verified read-only on 2026-10-12
- HUB: `dark-tr-01`, deployed source `c67e4add964c757af4a8f292229b58f976887349`.
- Frontend static assets return HTTP 200 rapidly; no missing indexed assets or JavaScript syntax errors.
- Original owner consumption aggregation scans historical `traffic_ledger` via `Store._usage`; a bounded real-host probe exceeded 1.3 seconds and was interrupted without writing.
- Real-host SQL summaries over `clients`, `remote_nodes`, `core_inbounds`, `owner_profiles` were sub-millisecond in individual isolated queries.
- Evaluating `DashboardReadModel` as a **read-only one-off Python process**, *without installing it* on the HUB, returned:
  - first pass: 8.24 ms
  - second pass: 3.73 ms
  - third pass: 3.63 ms
  These are local model execution times, **not** browser/API P95 measurements.

## V2 design
- `GET /api/dashboard/summary` is authenticated and role-scoped.
- Read-only SQLite URI connection, query-only mode, progress budget and short busy timeout. Never acquires the writer's shared `Store.lock`.
- Memory TTL cache per principal; an older valid snapshot is explicitly `stale`, otherwise 503. Database exceptions are not leaked to the UI.
- No migration, triggers, writes, ledger checkpoint, reconciliation, or background database job.
- Reported usage is **current stored client counters**. Historical/lifetime/period usage is **not inferred** and must not be conflated with `traffic_ledger`.
- `policy_eligible` is not online-presence; node `recent_heartbeat` is not data-plane route readiness.
- Web requests are GET-only, abortable and bounded in concurrency. The first Dashboard render waits on **zero** data APIs. Detailed Node/Core/backup/audit/update panels load independently.
- Full client and representative lists are not fetched on the Dashboard.
- Other workspaces receive their required data via route-specific hydration, without fake empty records.

## Production source drift — explicitly accounted for
Three frontend files on the live HUB differ from GitHub main at the same installed source SHA:
- `web/live.js`: route-aware loader, navigation hydration and slower polling.
- `web/ops-v2.js`: avoids duplicate full-load Dashboard navigation.
- `web/overview-v4.js`: handles representatives that were not preloaded.

V2 implements the route-aware behavior in `dashboard-fastpath-v2.js`, reduces the fallback poll in `web/live.js`, preserves the `ops-v2.js` fix, and replaces the representative placeholder with a cheap account summary. **Do not blindly overwrite production drift without canary parity review.**

## Non-negotiable safety
No modification of the running HUB/Nodes, accounts, Xray inbound/config, GRE/SWAP, routing, WARP, subscriptions, accounting ledger, or Lease. This patch must not start/stop/restart Xray.

## Required acceptance gates before merge / deploy
1. Source hygiene, exact SHA256 coverage, Python 3.12/3.13, JS and UI regression tests pass.
2. Fresh HUB and minimal Node Agent install pass, plus real-core and accounting/lease security checks.
3. Chromium browser and mobile smoke pass, including navigation away from Dashboard.
4. Owner and reseller authentication isolation, stale/timeout rendering, no invented zeros.
5. Disposable canary with live Xray; verify accounting snapshots unchanged, no Node re-pair/restart, and compare API latency under sustained load.
6. Measure actual browser P95 (target summary <500 ms; useful first data <2 sec), plus failure drills.
7. Explicit owner acceptance before merge or Production deployment. CI alone is not target-provider SLA proof.
