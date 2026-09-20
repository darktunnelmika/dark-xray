[فارسی](README.md) | [English](README.en.md)

# DARK XRAY

Independent Xray control panel with a cyber-dark web interface, first-class inbound/client management, one primary owner, unified representatives, accounting, subscriptions, nodes and direct Xray-core control.

**VPS test candidate:** `0.9.0-rc7`

> [!CAUTION]
> DARK XRAY is now a **Release Candidate**, not a production-ready declaration. Real Chromium, official Xray, packet-level nftables, systemd crash recovery and a 1000-client/SQLite contention smoke are green in CI; target-VPS install, real machine reboot, provider TLS renewal and two-VPS node validation remain final gates.

## Architecture

DARK is not runtime-dependent on Sanayi/3x-ui:

```text
DARK UI → DARK API / Owner + Representative Scope → DARK Database → Xray-core
                                  ├→ Ledger / Policy
                                  ├→ Node control
                                  └→ IP Guard → nftables
```

Current major workspaces include:

- **Inbounds V3** with the main Xray protocols/transports, TLS/REALITY, sniffing, fallbacks and advanced JSON.
- **Clients + Groups V4** with a reduced-noise cyber command deck, quick presence controls, optional advanced filters, owner-scoped groups and real-activity Online/Idle/Offline presence.
- **Create/Edit Client V4** is basic-first: identity, inbound, plan, expiry and IP limit are primary; HWID/reset/comment/XTLS Flow stay under Advanced.
- **Reduced row actions** expose only `OPEN / QR`; IP/HWID and More Actions are removed from the primary client UI.
- **QR / Share V3** with independent QR payloads for the subscription and every generated config, plus copy and SVG download.
- **REALITY Guard** for the pinned Xray release: known-incompatible targets such as `www.microsoft.com` are rejected fail-closed and target search prefers compatible candidates.
- **Representatives V2** with one primary owner, unified login/profile management, allowed inbounds, traffic quota, client-count ceiling, optional enforced client prefix, Max IP/HWID policy and fixed server-side self scope. The Access Control / permission-matrix workspace is removed from the UI.
- **Account Security** with revocable sessions, replay-hardened TOTP and scoped API keys.
- **Settings V2** with staged privileged runtime changes and rollback-aware apply flows.
- **Finance / Ledger V2** with idempotent event IDs, current-period and lifetime usage, interactive-owner credit and audit traceability.
- **Xray Control V2** for DNS, outbounds, routing, balancers and observatory.
- **Nodes V5 / Lightweight Agent** with a separate agent-only install, one-time Pair Code, versioned Desired State, Inbound deployment targets, runtime-aware Public Endpoints, central traffic/security/IP-HWID policy, local Guard enforcement, and Hub-controlled Xray/logs/exact-version updates.
- **Dashboard Control Center** for interactive owners: the Update Center with Latest Verified / Stable / RC / Exact Ref, exact-commit CI gating, preflight, changelog, live progress, logs and automatic rollback now lives directly on the first page.
- **Root-owned Update Broker** separate from the non-root web process; only narrow status/check/start operations cross the authenticated Unix socket.
- **Backup / Restore / Doctor** are Hub-owned. A Full Encrypted Backup can be created from Web and includes SQLite, `secret.key`, panel TLS, managed Inbound TLS, Node registry and Desired State. Nodes require no independent management backup.
- **Cyber UI** with English/LTR default, Persian/RTL switching, responsive layout and refresh/focus stability.

## Online installation

### Main Panel / Hub

```bash
curl -fL --retry 3 https://raw.githubusercontent.com/darktunnelmika/dark-xray/main/install-online.sh -o /tmp/dark-xray-install.sh
sudo bash /tmp/dark-xray-install.sh
```

After installation:

```bash
darkxray
```

### Lightweight Node Agent on a separate VPS

```bash
curl -fL --retry 3 https://raw.githubusercontent.com/darktunnelmika/dark-xray/main/install-node.sh -o /tmp/dark-xray-node.sh
sudo bash /tmp/dark-xray-node.sh
```

The Node installer installs only the DARK Node Agent, Xray-core, Guard, updater and TLS runtime—no second Web UI, owner, finance or reseller runtime. It prints a bootstrap-only `DXN1...` Pair Code for **Main Panel → Nodes → Add Node**. The Hub rotates the Agent credential after a successful authenticated pair, consuming the bootstrap credential.

Both paths fetch the pinned official Xray release with verified release SHA-256 metadata.

## Installed-server gates

Read-only readiness inspection:

```bash
sudo darkxray vps-verify
```

Combined readiness + isolated real-data-plane test using the **same Xray binary configured on the server**:

```bash
sudo darkxray production-gate
```

Machine-readable output:

```bash
sudo darkxray production-gate --json-only
```

The data-plane lab uses temporary loopback ports and temporary data. It does not mutate the installed customer database or installed firewall rules.

Real WAN inspection of registered nodes from the Central VPS:

```bash
sudo darkxray node-wan-gate --json-only
```

For a real outage/recovery rehearsal, run watch mode and interrupt/restore the chosen node network outside the gate:

```bash
sudo darkxray node-wan-gate --watch-seconds 180 --expect-outage NODE_ID
```

The WAN gate uses the production pinned HTTPS/TLS node client. It does **not** inject the outage itself and only reports outage recovery as passed after it observes a real Down → Recovery transition.

## What is exercised on `main`?

- **Python 3.12 / 3.13:** API, owner/representative scope, legacy-role hardening, TOTP, accounting, settings, backup/update recovery, nodes and security regressions.
- **Browser QA:** real Chromium login/session, owner workspaces, Inbounds V3 save, EN/LTR ↔ FA/RTL, refresh/focus stability and a 390px mobile viewport.
- **Real Xray data-plane:** checksum-verified official Xray `v26.3.27`, SOCKS → VLESS → HTTP, generated subscriptions, traffic metering, reseller quota isolation, top-up/manual-disable semantics and reset/delete accounting behavior.
- **Kernel firewall:** real network namespaces and nftables with TCP/UDP drop, management-port preservation, native timeout, explicit unban and foreign-table ownership protection.
- **systemd recovery:** production-like paths, the restricted `darkxray` service user, enable/start, `vps-verify`, SIGKILL restart, stop/start recovery and a single owned Xray child after recovery.

See [docs/VALIDATION.md](docs/VALIDATION.md) for the exact evidence boundaries.

## Security boundaries

- DARK account passwords: 8–512 characters.
- Encrypted-backup passphrases use a separate policy and require at least 12 characters.
- Robot keys cannot own API-key lifecycle or sensitive finance mutation scopes.
- Representatives never receive sensitive finance mutation scopes; legacy role records remain sanitized only for migration compatibility.
- Node origins must be public HTTPS; redirects and environment proxies are not followed for node control requests.
- IP Guard enforcement should only be enabled after verifying that Xray's observed client source corresponds to the packet source seen by nftables on that same host.
- The global multi-node guard aggregates only source IPs reported as directly verified by each node and device SHA-256 digests; raw HWIDs are never transferred. A global violation disables the Central credential and its mirrors. nftables enforcement remains host-local and is not presented as a distributed firewall.

## One-time bootstrap for RC6 and older installs

A server that predates the root update broker needs one final online-installer Safe Update to this RC. That path executes the candidate updater itself and installs/enables `dark-xray-update.service`. Future updates can then be performed from the first-page Web Update Center without SSH.

## Remaining production gates

This `0.9.0-rc7` candidate remains pre-production until the intended deployment environment passes:

- fresh install on the actual target VPS image/provider;
- real machine reboot/power-cycle recovery;
- Let's Encrypt issuance and renewal on the target DNS/provider path, including Secure Cookie/HSTS behavior;
- IP Guard validation on the actual tunnel/CDN/source-IP topology;
- two real VPS nodes over valid public HTTPS, including `darkxray node-wan-gate`, Traffic/Security sync, failover readiness, reset coordination, and a real observed network-loss → recovery transition;
- capacity validation on the intended VPS plan; CI already passes a 1000-client and SQLite-contention smoke, but that is not a provider capacity guarantee;
- final update/rollback rehearsal with the exact release artifact to be deployed.

## Documentation

- [Persian guide](README.fa.md)
- [Detailed status](STATUS.fa.md)
- [Validation matrix](docs/VALIDATION.md)
- [Security](SECURITY.md)
- [Third-party notices](THIRD-PARTY-NOTICES.md)
- [Publication status](PUBLISH-STATUS.json)

`SHA256SUMS` and archive checksums are regenerated only for the final fixed RC/tag snapshot; older checksum files are not treated as RC7 evidence.

Do not publish real credentials, private keys, certificates, databases or unredacted logs.
