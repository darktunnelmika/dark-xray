# P1 release: bounded HUB health check and online SQLite snapshot

## Production deployment blocker

2026-10-12 candidate preflight on a live Turkey HUB with ~1.7 GiB SQLite
failed the old updater's 15-second subprocess timeout in Doctor while
executing a full PRAGMA quick_check. The updater ALSO stopped the HUB before
performing another exhaustive quick_check and SQLite backup; that can break
the 60-second Node Lease and force all Xray Agents to stop.

## Bounded two-phase release

1. Read configuration and verify full SQLite integrity while HUB is still
   running (original database_preflight quick_check; unchanged).
2. Validate exact candidate source checksums, imports and isolated dependency
   environment; build source rollback snapshot while HUB remains live.
3. Perform sqlite3.backup to an **independent emergency snapshot** while
   HUB remains live. Verify this copy with full PRAGMA quick_check.
   All customer billing remains in the live SQLite DB, never the backup.
4. Only after snapshots pass: stop the HUB for the code/unit swap, then
   restart. Automatic rollback restores previous **source only** and never
   rewrites newer live usage (PR #152).
5. During availability checks, use a bounded read-only SQLite schema probe,
   validated Hub config, and real HTTP 200 for both UI and loaded style asset.
   Do **not** run another 1.7-GiB scan every restart attempt.
6. The interactive/full Doctor is unchanged and still performs full
   SQLite integrity verification when explicitly requested.

Before rollout, compare actual Core IDs, Restore IDs, group/inbound bindings,
per-Node metering totals and hashes before/after. A previously captured
747-client backup is **not** a safe replacement for a larger current live
database. If full preflight/snapshot fails, the HUB and Nodes are untouched.

This is code only; production deployment is separately gated by passing
full CI, independent verified backup, staging service-drain test and
ability to reactivate prior source without touching the live database.
