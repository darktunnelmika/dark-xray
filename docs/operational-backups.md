# Operational recovery backups

Panel downloads and `darkxray backup` now produce operational backups. They preserve all operational tables, including users, current consumption, owner/period lifetime totals, credit ledgers, subscriptions, Node counters and sequence checkpoints, configuration, keys and managed TLS. The detailed traffic ledger is limited to its latest 250 rows in this backup only. Production history is never deleted by this feature.

The source of historical totals is `traffic_totals`, updated in the same database transaction as each traffic insert. Client activity and internal event sequence watermarks are also maintained transactionally. External API idempotency events are retained individually. Internal events at or below a restored watermark fail closed if their individual history is no longer present. Current meter checkpoints are copied consistently with these watermarks.

An existing installation must initialize its summaries once before requesting an operational backup:

```sh
sudo /opt/dark-xray/.venv/bin/python /opt/dark-xray/tools/seed_traffic_summary.py /var/lib/dark-xray/dark.sqlite3 /root/traffic-summary-seed.sqlite3 --install
```

The staging filename must not already exist. The initializer scans through a separate read-only connection, catches up concurrent appended events, then atomically installs the summaries and triggers only when its highwater equals the live ledger tip. The final write transaction has a two-second SQL progress budget. It never changes users, credits or historical records. New empty databases initialize automatically. Uninitialized existing databases refuse compact backup rather than silently dropping accounting history.

Compact snapshots use an independent WAL read transaction, copy operational tables and at most 250 ledger rows, and have a 60-second time budget. They never acquire the application accounting lock or physically copy the multi-gigabyte historical database. Downloads use temporary files cleaned up after response completion. Concurrent backup creation is rejected.

Encrypted compact archives use manifest schema 3 and database user_version 5. Older software refuses these formats. Install summary-aware software before recovery. Restoration remains isolated, revokes sessions, disables automatic core startup and applies the existing Telegram/domain recovery rules. Never restore a historical snapshot over the current live database as a source-code rollback.

For a separate complete historical archive, explicitly request:

```sh
sudo darkxray backup --full-history --output /root/dark-history.darkbackup
```

This archive includes all detailed traffic and can remain large and slow. Its streaming encryption supports existing archives without reading the whole database into memory. Operational backups and historical archives serve different recovery needs; the interface states the omitted history explicitly.
