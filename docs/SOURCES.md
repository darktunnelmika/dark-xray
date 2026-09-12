# Primary engineering references / 2026-09-12

These are primary references used for interface and configuration design. Referencing a supported upstream command does not prove that this application's integration passed against a real upstream binary.

- Xray CLI: https://xtls.github.io/en/document/command.html — local `run -test`, process startup, `api statsquery`.
- Xray API: https://xtls.github.io/en/config/api.html — loopback API for the owned engine.
- Pinned core release: https://github.com/XTLS/Xray-core/releases/tag/v26.3.27 — GitHub connector metadata reported this as a non-prerelease release. This is an explicit installer pin, not a claim that its binary was available or validated in this build environment.
- nftables manual: https://netfilter.org/projects/nftables/manpage.html — inet tables, concatenated address/port sets, element timeouts, check-only command mode. Kernel packet enforcement has NOT been exercised here.
- Certbot user guide: https://eff-certbot.readthedocs.io/en/stable/using.html — HTTP-01 standalone and deployment hooks. Live certificate issuance/renewal has NOT been exercised here.
- systemd upstream manuals (implementation references): https://www.freedesktop.org/software/systemd/man/latest/systemd.exec.html — service identities/capabilities/sandboxing. Units are supplied; a fresh systemd installation was NOT performed here.

No 3x-ui/Sanaei API, binary, embedded page, database, or installation is required by this runtime. Third-party software notices are in `THIRD-PARTY-NOTICES.md`.
