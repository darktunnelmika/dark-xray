# 2F4: complete replacement with real Xray in an isolated lab

This completes the **isolated replacement acceptance** checkpoint, not provider
VPS/WAN acceptance or deployment approval. The exact-head workflow verifies the
pinned official Xray v26.3.27 release with the existing downloader and runs:

```sh
DARK_REAL_XRAY_BINARY="$PWD/.test-core/xray" \
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
python -m pytest tests/test_node_real_replacement.py \
  tests/test_node_real_data_plane.py -q \
  --junitxml=qa/junit/node-real-replacement.xml
```

A missing or mismatched executable fails the inherited real-core fixture. There
is no fake-core or skipped-test fallback. Eight new cases and the seven existing
data-plane cases are run together. JUnit, not a stage observation, determines
acceptance. The dedicated workflow does not replace or weaken any previous gate.

## Topology and operator boundary

The lab has one Hub and three independent Agent installations: the old server,
a surviving node, and a fresh replacement. Each Agent owns its SQLite database,
runtime and real Xray executable. Two exact test hostnames and three HTTPS
origins use the fixture CA with hostname/certificate validation still enabled.
Only those disposable origins are mapped to loopback; production destination
validation is unchanged. Hub handlers use in-process FastAPI TestClient.

The old and replacement data listener reuse one loopback address/port. **The
test operator explicitly stops the old owned core to free that port** before
activation; the replacement protocol does not stop an old VPS automatically.
For the offline-management case the old core is stopped separately and its HTTPS
socket is aborted. No traffic after the old installation's last sampled bytes
is claimed recovered. The API's `old_stop_confirmed` and `traffic_tail_complete`
remain false even when this fixture knows that it stopped its own process.

The surviving node must remain usable with the same core PID. A new real SOCKS
client consumes the post-replacement raw subscription URI, using the inherited
fresh-nonce response and independent target-hit assertions. There is no direct
outbound or fallback. Plain VLESS/TCP is confined to private loopback.

## Assertions

- Full prepare, commit, stopped staging, review and conditional activation;
  distinct installation identity and exactly one retirement/new generation.
- Stable logical node, customer UUID, ownership, quota, expiry, assignments and
  subscription URL; real metered bytes and the ledger remain cumulative.
- No public subscription route or data listener before explicit activation.
  The replacement's first real bytes are counted and repeat snapshots are not
  charged again. Wrong UUID is rejected by the replacement's real VLESS core.
- Real TLS reply loss **after** a successful token rotation, configuration apply
  or conditional Start. ASGI responses are buffered and the actual socket is
  aborted after the handler completes; the production transport is not mocked.
- Lost Start can leave a directly reachable running core while the Hub still
  withholds the subscription route. Retry keeps the accepted command/PID and
  counts real bytes transferred during that uncertainty.
- Pause after lost Start captures real final counters, stops the target and
  permits a fresh review/Start without clearing past usage.
- Completed commit/activation retries make no target request and do not enable
  an administratively disabled node. A real earlier traffic sample presented
  with its retired installation context is rejected before accounting changes.

The final retired-context case models delayed **completion**, not a concurrent
network race. It uses an actual prior HTTPS sample, not synthetic counters.
Reports contain selected observations and source/core hashes; temporary private
keys, credentials, customer databases and raw HTTP bodies are not uploaded.

## Not established here

This does not prove physical multi-VPS networking, public DNS/tunnel cutover,
transparent migration of existing TCP sessions, renewal of ACME certificates,
global IP/device limits or every supported transport. Those remain the existing
separate acceptance checkpoints. No production server, main branch, customer
record, firewall or DNS configuration is changed by adding this test suite.
