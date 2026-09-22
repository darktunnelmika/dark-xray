# Stage 2: real source-IP and declared subscription HWID acceptance (2G2)

This suite adds evidence within the existing limits/transport milestone. Creating
these files is not acceptance: exact-commit CI and JUnit must pass. No production
code changes or release approval are implied. Other transports, automatic
scheduler/offline behavior, ACME and provider-WAN remain separate acceptance work.

## Isolated topology

The dedicated workflow fetches the existing pinned official Xray v26.3.27 with
archive verification, then starts a NEW network namespace using `unshare --net`.
The bootstrap refuses a missing/equal parent namespace or any interface besides
`lo` BEFORE executing any network-configuration command. Only inside that new
namespace it brings up loopback and assigns 11.250.0.1/32 and 11.250.0.2/32. These
public-shaped test addresses have no public route, interface, veth, NAT or access
to their real owners. No host addresses/routes/firewall rules are changed.

Two real Agent-owned Xray cores, two separate SQLite stores and real SOCKS/VLESS
clients operate inside that namespace. Outbound `sendThrough` binds actual
source addresses; the product's unmodified parser imports real Xray access logs.
No observations, device rows, timestamps or usage counters are inserted manually.
A new test-only client binds its source address; all original helpers and
previous test assertions remain unchanged. Eight namespace-guard cases run in the
dedicated workflow before the real-core tests; 12 real-core cases are opt-in and fail, not skip, if the
namespace/binary/provenance prerequisite is missing.

Agent control uses the production pinned HTTPS client and temporary verified
certificates. Only exact disposable origins map to loopback. The Hub uses actual
FastAPI handlers through in-process TestClient, not a browser or real Hub socket.
Plain VLESS/TCP stays inside the private fixture. TLS/hostname checks and the
product's private-source exemptions are not disabled. The fixture operator marks
its directly observed Agent sources verified; the remote-only Hub does not need
to misrepresent its own source as verified.

## What the assertions establish when they pass

* The same source IP used on both nodes counts once. Two different actual sources
  (one per node) exceed a one-IP global allowance after security reconciliation.
* That global decision removes the restricted customer's authorization on both
  cores. A control customer on the same inbounds and even the same source IP still
  passes actual bytes. Increasing the limit preserves usage and credentials.
* An unverified observation cannot create a new global block or clear an existing
  block. The recent-IP window ages using real wall time, not a fake clock.
* Missing/short HWID headers are denied without allocating a device slot. A
  repeated claimed HWID consumes one slot; a new claim above the cap is denied.
* Two HWIDs are admitted through the real subscription handler at cap two. Lowering
  the cap to one triggers the global device policy; both Xray data paths are then
  rejected while a different customer still works. Raising/clearing the cap state
  preserves usage, identity and an independent manual disable.

## Important negative boundary: HWID is not hardware attestation

The HWID here is the self-declared `x-hwid` subscription header, not a physical
fingerprint authenticated by VLESS on each connection. A diagnostic test obtains
a valid URI as device A, denies device B's subscription request, and shows that a
new real Xray process can still use a copied URI. Repeating the same declared
header can also request the subscription. That expected diagnostic must NOT be
reported as prevention of config sharing or a strict physical-device cap.

Lightweight Agents do not expose the Hub subscription endpoint; this suite does
not fabricate Agent device telemetry or claim each VLESS connection reports an
HWID. It exercises the Hub's admitted device registry, global reconciliation and
actual two-node authorization effects. Device entries represent registrations,
not live simultaneous sessions. IP counts represent distinct recent addresses,
not exact concurrent people; shared NAT and source changes remain relevant.

The tested global IP response is per-customer authorization removal, not an
nftables address ban. Root-broker packet enforcement is a separate gate. Explicit
security reads/sync drive these tests: they do not prove instantaneous distributed
cutoff, automatic background timing, an offline authorization lease, or trusted
original IP behind opaque tunnels. No new offline-stop behavior is implemented.

JUnit determines success. Per-case JSON is only observation evidence. The artifact
contains source hashes, exact SHA, core provenance and reports, never temporary
private keys, Agent tokens, raw customer DBs or full generated client configs.

Primary source for the test client's source binding:
https://github.com/XTLS/Xray-docs-next/blob/main/docs/en/config/outbound.md
