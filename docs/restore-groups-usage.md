# DARK RESTORE: groups and usage since migration

Restore imports are separate from native Clients and Representatives. A group is a
migration/reporting label, not a new login, permission grant or credit account.

## Operator workflow

Open DARK RESTORE and choose Scan / import subscriptions. Choose an existing group
or enter a new representative/group name. New subscriptions start with zero DARK
usage even when their old provider reports substantial consumption. Each group card
shows its current member count, migrated count, and sum of its members' DARK usage.
Select a group to filter its clients. The list supports searching and pages of 50.

Existing records are placed in `Ungrouped / previous imports` during upgrade. No
representative is guessed from an IP address, domain or URL. Select customers (or
all filtered results) and use Assign selected to group. Group reassignment and
renaming preserve UUIDs/passwords, subscription tokens, targets, quota, expiry,
migration timestamps and recorded usage. Reassignment moves the member's entire
DARK usage history into the selected group; group totals describe current members,
not a financial settlement ledger of historical group memberships.

## Accounting contract

* `legacy_upload`, `legacy_download`, `legacy_total` and `legacy_expire` retain the
  old subscription metadata. They are not included in the main usage column or
  group usage totals.
* `dark_used` is the recorded local plus Node usage of the identity created in
  DARK. The UI shows local and Node components separately. `first_seen` records the
  first successful migrated subscription update; waiting clients normally show 0.
* `effective_used = legacy_used + dark_used` is retained for quota compatibility.
  Remaining volume and the existing subscription-userinfo header continue to use
  this relationship. This change does not grant a fresh full quota or extend expiry.
* Local usage is seeded once from existing Core counters and then accumulated by a
  transactional SQLite trigger. Counter resets do not erase already recorded DARK
  usage. Node snapshots are observed after native NodeRegistry processing and only
  Restore identities on assigned inbounds are recorded in a separate table. Native
  owner traffic/credit ledgers are not touched by Restore accounting.
* The first cumulative Node sample belongs to the unique identity created in DARK.
  Repeated snapshots are idempotent; lower counters indicate a reset and add only
  their new value. Older captured snapshots are ignored. No system can reconstruct
  traffic that was lost before it was observed or persisted; these totals represent
  the recorded local/Node data, not fabricated historical usage.

After migration (or a verified scan), reimport preserves the original legacy
snapshot instead of fetching the now-taken-over URL and accidentally counting DARK
usage a second time as old-provider traffic. A failed/incomplete rescan cannot erase
previous quota metadata. A URL already assigned to another group is reported as a
conflict and left unchanged; explicit group assignment is the move operation.

## Storage and safety

`restore_groups`, `restore_subscriptions.group_id`, and `restore_usage` live inside
the existing Hub SQLite database and therefore travel with its database backup.
The upgrade is additive and idempotent. No Node reinstall, firewall change, routing
change or native representative creation is required. Group management endpoints
remain owner-only and retain the existing CSRF/write guards.

Tests: `tests/test_restore_groups.py`, `tests/dark-restore-groups.test.cjs`, and
`tests/restore-groups-browser.py`. The browser suite uses real HTTP/login/SQLite
with isolated data and deterministic old-provider metadata; it does not contact
customers' legacy providers or alter production.
