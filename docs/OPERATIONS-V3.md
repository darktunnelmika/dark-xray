# Operations V3 — rc32

## Baseline and boundaries

Base: rc31 / a0c2c265fe24fa8ff67bf8dea82516323f6964ee.
Operations-only closeout. Licensing and its enforcement remain frozen. No change to
Node Agent, accounting leases, traffic routing, WARP selection, tunnel configuration,
Guard configuration, sales/payment semantics, or customer data. No new network probe.

## Audit findings

The first Ops draft attached a service panel to the legacy dashboard, which the later
Overview V4 module overrides. The renderer now runs inside the shipped Overview V4.
The draft also treated absent/stale observations as nominal and considered a saved
WARP profile evidence of health. The new statuses distinguish applied configuration
from connectivity: every WARP row explicitly says connectivity was not tested.
Browser acceptance also exposed fixed-width audit activity columns overflowing a
1024px tablet dashboard; their responsive layout is fixed without changing the data.

## Snapshot and UI

`GET /api/operations/overview` requires an interactive primary Owner session. It reads
local Xray runtime, existing Node health/desired-state acknowledgements, Telegram
stored activity and in-memory worker status, and WARP profile metadata. Registry
listing is performed once per request. It makes no Node/WARP/Telegram network request,
runs no scanner or reconciliation, and performs no database write.

Enabled Nodes expose applied/pending/error/offline/stale/stopped/unknown, last seen,
and last acknowledged apply. Disabled Nodes are excluded. Telegram disabled and
unconfigured are not treated as healthy. Online requires fresh observed activity and
an online runtime worker. Missing or future timestamps stay unknown. Errors are
bounded and credential-bearing URLs, bot tokens, Bearer tokens and keyed secrets
are redacted; full Node/WARP/bot credentials are never selected into the response.

The existing Overview keeps resource charts and the aligned Node/Update row. Four
compact Service Status cards are followed by expandable per-server/per-bot details.
A missing/failed or older-than-60-second browser snapshot is displayed as unavailable,
not green. Refresh issues only a GET. Owner data is not rendered for representatives
or after an account change. WARP records remain configuration/acknowledgement status,
not a claim of working Internet egress. No payment/provider health is inferred.

## Release evidence

`tests/test_operations_v3.py` covers permission denial, zero-write/no-probe semantics,
Node freshness/acknowledgement, Telegram runtime/activity, secret redaction, malformed
profiles and WARP status truthfulness. `tests/operations-v3-ui.test.cjs` exercises the
real renderer in a VM, missing/stale snapshots, owner cache separation and retry.
`tests/operations-v3-browser.py` uses isolated HTTP/SQLite and the complete application
shell in Chromium, English/Persian at 1440/1024/390/320 px, failed fetch/retry/staleness,
visible details and zero non-auth writes. Fixture telemetry is not Production proof.
These suites run in existing source/browser CI gates; gates are not removed or relaxed.

Deployment remains gated on the exact PR candidate CI. Production verification must
confirm immutable source identity, service/HTTPS, customer counts and unchanged
protected configuration. Rollback snapshots are created by the existing exact-SHA
updater. Never publish customer databases, private key material or raw access logs.
