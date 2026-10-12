# DARK XRAY — Performance & UI: bounded page navigation

Date: 2026-10-12
Scope: **frontend read-only page hydration only**. No database migration, billing,
Node lease, server runtime, tunnel, client configuration, or Xray change.

## Problem and verified cause

Dashboard FastPath V2 fixed the dashboard-first-paint path, but the older
non-Dashboard route loader still awaited every resource with no deadline. In
particular, the Owner/Representative API may calculate summaries from a
multi-million-entry traffic ledger. An operator navigating to Clients could be
held on the loading screen until that endpoint returned.

A second race allowed stale dashboard/widget renderers to paint an incomplete
target page while the required data was still in flight. Rapid double navigation
could send duplicate GETs; restoring a browser during a slow page transition
could return the operator to the previous page because the route was persisted
only after the network finished.

## Narrow fix

- All base-route GET reads and Clients group GETs have AbortController-backed,
  explicit deadlines (5–6 s) and an honest timeout message.
- Leave all mutating requests and all other feature-specific GET handlers
  untouched; no retries or network-side writes.
- Cancel old page reads when changing route; check both login identity and
  request generation to prevent old replies from overwriting a new page.
- Deduplicate in-flight route reads for the same account, page and API path.
- Back off expensive Owner metadata on Clients (5 minutes when a valid value
  was observed, 90 seconds after timeout). Explicit Representatives and Finance
  pages always refetch their authoritative summaries.
- Persist chosen sidebar page *before* starting network I/O. Repeated clicks
  on the same in-progress destination do not duplicate hydration.
- Fence late Dashboard and UI-Stability renderers so only the finished route
  can replace its truthful Loading state or clear its busy indicator.
- Invalidate all frontend read caches when identity changes or login expires.

## Testing

Run inside an **isolated** checkout, never against a live database or server:

```bash
node --check web/live.js
node --check web/clients-v2.js
node --check web/dashboard-fastpath-v2.js
node --check web/ui-stability.js
node --test tests/dashboard-fastpath.test.cjs tests/ui-stability.test.cjs
python -m pytest -q -s tests/test_performance_ui_browser.py
```

The optional Playwright suite spins up a localhost mock of the entire actual
index.html, serves all frontend assets, and drives **real Chromium** across
Dashboard, Clients, Nodes, Inbounds, Sync, mobile breakpoints (320–1440 px),
English–Persian language switching, route recovery on reload, a 16–20-second
delayed owner read, and double-navigation. No production credentials are used.

**Interpret synthetic localhost timings as regression measurements only, not
claims about actual production latency.**

## Production safety and rollout

Merge only after exact-head CI and focused browser QA pass; do not run full
production updater for a frontend-only fix without independent database and
Node gate. Production static deployment is a separate verified operation:
preserve original JS files, checksum candidate and served assets, do not restart
Xray/Nodes/Hub services, and validate account/page/session behavior. Any
remaining accounting ledger performance or Node-lease issues require their own
accounting-safe review, never DB reset, ledger truncation or VACUUM as a UI fix.
