# Node replacement workspace — checkpoint 2C5

The owner can open **Replace server** on a Node card or in Manage Node. The
workspace uses the existing prepare, retry, commit, cancel, stage, review, start
and pause APIs. It does not change DNS, tunnels or the old VPS. It is not a
packet firewall or a provider-WAN acceptance test.

## Resume without browser secrets

`GET /api/nodes/{node_id}/replacement/current` is a read-only alias of the
existing interactive-owner status route. It locates the active preparation or
the current binding's committed receipt, falling back to the latest terminal
receipt when there is no current committed binding. The response contains:

- `attempt`: the existing secret-free status, or null.
- `has_deployment` and `has_activation`: local existence flags, not liveness.
- `activation_resume`: only binding ID, phase and review hash of a pending
  activation on the current binding, or null. The digest is not an authorization
  token. Start still requires owner authentication, CSRF and four explicit
  confirmations, and the existing activation coordinator rechecks all inputs.

The read does not contact either Agent, decrypt credentials, create journal
rows, or repeat a mutation. A lost initial Prepare response is recoverable via
this read; the Pair Code does not have to be stored in localStorage or sessionStorage.
Backend journals and receipts remain authoritative after a browser reload.

## Interaction boundaries

Each mutation requires a user click. Refresh reads saved status only; it never
retries a POST. Only one action runs per open workspace. Buttons are disabled
while busy. Consent is never preselected and is discarded after refresh or
an action. Review data is memory-only and must be reviewed again before a new
Start. A pending Start can resume the same stored identity after reloading,
with fresh confirmation of the existing operation, not a newly generated Start.

Commit requires acknowledgement of unconfirmed old-server shutdown and missing
traffic. Cancel additionally requires acknowledgement that the unused target
will be discarded and needs reinstall or authorized local token reset for reuse.
Cancel is never offered as rollback after binding commit. Pending Start/Stop
use the activation pause/retry paths; a historical completed activation does
not expose a replay button that re-enables an administratively disabled Node.

A failed or ambiguous HTTP request clears local actionable state and asks the
owner to refresh saved status. A 200 status alone is not success: the UI reads
`prepared`, `binding_current`, `configuration_staged`, `review_ready`, and
`activation_completed`/`service_activated` according to each phase. A saved
activation receipt is described separately from recent observed readiness.
Remote text and addresses are escaped, never inserted as executable markup or
used to choose API origins. All requests use the panel's base-path-aware API.

The native modal dialog confines keyboard focus, supports Escape, and closing
it never implies cancellation. Both Persian/RTL and English/LTR use the panel's
Cyber Classic palette. The Pair Code field clears when submitted or closed.

## Verification scope

The current-operation tests use real FastAPI and SQLite. The browser workflow
uses the full panel, a real Chromium session and local HTTP under `/control`,
and the real Agent API through an isolated TestClient transport. Xray is the
existing fake fixture; it does not test public TLS, WAN, real customer traffic,
DNS propagation, ACME renewal or shutting down the old VPS.

For local environments where managed Chromium denies loopback navigation,
`DARK_BROWSER_TEST_BRIDGE=1` explicitly renders the workspace assets with a
minimal shell and an in-process bridge to the real API. That mode does NOT prove
full-panel HTTP integration. CI deliberately leaves it unset and requires the
normal full-shell HTTP tests. `DARK_TEST_CHROMIUM` selects an installed local
browser for tests only; it is unset in CI, which installs Playwright Chromium.

Remaining release work is unchanged: provider-WAN/multi-VPS acceptance,
old-server retirement/traffic-tail handling, stale-backup revision recovery,
and durable handoff for the general initial-pair/token routes. This checkpoint
is not approval to merge into main or deploy to production.
