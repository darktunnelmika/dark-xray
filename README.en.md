[فارسی](README.md) | [English](README.en.md)

# DARK XRAY

Independent Xray control panel with an English-first cyber-dark interface, reseller ownership and server-side access control.

Baseline: `0.6.0-standalone-lab`.

> [!CAUTION]
> **The v0.6 source is complete, but this is still a Lab/experimental release and is NOT declared production-ready.**
> Repository CI is green, while live client connectivity, real nftables enforcement, certificate behavior across providers, reboot recovery and load capacity still require separate VPS validation.

## Online installer

For an Ubuntu/Debian systemd VPS:

```bash
curl -fL --retry 3 https://raw.githubusercontent.com/darktunnelmika/dark-xray/main/install-online.sh -o /tmp/dark-xray-install.sh
sudo bash /tmp/dark-xray-install.sh
```

The 1→100 installer distinguishes three states:

- **Clean** — fresh install
- **Partial / Failed** — preserve leftovers into recovery storage, then repair safely
- **Installed** — safe update or open the manager

The recommended `Domain + HTTPS/TLS` profile handles domain, panel port, owner, Xray-core and Certbot in one wizard. DNS/TLS failure no longer destroys an otherwise healthy installation; TLS can be retried later from `darkxray`.

After installation:

```bash
darkxray
```

## Cyber Control Center

The terminal manager includes live status, service control, logs, diagnostics, encrypted backups, owner-password recovery, domain/TLS, IP Guard, BBR, listening ports, nftables status, autostart and safe update with rollback snapshots.

Stored passwords, API keys and private keys are not printed by the manager, and disruptive operations require explicit confirmation.

## Account password policy

DARK XRAY account passwords now use a minimum of **8 characters** and a maximum of 512 characters. The same rule applies to owner, admin/reseller creation and password changes. Encrypted backup passphrases are a separate policy and still require at least 12 characters.

## English-first cyber UI

The web UI defaults to **English / LTR** and provides an EN/FA switch. A DARK-specific presentation layer adds a dark grid/scanline background, glassy panels, green/cyan glow, LTR sidebar geometry, clearer online/warning states and a hardened login surface without changing API, ownership or Xray behavior.

## Architecture

```text
DARK UI → DARK API / Access Control → DARK Database → Xray-core
                                              └→ IP guard (nftables)
```

DARK does not require Sanayi/3x-ui or another panel at runtime. Good installer/manager patterns such as staged setup, SSL management, updates and service control were used as UX references while DARK keeps its own UI, database, API and services.

## v0.6 baseline capabilities

- inbound/client management and explicit ownership on shared inbounds
- reseller client caps, traffic quotas and independent usage ledger
- authentication, sessions, TOTP and API keys
- per-client IP policy with a separate narrowly privileged nftables worker
- host metrics/dashboard
- native Host, Outbound and Routing forms plus advanced JSON
- encrypted backup and isolated restore
- standalone systemd installation
- Python/JavaScript tests and GitHub Actions on Python 3.12 and 3.13

## Validation limits

CI includes repository hygiene, installer runtime self-test, manager smoke tests, English/cyber UI smoke checks, JavaScript syntax checks and the isolated test suites. These checks use a test-double Xray and simulated firewall, so **green CI does not prove live VPN connectivity or kernel firewall enforcement on every VPS**.

Still required before production use: real Xray client connectivity, live IP-limit enforcement, quota disable/restore, certificate renewal, reboot recovery and load/concurrency testing. Multi-node operation and global multi-node IP limits remain incomplete.

See [Persian development guide](README.fa.md), [feature status](STATUS.fa.md), [security](SECURITY.md), [third-party notices](THIRD-PARTY-NOTICES.md), and [publication status](PUBLISH-STATUS.json).

`SHA256SUMS` represents the previous publication snapshot and should be regenerated for the next stabilized release/tag. For current `main`, rely on CI and the exact commit SHA.

Do not publish real credentials, private keys, certificates, databases or unredacted logs.
