# DARK XRAY validation matrix

Current line: `0.8.2-standalone-lab`.

This document separates automated evidence from deployment-specific production claims. A green repository gate proves the named scenario in the CI environment; it does **not** automatically prove every VPS, provider or traffic topology.

## Automated gates on `main`

| Gate | What is exercised | Evidence class |
|---|---|---|
| Python 3.12 / 3.13 | API, RBAC, sessions, TOTP, accounting, settings, backup/update rollback, nodes, SSRF boundaries and regressions | isolated application tests |
| Browser QA | Chromium login/session, owner workspaces, Inbounds V3 save, EN/LTR ↔ FA/RTL, refresh/focus stability and 390px mobile layout | real browser + local HTTP/SQLite |
| Real Xray data-plane | checksum-verified official Xray `v26.3.27`, SOCKS → VLESS → HTTP, generated subscription credentials, traffic metering, reseller quota isolation, top-up/manual-disable semantics and reset/delete ledger behavior | real Xray process + real sockets |
| Kernel firewall | real Linux network namespaces + nftables, TCP/UDP drop, management-port preservation, native nft timeout, explicit unban and foreign-table ownership protection | real kernel packet path |
| systemd recovery | production-like `/opt`, `/etc`, `/var/lib` paths, `darkxray` service user, service enable/start, `vps-verify`, SIGKILL restart, stop/start recovery and single Xray child after recovery | real systemd service lifecycle |

The heavy real-core, kernel-firewall and systemd-recovery gates run on pushes to `main`. Evidence JSON is uploaded as GitHub Actions artifacts.

## Installation-side gates

Readiness-only inspection:

```bash
sudo darkxray vps-verify
```

Combined readiness + isolated data-plane proof using the Xray binary configured on that installation:

```bash
sudo darkxray production-gate
```

Machine-readable output:

```bash
sudo darkxray production-gate --json-only
```

`production-gate` is fail-closed. It combines read-only installation readiness with an isolated lab that uses temporary storage and loopback ports. The lab does not mutate the installed customer database or installed firewall rules.

## Evidence currently established

### Real Xray

The official pinned core is fetched through `tools/fetch-core.py`, which requires release SHA-256 metadata and does not fall back to an insecure mirror. The data-plane Gate exercises real client/server Xray processes and real sockets.

### Linux kernel firewall

The packet Gate uses disposable network namespaces. It verifies actual TCP and UDP packets are dropped for a banned client/port while a protected management port remains reachable. It also verifies nft timeout, explicit unban and refusal to take over a same-name table carrying a foreign ownership marker. The host namespace ruleset is not modified by this test.

### systemd recovery

The recovery Gate builds production-like DARK paths on a disposable Ubuntu runner, creates the restricted `darkxray` service user, installs official Xray, starts the real service, passes `vps-verify`, sends SIGKILL to the main panel process and requires systemd to recover it with a new PID. It then verifies normal stop/start recovery and exactly one owned Xray child.

`real_machine_reboot_tested` is deliberately false: service/process recovery is not the same as a host reboot or power-cycle.

## Still required on target production infrastructure

The project remains `standalone-lab` until deployment-specific gates are completed for the intended production environment:

- actual fresh installation using the published online installer on the target VPS image/provider;
- real machine reboot/power-cycle recovery;
- Let's Encrypt issue + renewal on the chosen DNS/provider path, including Secure Cookie/HSTS verification;
- IP Guard validation on the target traffic topology, confirming that the IP observed by Xray is the packet source seen by nftables;
- two real VPS Node deployments over valid public HTTPS, including network loss/recovery and convergence;
- measured load/scale tests for the intended client/inbound count and SQLite write concurrency;
- update/rollback rehearsal on a disposable VPS using the exact release artifact intended for deployment.

## Claim boundaries

- IP/device limits are observation-policy limits, not a mathematical count of people or physical devices.
- Kernel nftables CI proves DARK rule behavior in isolated Linux namespaces; it does not prove provider-specific tunnel/CDN/source-IP topology.
- Real Xray CI proves the DARK control/data path with official Xray on the runner; it does not replace target-region connectivity tests.
- systemd CI proves crash and stop/start recovery; it does not prove a machine reboot.
- Node SSRF hardening reduces application-layer egress risk but does not replace host/network ACLs.
- `SHA256SUMS` should be regenerated only for the exact finalized release/tag, not treated as evidence for a moving `main` branch.
