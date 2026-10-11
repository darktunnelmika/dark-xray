# HUB runtime Xray logs: bounded, independent rotation

## Production finding
On 2026-10-11 Turkey HUB kept a ~3.2 GiB
`/var/lib/dark-xray/runtime/access.log` with no dedicated rotation policy.
Available storage was initially ~20 GiB and growing runtime logs threatened
future SQLite and Node lease stability.

## Remediation
A root-only 15-minute oneshot timer checks `size 100M` for the HUB runtime
access/error logs. It keeps five rotations, uses compression and copytruncate,
and runs at idle IO priority. The timer is independent of panel, Xray, Guard
and all tunnels. It never edits a client or usage database. It does not alter
Node logrotate configuration.

Fresh installation installs the standalone timer; future Hub code updates
also install it. An older running updater may not invoke this new helper
on its first update; bootstrap it separately if needed. Absence of the
optional OS logrotate binary does not restart or abort a healthy panel.

## Production canary
A verified independent SQLite/source/config backup existed before any change.
Dry-run `logrotate -d` confirmed one oversized access.log. After enabling
the isolated service/timer, a manually triggered service rotated the 3.2 GiB
log into `access.log.1` and reset the active file to a few KiB in about
38 seconds. HUB PID and NRestarts=0 were preserved; all 742 Core clients,
225 managed and 517 Restore users and four enabled Node assignments remained.

This commit is code-only. The live timer was enabled separately with no HUB
restart, and this PR is **not authorization** to upgrade HUB source, update
the database, restart Xray or bypass the graceful-shutdown deployment gates.
