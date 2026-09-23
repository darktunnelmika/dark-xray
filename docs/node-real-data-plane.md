# 2F3: isolated real-Xray two-node data-plane acceptance

This opt-in suite is separate from the fast/fake-core runner. Its dedicated
workflow checks out the exact candidate commit, verifies the pinned official
Xray v26.3.27 archive using `tools/fetch-core.py`, then runs:

```sh
DARK_REAL_XRAY_BINARY="$PWD/.test-core/xray" \
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
python -m pytest tests/test_node_real_data_plane.py -q \
  --junitxml=qa/junit/node-real-data-plane.xml
```

The executable must match its `DARK-CORE-PROVENANCE.json`. A missing executable
or mismatched provenance fails this suite; it never substitutes a fake core or
reports a skipped test as acceptance. The official downloader performs archive
verification; the test verifies the executable's recorded digest and version.

## Scope

Two real Xray server processes are owned by separate Agent runtimes with
separate SQLite stores. Two real Xray SOCKS clients consume VLESS URIs returned
by the Hub's actual raw subscription endpoint. The clients have no direct
outbound or fallback. Each HTTP request uses a fresh nonce, a 64 KiB response,
and an independent target-hit check. Negative cases require that the target
was not reached, not just that an exception occurred.

The Hub uses its actual FastAPI handlers through in-process TestClient; this
is not a browser test. Agent operations use Uvicorn HTTPS, the production pinned
HTTPS transport, distinct installation identities and disposable certificates.
Only exact test origins resolve to loopback. Certificate and hostname checks
stay enabled. Data-plane VLESS/TCP is unencrypted **only inside this private
loopback fixture**; this is not advice to deploy plaintext VLESS publicly.

Seven cases cover real bytes through both generated links, nonzero cumulative
statistics and ledger accounting, repeated-snapshot idempotence, wrong UUIDs on
each Node, Hub Stop/Start and link filtering, a management-only socket outage,
manual client disabling on both Nodes, and unchanged-configuration PID stability.
The no-background scheduler setup explicitly collects statistics from the real
Xray API before the normal Hub traffic-import API. No synthetic usage samples
or direct counter writes are used.

The two Nodes use two different logical inbounds/ports for the same customer
UUID because they share one host. Stop/Start is an explicit control operation;
it is not a forced provider network outage. The surviving link is opened as a
new client connection. No transparent migration of established sessions is
claimed. Management unreachability is explicitly distinguished from data-plane
failure: the old direct link can remain usable while being omitted from a newly
fetched subscription.

## Evidence and limits

JUnit is the authoritative pass/fail result. Per-case JSON records observations
and boundaries, not a blanket success flag. The artifact contains the candidate
SHA, source hashes, core provenance, JUnit and stage observations. No generated
TLS private keys, Agent credentials or installed customer databases are uploaded.

No main merge, release, deployment, firewall change or installed VPS mutation is
performed by the suite. It does not establish provider-WAN readiness, ACME
renewal, global IP/device enforcement, all transports, real server replacement,
or transparent traffic failover. Those remain separate acceptance steps.

Primary protocol references: Project X VLESS outbound and Statistics docs at
https://xtls.github.io/en/config/outbounds/vless.html and
https://xtls.github.io/en/config/stats.html.
