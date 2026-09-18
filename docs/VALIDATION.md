# DARK XRAY validation gates

This file documents what each automated gate proves. Passing CI is evidence for the tested boundary only; it is not a claim that every hosting provider or network topology is production-validated.

- `tests (3.12 / 3.13)`: backend, RBAC, accounting, settings, update/rollback, Nodes and JavaScript regressions.
- `browser`: real Chromium UI flow, Inbounds V3 save, EN/FA direction, mobile overflow and refresh/focus stability.
- `real-core`: checksum-verified official Xray v26.3.27 with real SOCKS → VLESS → HTTP traffic, metering, reseller quota isolation and recovery semantics.
- `kernel-firewall`: real Linux network namespaces and nftables packets; TCP/UDP data-port drop, management-port preservation, native timeout, explicit unban and foreign-table ownership refusal.
- `systemd-recovery`: disposable install on production paths, non-root service user, `vps-verify`, SIGKILL auto-restart, clean stop/start and single owned Xray process after recovery. This does not claim a physical VPS reboot was tested.
- `load-scale`: measured high-client-count / concurrent SQLite and API smoke. It is not a universal capacity guarantee.
- `web-update-center`: backend regressions cover narrow ref validation, stable/RC tag selection, exact-commit CI gating, prechecked immutable-start semantics, interrupted-job recovery and interactive-owner-only API access. Browser QA renders the Update Center gracefully when the root broker is absent in the browser lab. The real-systemd gate installs/enables the root broker, verifies its Unix socket is readable by the `darkxray` service UID, and `vps-verify` treats broker readiness as a hard check.
- `legacy-update-compat`: Safe Update must remain fail-closed while accepting older installed Doctor output that predates `panel_route`. In that compatibility path, the updater independently probes the local panel UI and `assets/style.css`; both must return HTTP 200 before rollback snapshots or source replacement begin.
- `clients-v4-ui`: regression coverage verifies the reduced-noise command deck, OPEN/QR row actions, basic-first client editor, advanced-field gating, owner-profile defaults and owner-scoped group filtering; Chromium also opens the V4 create editor and checks the removed low-frequency fields are not exposed in the basic form.
- `clients-v3-presence`: regressions cover real-activity presence derivation from traffic/access/device timestamps, no source-IP leakage in presence payloads, organized Clients V3 filtering, and QR selection of the exact subscription/config payload.
- `reality-target-compat`: pinned Xray v26.3.27 rejects the documented `www.microsoft.com` REALITY target failure mode, defaults target search to compatible candidates, and blocks XTLS Vision on incompatible gRPC/XHTTP client transports.
- `control-center-v2`: terminal-control regressions cover explicit Login Owner selection, configured protected-port handling, staged-runtime visibility, fail-closed BBR capability checks, correct `dark_xray_ip` table routing, broker-authenticated Guard clear/status, exact-ref Safe Update routing, and encrypted backup → verify → isolated restore CLI flow.

The installed-server boundary remains:

```bash
sudo darkxray production-gate
```

Real provider-specific TLS renewal, physical/VM reboot, remote-node WAN behavior and IP Guard topology/source verification must still be validated on the target VPS before promoting `0.9.0-rc7` to a stable production release.
