[فارسی](README.md) | [English](README.en.md)

# DARK XRAY

Independent Xray control panel with a Persian cyber-dark interface, reseller ownership and server-side access control.

Baseline: `0.6.0-standalone-lab`.

> [!CAUTION]
> **INITIAL IMPORT IS INCOMPLETE. DO NOT INSTALL FROM THIS REPOSITORY YET.**
> The main branch now contains 22 of the original archive's 67 paths, with the two READMEs replaced by publication notices. Three backend files are present, but the remaining backend, web interface, tools, tests and CI workflow are still missing.

## Publication status

The normal retry of the earlier two-file upload was accepted. The following modules were uploaded without changing their source contents, and their Git blob hashes match the original archive:

- `backend/auth.py` — sessions, TOTP and scoped administrator API keys.
- `backend/backup.py` — encrypted backups and restore into a new destination.
- `backend/core.py` — standalone storage and direct Xray process management.

The next upload, `backend/dark_policy.py`, was stopped by the publishing tool with:

```text
This tool call was blocked by OpenAI because we couldn't determine the safety status of the request.
```

The message does not establish the exact cause. The blocked file has not been published. Full publication and successful GitHub Actions execution are NOT claimed. See [PUBLISH-STATUS.json](PUBLISH-STATUS.json) for the archive identity, verified hashes and remaining files.

## Project direction

DARK has its own UI, API, accounts and database. It must not depend on a Sanayi/3x-ui installation, token, second login or embedded pages. Xray-core is supplied separately.

The local baseline package contains reseller/client ownership, quotas and an independent traffic ledger, IP policies, TOTP, an initial installer and encrypted backups. Those components are not fully present here until the source import is completed; their presence in the local package does not establish production readiness.

The baseline remains experimental: real Xray connectivity, live firewall packet enforcement, fresh-VPS installation and load capacity have not been validated. Multi-node operation and some advanced features remain incomplete.

## Local revalidation

The original archive checksum checks and best-effort repository hygiene scan passed. Two complete Python test groups passed again: 78 legacy tests and 28 standalone tests. The 120-second execution limit was reached during the supervisor group, so a complete-suite pass is NOT claimed for this transfer. The leftover local test-double process was stopped. This is not a live Xray or firewall validation.

The installation entrypoints and uploaded modules require files that are still missing; do not run them. Do not publish real credentials, certificates, private keys, databases or unredacted logs.
