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

Additional endpoints:

- `POST /api/smart-routing/validate`
- `POST /api/smart-routing/review`
- `GET /api/smart-routing/revisions`
- `POST /api/smart-routing/activate`
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

