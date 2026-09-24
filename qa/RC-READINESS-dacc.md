# DARK XRAY RC Readiness Report

**Version:** 0.9.0-rc7  
**Current prerelease tag:** `v0.9.0-rc7-stage6-rollbackfix`  
**Release commit:** `dacc4f1eab2c30dacedb6451d475293be94c98f9`  
**Release tree:** `b901a28e358d1e498b29851bf4ab4ee78ab24cde`  
**Status:** RC gates passed for prerelease. Stable is **not** claimed yet.

## Current Release

https://github.com/darktunnelmika/dark-xray/releases/tag/v0.9.0-rc7-stage6-rollbackfix

| Artifact | SHA256 |
|---|---|
| `dark-xray-0.9.0-rc7-dacc4f1eab2c.tar.gz` | `42f359c9162fd8ef85be3b7c76e7fbbe54c522745cd116733f02ab6c269f17c4` |
| `dark-xray-0.9.0-rc7-dacc4f1eab2c.zip` | `ecda6811d13d7a8da234909889fb920cfc4b243cbc13ba116af627744acd9489` |
| `dark-xray-0.9.0-rc7-dacc4f1eab2c.manifest.json` | `1214a2beaa0facc39334dbbeeb650f45f8f165c6076b64056f486497a2ba657b` |
| `SHA256SUMS.release` | `777659bb25677b9841d34f5e586e9da5b1a5b01807754a088c3661d6cb3211fa` |

## Gate Summary

| Gate | Result | Evidence |
|---|---:|---|
| Stage 4 final runtime replay | PASS | `/var/lib/dark-xray/qa/target-vps-gate.json` |
| Stage 6 provider enforcement | PASS | provider node/offline evidence |
| Public Release full gate | PASS | `/var/lib/dark-xray/qa/public-release-rollbackfix-full-gate-dacc.json` |
| Production Hub/Node sync | PASS | `/var/lib/dark-xray/qa/prod-rollbackfix-sync-dacc.json` |

## What Passed

- Stage 4 replay: reboot proof, node outage/recovery, source-IP observation, ACME staging HTTP-01 rehearsal, and retained load acceptance.
- Stage 6 provider enforcement: Node1, Node2, and offline/reconnect convergence.
- Public GitHub Release gate: download, checksum verify, `release-install.py verify`, fresh install, update, and rollback injection.
- Production sync: Hub, Node1, and Node2 updated to `dacc4f1eab2c30dacedb6451d475293be94c98f9`; fleet convergence remained healthy.

## Stable Blockers Remaining

1. Production CA renewal/issuance against the real CA path, not only staging dry-run.
2. Provider capacity and SLA sizing claim: bandwidth, CPU/RAM, node sizing, and operating limits.
3. Optional cleanup of test data on Node2.

## Scope Note

This report validates the current **prerelease RC**. It does not claim provider SLA sizing and does not claim a production certificate replacement. Do not mark this release as Stable until the blockers above are completed.
