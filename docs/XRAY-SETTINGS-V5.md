# Xray Settings V5 — audit and scoped closeout

## Baseline audit (2026-10-01)

Requested historical baseline: rc27 / `292f981c8ac660ab5a31a304bdb8084c33894142`.
Actual installed Hub and main at audit: rc29 / `1f6028cbbe282bf818f15530f87ef5295ed7b8f3`.
PR #76 had already merged. Development therefore starts from rc29 rather than rolling back its independent Node monitor and lease stability fixes.
Read-only production checks: dark-xray.service active; Restore subscriptions 517; promoted 0.

Already present and retained: one Xray Settings sidebar, server-specific WARP profiles,
pending registration with no default activation, consumer WARP endpoint candidates,
manual endpoint selection, isolated Xray probes, per-outbound Ping and source-server
selector, per-inbound/per-server Traffic Matrix, preview confirmations, and Node timeout
bounds. These features were not rebuilt.

Confirmed gaps: WARP entry required an inbound/matrix before server selection; no
Degraded result classification; incomplete remote responses hid missing endpoints;
Auto Best was a button rather than a default-off toggle; Routing hid server-scoped
policies behind the matrix; apply toasts could imply completion without checking runtime
acknowledgement; bulk Ping ignored unsuccessful non-exception probe results.

## Delivered behavior

Select Server -> Scan WARP Paths -> Results / Ping / Location -> Select Route -> Preview -> Apply.
First registration is staged, and its candidate default never becomes an active profile
without explicit selection and confirmation. WARP registration/scanning is available
without any inbound selection. Rescan and Change Route reuse the original scanner.
Manual is the default on every scan. The optional Auto Best toggle proposes the best
result from that scan and reveals a Preview button. It does not schedule background
scans, modify another server, or bypass Apply confirmation. Selected endpoints remain
stored in the existing per-server profile tables.

Every requested endpoint remains visible even when one batch fails or the Node returns
only one result. Scans use batches of at most four, preserving the existing Node request
limits and releasing its existing operation lock between requests. No Node Agent or
lease/monitor/tunnel implementation changes are needed.

Health is measured from an HTTP request through the WARP outbound, not an ICMP ping.
Healthy requires verified WARP egress, valid latency <=500 ms, zero measured loss and
jitter <=100 ms. Otherwise a usable verified endpoint is Degraded; unverified or
unusable endpoints are Failed. Missing metrics are not invented. Country/colo are the
observed egress trace; unknown location remains unknown, and different endpoints may
legitimately share a location.

The simple Routing / Ad-block editor explicitly selects Server, Inbound, Access Path
and a managed route (Direct, WARP AI, WARP All), with a separate Ad-block toggle.
Preview shows the exact scope and settings before Apply. Existing advanced shared
routing rules and arbitrary outbound editors remain accessible and are clearly labeled
as shared, not falsely advertised as scoped by the simple editor's server selector.
No listener ports, inbound deployments, Node identity or tunnel parameters are edited.

Applied/Pending/Error reflect Hub runtime state or Node desired/applied acknowledgement.
Saved state alone is not reported as successful application. Outbound Ping displays its
source server, and Ping All counts failed results as failures. These are outbound-only
probes, not tunnel health tests.

## Verification boundaries

`tests/test_xray_settings_v5.py` covers health classification, missing results, bounded
batches, exact Node origin, pending profile isolation, rollback, and acknowledgement.
`tests/xray-settings-v5.test.cjs` covers explicit selection, owner access, API failure,
manual toggle defaults, asset order and separate Ad-block controls.
`tests/xray-settings-v5-browser.py` exercises real HTTP, SQLite and Chromium with
explicit fixture WARP network/runtime calls. It proves the UI and persistence flow,
not live public WARP connectivity. It is run by the existing strict browser CI job.
Both browser labs isolate the update-broker socket from any installed service.
The existing full test suite and PR checks remain release gates; this document does
not assert that a build was deployed or that every live route is healthy.

Production rollout must preserve Restore users and PROMOTED=0, retain rc29 fixes,
apply no customer route automatically, and occur only after the exact PR candidate's
required checks are successful. No ad-hoc tunnel health test is part of this closeout.
