# Immutable live usage on failed HUB / Node updates

## Previously unsafe behavior
Both source updaters captured a correct SQLite pre-update snapshot, then
automatically restored **that older database** if candidate activation failed.
If the new Agent or Hub accepted additional metered traffic before the failure,
this could erase valid accounting, customer changes and Node activity.

## New fail-safe invariant
After a failed candidate activation:
- Stop candidate service before code/environment rollback.
- Keep live /var/lib/dark-xray/dark.sqlite3 (HUB) or
  /var/lib/dark-xray-node/node.sqlite3 (Node) intact.
- Restore previous source (and Node virtualenv/source identity), restart with
  old services, verify health; do not use the snapshot to replace usage.
- If older code is incompatible with new database schema and cannot restart,
  flag a critical error and require manual operator recovery; **never**
  rewrite the live DB with an older snapshot to hide the failure.
- Keep source and SQLite snapshots independent for auditable manual recovery.

This change does not authorize live rollout. Production services must have
valid external backups and strict canary tests before upgrades. An admin
restoring a stale snapshot knowingly may still lose newer data; the automatic
updater must never do that. Source revisions with incompatible migrations
require a reviewed forward-compatible recovery procedure.
