[فارسی](README.md) | [English](README.en.md)

# DARK XRAY

Independent Xray control panel with a Persian cyber-dark interface, reseller ownership and server-side access control.

Baseline: `0.6.0-standalone-lab`.

> [!CAUTION]
> **The source import is complete, but this is still a Lab/experimental release and is NOT production-ready.**
> Repository tests pass, while real Xray connectivity on a fresh VPS, live firewall packet effects, certificate issuance and load capacity remain unverified.

## Publication status — 2026-09-13

All required v0.6 source components are now present on `main`. `guard_bridge.py` and `guardd.py` were published byte-for-byte from the original archive. The large `policy_api.py` was split for reviewability without changing its tested API behavior:

- `backend/policy_auth.py` — auth models, permissions and bootstrap
- `backend/policy_routes.py` — FastAPI routes and HTTP access control
- `backend/policy_api.py` — compatibility exports and CLI entrypoint

The legacy API compatibility suite passes 20/20 after the refactor. The local isolated suite records 189 passing tests, and GitHub Actions completes successfully on Python 3.12 and 3.13.

## Architecture

```text
DARK UI → DARK API / Access Control → DARK Database → Xray-core
                                              └→ IP guard (nftables)
```

DARK does not require Sanayi/3x-ui or another panel. It owns its UI, accounts, database and API; Xray-core is the separate connection engine.

## v0.6 baseline capabilities

- inbound/client management and explicit ownership on shared inbounds
- reseller client caps, traffic quotas and independent usage ledger
- authentication, sessions, TOTP and API keys
- per-client IP policy with a separate narrowly privileged nftables worker
- host metrics/dashboard
- native Host, Outbound and Routing forms plus advanced JSON
- encrypted backup and isolated restore
- standalone installation and systemd units
- Python/JavaScript tests and GitHub Actions

## Validation limits

GitHub Actions is green on Python 3.12 and 3.13 for the completed source commit. Those checks use a test-double Xray and simulated firewall, so **green CI does not prove live VPN connectivity or kernel firewall enforcement on a VPS**.

Still required before production use: fresh-VPS installation, real Xray client connectivity, live IP-limit enforcement, quota disable/restore, certificate issuance, reboot recovery and load testing. Multi-node operation, global multi-node IP limits, migration and some advanced features remain under development.

See [Persian development guide](README.fa.md), [feature status](STATUS.fa.md), [security](SECURITY.md), [third-party notices](THIRD-PARTY-NOTICES.md), and [publication status](PUBLISH-STATUS.json).

`SHA256SUMS` is regenerated for the current repository state and excludes itself.

Do not publish real credentials, private keys, certificates, databases or unredacted logs.
