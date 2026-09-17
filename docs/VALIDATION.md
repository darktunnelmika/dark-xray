# DARK XRAY validation matrix

Current line: `0.8.2-standalone-lab`.

This document separates automated evidence from deployment-specific production claims. A green repository gate proves the scenario named below on the CI environment; it does **not** automatically prove every VPS/provider/topology.

## Automated gates on `main`

| Gate | What is exercised | Evidence class |
|---|---|---|
| Python 3.12 / 3.13 | API, RBAC, sessions, TOTP, accounting, settings, backup/update rollback, nodes, SSRF boundaries, recovery regressions | isolated application tests |
| Browser QA | Chromium login/session, owner workspaces, Inbounds V3 save, EN/LTR ↔ FA/RTL, refresh/focus stability and 390px mobile layout | real browser + local HTTP/SQLite |
| Real Xray data-plane | checksum-verified official Xray `v26.3.27`, SOCKS → VLESS → HTTP, generated subscription credentials, traffic metering, reseller quota isolation, top-up/manual-disable semantics, reset/delete ledger behavior | real Xray process + real sockets |
| Kernel firewall | real Linux network namespaces + nftables, TCP/UDP drop, management-port preservation, native nft timeout, explicit unban, foreign-table ownership protection | real kernel packet path |
| systemd recovery | production-like `/opt`, `/etc`, `/var/lib` paths, `darkxray` service user, service enable/start, `vps-verify`, SIGKILL restart, stop/start recovery and single Xray child after recovery | real systemd service lifecycle |

The heavy real-core, kernel-firewall and systemd-recovery gates run on pushes to `main`. Evidence JSON is uploaded as GitHub Actions artifacts.

## Installation-side gate

On an installed server:

```bash\sudo darkxray production-gate
```

or machine-readable output:

```bash
sudo darkxray production-gate --json-only
```

`production-gate` is fail-closed. It combines read-only installation readiness with an isolated data-plane lab that uses the Xray binary configured on that installation. The lab does not mutate the installed customer database or installed firewall rules.

For readiness-only inspection:

```bash
sudo darkxray vps-verify
```

## Still required on target production infrastructure

The project remains `standalone-lab` until deployment-specific gates are completed for the intended production environment:

- actual fresh installation using the published online installer on the target VPS image;
- real machine reboot/power-cycle recovery, not only systemd process/service recovery;
- Let's Encrypt issue + renewal on the chosen DNS/provider path, including Secure Cookie/HSTS verification;
- IP Guard validation on the target traffic topology, confirming that the IP observed by Xray is the packet source seen by nftables;
- two real VPS Node deployments over valid public HTTPS, including network loss/recovery and convergence;
- measured load/scale tests for the intended client/inbound count and SQLite write concurrency;
- update/rollback rehearsal on a disposable VPS using the exact release artifact intended for deployment.

## Claim boundaries

- IP/device limits are observation-policy limits, not a mathematical count of people or physical devices.
- Kernel nftables CI proves the DARK rule behavior in isolated Linux namespaces; it does not prove a provider-specific tunnel/CDN/source-IP topology.
- Real Xray CI proves the DARK control/data path with official Xray on the runner; it does not replace target-region connectivity tests.
- systemd CI proves service crash and stop/start recovery; `real_machine_reboot_tested` remains false until a real host reboot is recorded.
- `SHA256SUMS` should be regenerated only for the exact finalized release/tag, not used as evidence for a moving `main` branch.
