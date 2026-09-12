[فارسی](README.md) | [English](README.en.md)

# DARK XRAY

Independent Xray control panel with a Persian cyber-dark interface, reseller ownership and server-side access control.

Baseline: `0.6.0-standalone-lab`.

> [!CAUTION]
> **INITIAL IMPORT IS INCOMPLETE. DO NOT INSTALL FROM THIS REPOSITORY YET.**
> The main branch currently contains project metadata, example configuration and part of the installation/service structure. The backend, web interface, tools, tests and CI workflow have not been published.

## Publication status

Repository write access and the initial README commit succeeded on 2026-09-12. A subsequent upload of two backend source files was blocked twice by the publishing tool with:

```text
This tool call was blocked by OpenAI because we couldn't determine the safety status of the request.
```

The message does not establish the exact cause. Full publication and successful GitHub Actions execution are NOT claimed. See [PUBLISH-STATUS.json](PUBLISH-STATUS.json) for the original archive identity and import status.

## Project direction

DARK has its own UI, API, accounts and database. It must not depend on a Sanayi/3x-ui installation, token, second login or embedded pages. Xray-core is supplied separately.

The local baseline package contains reseller/client ownership, quotas and an independent traffic ledger, IP policies, TOTP, an initial installer and encrypted backups. Those features are not fully present in this repository until the source import is completed.

The baseline remains experimental: real Xray connectivity, live firewall packet enforcement, fresh-VPS installation and load capacity have not been validated. Multi-node operation and some advanced features remain incomplete.

The installation entrypoints already present require files that are still missing; do not run them. Do not publish real credentials, certificates, private keys, databases or unredacted logs.
