# Node LIVE/STALE: separate health probes from transport reachability

## Root cause
The Hub refreshed remote_nodes.last_seen for **every** successful authenticated
Agent HTTP request (including traffic accounting / lease renewal). The Nodes
page and detail views used last_seen to badge cached system/CPU/RAM/Xray JSON
as LIVE even when an optional /node/api/health request failed repeatedly.
That allowed stale metrics to look current on a still-reachable Node and made
dashboard confidence and alert badges misleading.

## Fix
- Continue to use authenticated last_seen and existing 3-transient-error
  hysteresis for **transport connectivity and subscription route eligibility**.
  Existing assignment, failover, Node lease and accounting enforcement are
  unchanged.
- Use the existing indexed remote_node_metrics(node_id,captured_at) Hub table
  to discover the **last successfully persisted health sample** without sending
  any extra HTTP request or adding a database column.
- Expose health_sample_at, health_sample_age_seconds and health_sample_state
  alongside the existing telemetry_state and online fields. Nodes UI LIVE
  resource fields, fleet summary and detail view use the health sample state
  when available. Stale CPU/RAM/Load/Network fields display an em dash.
- Operational health/capacity uses the same verified sample freshness, so a
  stale sample produces a warning rather than stale-looking healthy metrics.
- Older installations with **no retained metric sample** continue legacy
  display behavior until the first successful health sample is captured.
  No automatic assignment changes follow from stale metrics.

## Regression tests
A disposable Node registry demonstrates that multiple healthy lease/traffic
HTTP acknowledgements do NOT mark an old CPU sample as fresh; only a newer
actual health sample restores LIVE. Two transient failures keep connectivity
and saved assignments; a third still blocks routing as before. JS checks
enforce the independent sample guard on both Nodes cards and detail views.

## Safety
Read-only metrics lookup uses the existing covering index and requires no Node
agent change, additional probes, service restarts, migrations, or public
endpoint changes. Refrain from deploying until the full CI suite passes.
