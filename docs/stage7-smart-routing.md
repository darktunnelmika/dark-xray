# Stage 7 — Smart WARP + Smart Adblock Routing

Stage 7 starts as a safe preview layer. It does not mutate production traffic by
itself and does not restart Xray. The goal is to make WARP AI routing and
Adblock routing easy to review before any apply step.

## Goals

- Smart WARP AI: route selected AI domains through reviewed WARP outbounds.
- Smart Adblock: route known ad domains/geosite categories to the existing
  blackhole outbound tagged `block`.
- Smart WARP scan plan: prepare low-ping path ranking from collected probe
  results without making the backend perform disruptive live network changes.
- Node role suggestions: prefer USA/Germany nodes for WARP AI and France/UK
  nodes for adblock, matching the current DARK XRAY operating model.

## Safety model

- Default mode is `preview_only`.
- No production Xray restart is performed by the planner.
- WARP credentials are not generated or stored by this stage.
- Unknown outbound tags are rejected instead of silently falling back.
- Existing Stage 7 rules are replaced deterministically by `ruleTag` so duplicate
  rules do not accumulate.

## API preview endpoints

- `GET /api/smart-routing/plan` returns current node suggestions, available
  outbound tags, WARP candidates and active Stage 7 rules.
- `POST /api/smart-routing/preview` accepts selected WARP outbound tags and
  returns a reviewed routing/observatory preview. It does not save settings.
- `POST /api/smart-routing/warp-rank` ranks supplied observations by health,
  packet loss, median latency and jitter without applying anything.
- `POST /api/smart-routing/warp-scan` runs the isolated temporary-Xray path probe
  for up to 8 reviewed WireGuard/WARP outbounds.
- The Outbound page exposes a dedicated **Smart WARP AI** preview control for
  choosing WARP outbound/node paths.
- The Routing page exposes separate **Smart WARP AI** and **Smart Adblock**
  preview cards. Their actions show only the proposed rules/balancer/Observatory
  plan; they do not save settings or restart Xray.

Example body:

```json
{
  "warpAi": true,
  "adblock": true,
  "warpOutboundTags": ["warp-us", "warp-de"]
}
```

## Reviewed apply and rollback flow

Stage 7 now keeps traffic unchanged through the first three gates:

1. **Preview** builds the proposed rules, balancer and Observatory state without
   saving settings. The preview API does not return WireGuard secret material.
2. **Validate** compiles the complete candidate Xray configuration and runs
   Xray `-test`; neither settings nor runtime are changed.
3. **Review** re-validates the same baseline/candidate hashes and stores a durable
   revision containing the previous and proposed Routing/Observatory snapshots.
   This still does not apply anything.

Only the explicit **Apply reviewed change** action may change runtime traffic.
It is protected by a baseline hash so concurrent Routing/Outbound/Observatory
edits make the review stale instead of being overwritten. The candidate runtime
is validated and started first; reviewed settings are committed only after the
candidate runtime is accepted. If that commit fails, the previous owned Xray
generation is restored.

An applied revision exposes **Rollback**. Rollback is accepted only while the
current Smart Routing state still matches that revision's candidate hash, so
later operator changes are never silently overwritten. The saved pre-apply
Routing and Observatory snapshot is validated, restored to runtime, and then
committed atomically.

Existing Observatory values are preserved: Smart WARP adds its selectors and
uses defaults only for Observatory fields that were previously absent.
Only real `wireguard` outbound tags are accepted as Smart WARP paths.

### Per-Node role policy

Node roles are part of the same reviewed revision and rollback snapshot. The UI
suggests USA/Germany Nodes for **WARP AI** and France/UK Nodes for **Adblock**,
but the owner can override either role per Node before validation.

- A WARP-role Node receives the `dark-smart-warp-ai` rule and its Stage 7
  balancer; a non-WARP Node does not.
- An Adblock-role Node receives `dark-smart-adblock`; a non-Adblock Node does not.
- Nodes with neither role retain all non-Stage7 routing rules and do not receive
  either Smart Routing rule.
- Role membership is hashed as a set, so checkbox ordering cannot make a valid
  revision look stale.
- Applying or rolling back a revision refreshes Node desired-state payloads.

### Pre-Apply Safety Gate

A reviewed revision cannot be applied until the live Safety Gate passes. The
check itself does not mutate traffic or restart production Xray.

- Every selected WARP/Adblock Node must be enabled, online, free of pending
  desired-state drift, and report no explicit core/runtime error.
- Every selected WARP path is tested through the isolated temporary-Xray probe.
- Default limits are: loss <= 20%, median latency <= 1200 ms, jitter <= 350 ms.
- Adblock additionally requires the `block` outbound to be a real `blackhole`.
- A Safety PASS is valid for 300 seconds only and is bound to the reviewed
  candidate hash. Expired or failed checks keep Apply locked.
- Immediately before Apply the selected Node readiness is checked again. If a
  Node goes offline or becomes pending after the scan, Apply is refused.
- Safety reports contain only health/metric data; WireGuard secret material is
  never returned.

### Stage 7.1 Canary / Staged Apply

Safety PASS revisions use a persisted per-Node rollout instead of a fleet-wide direct apply.
The default sequence is **Canary → Verify → Batch → Hub last**.

- The first WARP-role Node and first Adblock-role Node are Canary targets.
- Every changed Node receives a rollout-specific candidate desired state while all
  not-yet-rolled Nodes stay on the baseline configuration.
- After each Node acknowledges the desired revision, DARK enters an observation window
  (5 seconds by default, configurable from 1–30 seconds) and re-checks Core/desired-state
  health throughout that window before proceeding.
- WARP-role Nodes additionally run an isolated Node-local WARP probe after apply;
  the same Safety Gate loss/latency/jitter thresholds are enforced again.
- If any Node fails delivery, convergence, health, or post-apply WARP verification,
  all Nodes already changed by that rollout are automatically restored to baseline.
- The Hub data plane is not changed during Canary/Batch. It is applied only after
  every Node verifies healthy.
- Running rollouts are persisted and automatically resume after Hub process restart.
- Node-targeted revisions cannot use the direct apply endpoint; staged rollout is mandatory.

### Stage 7.2 Failure-Injection Rehearsal

Stage 7.2 adds a disposable QA gate for the rollout state machine. It never targets
`/opt/dark-xray`, `/opt/dark-xray-node`, or the production database. The rehearsal
uses temporary SQLite/runtime directories and fake Xray processes, while exercising
real FastAPI lifecycle, persisted rollout rows, worker threads and Node desired-state
acknowledgements.

The dedicated suite proves four failure/ordering cases:

1. A Canary Node disconnects inside the observation window: the changed Canary is
   restored to baseline and the Hub remains unchanged.
2. A WARP path becomes unhealthy only after candidate apply: the Node-local probe
   exceeds Safety thresholds and triggers automatic rollback before Batch proceeds.
3. A successful rollout records every Node delivery before the final Hub apply,
   proving the **Hub last** invariant.
4. A running rollout persisted before a simulated Hub restart is reopened from the
   same SQLite database and automatically resumes to completion on the new app lifecycle.

GitHub workflow `Stage 7.2 Smart Routing failure rehearsal` stores bounded evidence:
`rehearsal.json`, JUnit XML, the exact source commit and SHA256 hashes of the rollout
implementation/tests. The evidence explicitly records `productionMutation=false`.

### Stage 7.3 Rollout Control & Telemetry

Stage 7.3 adds durable operator control and per-rollout telemetry without changing the
Hub-last safety invariant.

- **Pause** persists `pause_requested` in SQLite and the worker transitions to `paused`
  at the next safe checkpoint. Observation loops check control state every 250 ms while
  paused and do not advance to another Node or the Hub.
- **Resume** clears the persisted control state and can restart the rollout worker after
  a Hub process restart if the original worker is no longer alive.
- **Abort + Rollback** persists `abort_requested`; the worker acknowledges it before a
  Node apply, during the observation window, or immediately before Hub apply. Every
  Node changed by the rollout is restored to baseline and the rollout closes as `aborted`.
- A bounded **timeline** records rollout start, worker lifecycle, Node state changes,
  health samples, WARP probe metrics, pause/resume/abort requests, rollback completion,
  Hub-last transition and successful completion.
- Health timeline metrics include Node latency, applied revision/hash and Core state.
  WARP telemetry includes pass/fail, latency, loss, jitter and the thresholds used.
- The Routing UI exposes Pause/Resume/Abort/Timeline controls on the active rollout and
  keeps recent completed/aborted/rolled-back rollouts in a history card.
- The rollout list endpoint stays lightweight; full timeline data is returned only for
  an individual rollout or its timeline endpoint.

### Stage 7.4 Real Multi-Node Acceptance

Stage 7.4 runs the rollout against a disposable **five-process fleet**: one real Hub
process and four real Node Agent processes representing USA, Germany, France and UK.
Every service uses an independent SQLite/runtime directory, a real HTTPS socket and a
certificate signed by a disposable test CA. No `/opt/dark-xray`, `/opt/dark-xray-node`
or production database path is used.

The acceptance proves:

1. **Pause → real Hub process restart → Resume** keeps the persisted rollout paused
   across a different Hub PID, then continues Canary/Batch and applies Hub last.
2. A WARP Node whose post-apply probe degrades beyond loss/latency thresholds is
   automatically restored to baseline and the Hub routing remains unchanged.
3. Owner **Abort** during a real Node observation window restores every changed Node
   and closes the rollout as `aborted` without mutating Hub routing.
4. Killing the real Canary Node Agent process during observation makes the rollout
   fail closed. After that Agent process is restarted with the same identity/state,
   a normal Hub probe + desired-state sync restores it to the baseline Core state.

The network/process path is real; only two test adapters exist: Node origins are mapped
from allowlisted fixture DNS names to loopback because production SSRF policy correctly
refuses non-global Node addresses, and WARP path health is fixture-controlled because the
explicit fake Xray binary never proxies Internet/WireGuard traffic.

Stage 7.4 also hardened multi-path WARP probing. Hub-to-Node WARP checks now split tags
into bounded batches so every pinned HTTPS request stays within the Node transport's
30-second hard timeout, then merges results in the original requested order.

GitHub workflow `Stage 7.4 Real multi-Node acceptance` pins checkout to the exact candidate
SHA and stores acceptance JSON, exact source commit and SHA256 source evidence.

Additional endpoints:

- `POST /api/smart-routing/validate`
- `POST /api/smart-routing/review`
- `GET /api/smart-routing/revisions`
- `POST /api/smart-routing/safety-check`
- `GET /api/smart-routing/rollouts`
- `GET /api/smart-routing/rollout/{rollout_id}`
- `GET /api/smart-routing/rollout/{rollout_id}/timeline`
- `POST /api/smart-routing/rollout/start`
- `POST /api/smart-routing/rollout/{rollout_id}/pause`
- `POST /api/smart-routing/rollout/{rollout_id}/resume`
- `POST /api/smart-routing/rollout/{rollout_id}/abort`
- `POST /api/smart-routing/activate` (Hub-only/non-Node path)
- `POST /api/smart-routing/rollback`

## Real WARP path scan (Inbound)

- The Inbounds toolbar keeps a global **WARP Scan** action for owner users.
- Every Inbound editor also exposes a **Smart WARP** tab. The tab lists reviewed
  WireGuard/WARP candidates and renders scan results as:
  **Node / Region / Ping / Loss / Jitter / Status / Select**.
- Selecting a result is local preview state only; it is not written into the
  inbound, Routing, or Outbound settings in this stage.
- The scan accepts existing WireGuard/WARP outbound tags only.
- Each path starts a short-lived Xray child on `127.0.0.1` with a temporary HTTP
  proxy inbound and routes only that probe through the selected WireGuard outbound.
- The production Xray child is not restarted and DARK settings are not saved.
- Results are ranked by health, packet loss, median HTTPS latency and jitter.
- Scans are serialized globally and limited to 8 candidates / 3 attempts to avoid
  load spikes on the validated 1-vCPU small-production profile.
- WireGuard private keys and peer configuration are never returned by the scan API.

