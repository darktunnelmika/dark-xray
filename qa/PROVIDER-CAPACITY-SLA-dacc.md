# DARK XRAY Provider Capacity / SLA Sizing

**Version:** 0.9.0-rc7  
**Release:** `v0.9.0-rc7-stage6-rollbackfix`  
**Commit:** `dacc4f1eab2c30dacedb6451d475293be94c98f9`  
**Result:** PASS for the current small-production RC profile. This is not a larger-fleet provider SLA.

## Current Topology

| Host | Role | CPU | RAM | Disk | Status |
|---|---|---:|---:|---:|---|
| Hub | control plane | 1 vCPU | ~1 GiB | ~20 GB | active |
| Node1 | node agent + owned Xray + Guard | 1 vCPU | ~1 GiB | ~20 GB | active |
| Node2 | node agent + owned Xray + Guard | 1 vCPU | ~1 GiB | ~20 GB | active |

## Validated Evidence

| Evidence | Result |
|---|---:|
| Stage 4 load acceptance | PASS |
| Stage 6 provider enforcement | PASS |
| Public Release fresh/update/rollback gate | PASS |
| Production Hub/Node sync | PASS |
| Production CA real renewal | PASS |

Stage 4 retained load acceptance completed 3/3 runs with 1,000 client records per run, concurrency 12, and 100/100 patch operations accepted and finished in each run. The observed max patch batch times were 10.429s, 9.885s, and 10.189s.

## Capacity Claim for Current RC Profile

### Hub / Control Plane

- Supported without retest: **2 enabled Nodes**.
- Supported without retest: **up to 1,000 managed client records**.
- Validated bulk operation envelope: **100 client patch operations per batch**, **concurrency 12**, max observed batch time under **10.5s**.
- Recommended operator/admin usage: **single active owner/operator session** for this small profile.

### Node / Data Plane

- Supported without retest: **1 remote inbound per Node**.
- Conservative allocation cap: **100 allocated clients per Node**, **200 allocated clients across the current 2-node fleet**.
- Guard/IP enforcement: supported in **enforce/applied** mode with direct source verification.
- WAN throughput is **not claimed** from this evidence. No bandwidth Mbps/Gbps SLA is claimed until a real throughput benchmark is run per provider/node.

## Operational Objectives

This is an RC operational objective, not a legal SLA.

- Control-plane availability target on current profile: **99.0% best-effort**, assuming monitoring and normal provider uptime.
- Service recovery objective: **5 minutes** for service restart/rollback recovery.
- Node offline detection objective: **60 seconds**.
- Node reconnect convergence objective: **5 minutes**.
- Certificate renewal objective: Certbot timer enabled; production CA renewal already tested successfully.

## Scale Triggers

Scale or retest before exceeding any of these:

- Hub CPU > 70% for 10 minutes.
- Hub RAM available < 200 MiB.
- Disk usage > 80%.
- More than 2 enabled Nodes.
- More than 1,000 managed client records.
- More than 100 allocated clients per Node.
- Multiple remote inbounds per Node.
- Any public bandwidth/throughput claim.

## Stable Status

The Provider capacity/SLA sizing blocker is closed for this current RC profile. Stable is still not tagged here; the next step is an explicit Stable promotion/tag/release decision.
