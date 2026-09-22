# Stage 2: real two-node customer quota and expiry acceptance (2G1)

This suite covers the first part of the existing **limits and supported
connections** milestone. It does not close that milestone or authorize a release.
Global IP/device enforcement, transport coverage, ACME and provider-WAN acceptance
remain separate work within the previously agreed four remaining major stages.
Adding a suite or this document is not evidence that it has passed: the exact
candidate's JUnit and CI results determine acceptance.

## Run and evidence

The dedicated workflow checks out the exact PR head, uses the existing downloader
to verify pinned official Xray v26.3.27, and runs:

```sh
DARK_REAL_XRAY_BINARY="$PWD/.test-core/xray" \
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
python -m pytest tests/test_node_real_limits.py \
  tests/test_node_real_data_plane.py tests/test_node_real_replacement.py -q \
  --junitxml=qa/junit/node-real-limits.xml
```

Nine new cases run alongside the seven existing data-plane and eight replacement
cases. A missing executable or mismatched provenance fails the inherited fixture;
there is no fake-core or skipped-test substitute. Source hashes, candidate SHA,
core provenance, stage observations and JUnit are retained. Stage JSON is not a
blanket pass flag. Private TLS keys, tokens, temporary databases and raw customer
configurations are not published in artifacts.

The existing `xray_client` helper gains an optional `expected_uuid` argument whose
default is unchanged. The existing fleet also exposes its TestClient. Its seven
previous tests and all assertions remain present. This lets a second UUID obtain
raw subscription links through the same Hub HTTP handlers and share both logical
inbounds with the limited customer. No product code changes are made.

## What is asserted

* Combined volume: each node's actual user consumption remains below 110,000
  bytes but their sum exceeds it. Both old direct VLESS links must fail after
  reconciliation, while the second customer's links on both inbounds still work.
* Quota top-up restores the same credentials without resetting usage or refunding
  the traffic ledger. A repeated real-counter snapshot must not charge twice.
* Past-dated expiry blocks both nodes for both unlimited-volume and volume-limited
  services. Extending expiry restores the same UUID and preserves usage.
* Increasing quota must not override manual disabling or expiry. Extending expiry
  must not override an exhausted quota. Zero quota and expiry mean unlimited
  volume/no expiry; they do not mean immediate disabling.
* An HTTPS management outage is kept distinct from customer packet reachability.
  A desired disable is retained as pending when unreachable; after reconnect,
  both the disable and the previously unreported real bytes are reconciled.

All usage comes from real SOCKS -> VLESS -> HTTP transfers and Xray statistics,
not direct counter writes. Each response has the inherited fresh nonce and
independent target-hit assertion. The control customer shares the same inbounds,
so a global core failure cannot masquerade as correct per-customer enforcement.
The customer UUID, owner, inbound assignments and subscription URL remain stable.

## Important boundaries

Reconciliation is explicit: the no-background fixture imports Xray statistics,
invokes the normal Hub policy tick, and synchronizes each Agent through verified
HTTPS. This is not a test of the background scheduler or an instantaneous,
transactionally atomic distributed quota. Expiry is set to a real past timestamp
through the owner API, not reached by a fake clock or a wall-clock waiting test.

The offline case is a **diagnostic**, not proof of offline hard enforcement:
management disconnection can leave a direct link with its old authorization
working even after the Hub suspends the subscription. The test explicitly checks
that boundary instead of misreporting it as fail-closed. Only bytes that remain
available in the Agent counters are claimed recovered. No authorization lease or
automatic offline stop is implemented by this suite.

The two Agents and clients use separate processes/stores on one isolated host.
Agent TLS and hostname verification remain enabled; only the exact disposable
origins map to loopback. Hub HTTP uses in-process FastAPI TestClient. Plain
VLESS/TCP is confined to private loopback and is not a public deployment recipe.
No real VPS, DNS, tunnel, firewall, main branch or customer database is changed.
