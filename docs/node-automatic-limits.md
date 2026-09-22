# 2G4: automatic multi-node policy and management-outage acceptance

This is part of the existing limits/transport milestone, not a new major stage
or permission to deploy. Adding this suite is not evidence of success: inspect
the exact candidate's JUnit, source identity and all CI results.

## Execution and isolation

`node-automatic-limits.yml` checks out the exact PR head and verifies the pinned
official Xray v26.3.27 with the existing downloader/provenance fixture. It then
creates a new network namespace. `node_automatic_lab.py` refuses the parent
namespace or any non-loopback interface BEFORE setting loopback/source addresses.
There is no external route, NAT, public listener, DNS change or firewall operation.
The two source IPs are the existing test-only namespace addresses.

Unlike the previous explicit-reconciliation suites, the new Hub serves a real
loopback HTTP socket. Both Agents serve separately authenticated HTTPS with
hostname/CA verification. The normal Hub and Agent application lifespans start
the actual Manager, NodeRegistry and EngineLoop workers. Configured poll_seconds
is 1; the production Node monitor's minimum interval of 5 seconds and initial
5-second delay are not bypassed or shortened. Existing product files and helpers
are unchanged. Two registered nodes are installed into the disposable registry;
this is not a test of initial pairing.

Test code never calls tick, flush, collect_stats, sync_traffic, sync_security or
manual Sync endpoints to drive progress. It changes user settings through the
normal HTTP API, sends real client packets, and waits using read-only persisted
state observations. The HTTP helper rejects accidental manual reconciliation
endpoints. Background workers alone pull statistics/security and deploy changes.
Each generated VLESS URI is consumed by a real Xray process with no direct/fallback
outbound. Every request uses a fresh nonce and an independent target-hit check.

## Covered cases when the exact run passes

* Combined per-customer quota across two nodes and top-up with cumulative usage.
* Future expiry reached by real wall-clock progression, with unlimited or limited
  volume, followed by extension using unchanged customer identity.
* Management TLS disconnection: the reachable node enforces quota, the other
  node's desired disable remains pending, and reconnect automatically imports
  retained counters and applies the disable without pressing Sync.
* Same source across nodes counted once; a second actual IP exceeds the global
  cap and removes that customer's authorization on both nodes. Raising the cap
  restores access. The product parses actual Xray access logs.
* Registered/self-declared HWID cap lowered and raised, without overriding an
  independent manual disable. This is not hardware attestation.
* Unchanged background rounds preserve core PIDs and do not charge snapshots
  twice. Explicit Stop is not undone by subsequent monitor/maintenance rounds.
* Every successful fixture verifies its actual Hub and Agent Thread objects have
  stopped after lifespan shutdown. A control UUID uses the same inbounds and
  must remain able to transmit when a different customer's authorization is removed.

## Deliberate negative boundary

Management loss does NOT grant the Hub an out-of-band way to stop an unreachable
Agent. The offline test requires the old direct URI to remain usable in that
scenario, even if subscription access is centrally blocked. This is diagnostic
coverage, not a promise or implementation of hard offline cutoff. Only counters
still readable on reconnect are recovered. No authorization leases, automatic
whole-node offline Stop, physical-device binding, instantaneous distributed
cutoff or in-flight session migration are introduced here.

The fixture uses two local Agents, not separate provider networks. Bounds on
convergence detect a failure to progress; they are not a latency or production
SLA. TLS customer transports were covered in 2G3; this scheduler suite uses plain
VLESS/TCP solely within the private namespace. Kernel address bans and opaque
tunnel source verification are not established by this suite.

Only allowlisted reports, JUnit, source hashes and core provenance are uploaded;
no private keys, tokens, temporary databases or raw client configurations. JSON
observations do not override failed JUnit or CI. The earlier load ReadTimeout
remains a separate pre-release investigation, not fixed by these tests.
