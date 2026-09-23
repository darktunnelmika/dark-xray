# 2G3: two-node exported transport matrix

This is part of the existing limits/transport milestone, not a new major phase.
Adding this suite does not establish success; exact-candidate JUnit is required.
There is no release, main merge, installed VPS update, or production data change.

## Explicit matrix (16 cases)

| Protocol | Network | Security |
|---|---|---|
| VLESS | TCP, RAW, WebSocket, gRPC, HTTPUpgrade, XHTTP | TLS |
| VLESS | mKCP | none, default mKCP options |
| VLESS | TCP with Vision, gRPC, XHTTP | REALITY |
| VMess | TCP, WebSocket, gRPC | TLS |
| Trojan | TCP, gRPC | TLS |
| Shadowsocks | TCP, aes-128-gcm | native cipher; no outer TLS |

XHTTP is packet-up. Web paths/hosts and gRPC serviceName are non-default.
This is not every cipher, option, transport/protocol cross-product or client app.
SOCKS/HTTP management/proxy inbounds have no raw subscription generator here and
are not silently counted as covered. Custom mKCP seed/header options, additional
XHTTP modes, mux, UDP payloads, reverse proxies and provider tunnels are outside
this matrix. mKCP carries the same TCP request inside its UDP transport.

Each matrix case checks actual bytes through BOTH generated subscriptions,
nonzero real statistics and cumulative ledger import, duplicate-snapshot
idempotence, unchanged-sync PID stability, wrong credentials on both nodes,
and explicit disabling of only the subject customer. A second customer on the
same inbounds must still make successful new connections after the policy sync.
Five additional negative controls test bad TLS trust, wrong SNI, wrong WebSocket
path, wrong REALITY key and wrong short ID. Success/denial requires the inherited
fresh nonce response and independent target-hit check, not just an exception.

## Trust and topology

Two disposable Agents have separate databases, runtime identities and actual
Xray processes. The production pinned HTTPS transport is used; only exact test
Agent origins map to loopback and only the fixture CA is trusted. Hub API handlers
use in-process FastAPI TestClient, not a browser or live Hub HTTP socket.

A separate independent URI consumer reconstructs the Xray client outbound only
from the raw subscription output, not private server settings. It refuses
external addresses, insecure TLS flags and duplicate URI parameters. Clients
have one proxy outbound with no direct/fallback route. TLS clients explicitly
trust the disposable CA with hostname verification enabled; this does not bypass
certificate validation and no system trust store is changed. The Hub deploys
TLS files through its normal managed-file path to each Agent.

REALITY borrows a loopback-only Python/OpenSSL TLS 1.3 handshake target.
The target accepts raw TCP and handles each TLS handshake in its own bounded
worker; an unfinished peer cannot block other incoming TLS connections. Five
local TLS regressions cover concurrent stalled peers, bad trust/SNI and cleanup. It is not
an external website and does not prove production camouflage compatibility.
The target is not the nonce-bearing HTTP destination. Client-side REALITY
settings are derived from the exported pbk/sid/fp/sni/flow values; private keys
are not copied into client configs or evidence. Plain transport test traffic is
confined to private loopback. No public DNS, firewall or tunnel is changed.

## Execution and limits

The dedicated workflow checks out the exact candidate SHA and obtains official
pinned Xray v26.3.27 with the existing verified downloader. The inherited fixture
requires matching binary provenance; missing binaries fail, never skip or fake.

```sh
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_node_transport_helpers.py -q
DARK_REAL_XRAY_BINARY="$PWD/.test-core/xray" PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  python -m pytest tests/test_node_real_transports.py -q \
  --junitxml=qa/junit/node-real-transports.xml
```

JUnit is authoritative. Per-case JSON describes reached observations, not a
blanket pass. Source hashes, commit SHA and core provenance accompany reports;
no temporary database, TLS private key, token, client JSON or raw access log is
uploaded. Existing suites are unchanged and still required. Explicit sync is
not automatic scheduler acceptance, atomic distributed cutoff or an offline
lease. WAN, certificate renewal, copied-config/HWID limits, initial load-test
instability and the remaining release checks stay open.

Primary protocol references: Project X transport, TLS and REALITY documentation
at https://xtls.github.io/en/config/transport.html and
https://xtls.github.io/en/config/transports/tls.html and
https://xtls.github.io/en/config/transports/reality.html . The binary is pinned;
current documentation alone is not used as evidence of runtime compatibility.

## Initial fixture failure and required rerun

The first exact-head matrix at 53d499a passed 16 tests and failed all five
REALITY-related positive paths. A separate real-socket regression reproduced a
fixture defect: wrapping the listening socket performed the TLS handshake inside
accept(), blocking every other connection behind a peer that had not finished.
The fixture now handles handshakes concurrently, with the original matrix and
packet deadlines unchanged. This local regression is not by itself proof that
all REALITY failures are fixed; the new exact-head CI result remains required.
