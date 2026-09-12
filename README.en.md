[فارسی](README.md) | [English](README.en.md)

# DARK XRAY

Independent Xray control panel with a Persian cyber-dark interface, reseller ownership and server-side access control.

Baseline: `0.6.0-standalone-lab`.

> [!CAUTION]
> **SOURCE IMPORT REMAINS INCOMPLETE. DO NOT INSTALL FROM THIS REPOSITORY.**
> 64 of the original archive's 67 file paths are present. Three backend files remain absent:
>
> - `backend/guard_bridge.py`
> - `backend/guardd.py`
> - `backend/policy_api.py`

## Publication status — 2026-09-13

Previously accepted source objects were recovered. The repository now includes all original web assets, tools, tests, documentation, licensing, CI and deployment files, plus six backend modules: auth, backup, core, dark_policy, manager and server.

The omitted `tests/test_v06.py` was restored. A transfer difference in `tests/legacy/test_policy.py` and newline differences in three historical QA reports were corrected to the source archive. Application source has not been rewritten during this import.

The publishing tool blocked the remaining three backend uploads because it could not determine the requests' safety status. Repository write permission is available; publication nevertheless remains incomplete. No replacement stubs or bypasses are included.

See [PUBLISH-STATUS.json](PUBLISH-STATUS.json) for the exact archive identity, missing paths and verified directory hashes. Instructions in the development guide describe the complete source package; do not execute them from this incomplete checkout.

## Project architecture

DARK owns its UI, API, accounts and database. It does not require a Sanayi/3x-ui installation, API token, second login or embedded pages. Xray-core is supplied separately.

The development baseline contains client ownership on shared inbounds, reseller quotas and an independent traffic ledger, IP policies, TOTP, an initial installer and encrypted backups. Source presence is not a production-readiness claim.

## Validation and limits

Files under `qa/` are historical v0.6 development reports, not fresh validation of this branch. The historical 189-test result must not be presented as a current GitHub Actions pass. The initial Actions run for recovery commit `8316f19` failed in Repository hygiene and did not execute the subsequent test suite. Check the Actions tab for later run results; no successful current run is claimed here.

Real Xray proxy connectivity, kernel firewall effects, clean-VPS installation, live certificate issuance and load capacity remain unverified. Multi-node operation, global multi-node IP limits, migration and some advanced features remain incomplete.

`SHA256SUMS` is the unchanged ORIGINAL ARCHIVE manifest. It is not a complete-checkout certificate: verification against this branch will fail for the three missing files and the two intentionally updated README notices. The other 62 published archive files match their original bytes.

Do not publish real credentials, private keys, certificates, databases or unredacted logs. See [the Persian development guide](README.fa.md), [feature status](STATUS.fa.md), [security](SECURITY.md) and [third-party notices](THIRD-PARTY-NOTICES.md).
