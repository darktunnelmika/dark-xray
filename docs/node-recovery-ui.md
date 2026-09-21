# Stale-backup recovery workspace — checkpoint 2D2

The owner can open **Recover after backup / بازیابی بعد از بکاپ** on the node
card or in Manage. It is separate from Replace server, uses the same Cyber Classic
palette, and supports English/LTR and Persian/RTL including narrow mobile layouts.
No recovery protocol, token rotation, deployment or production data migration is
introduced here: this workspace calls the existing 2D1 endpoints.

## Explicit actions

Opening, reopening or refreshing the dialog only GETs `/recovery/current`.
That endpoint reads the saved receipt without contacting the Agent. The UI never
uses that receipt as a current observation of whether Xray is running.

**Compare Hub and node** explicitly POSTs `/recovery/review`; the API only reads
local and authenticated remote checkpoints. It does not stop the node. The view
shows both command/configuration revisions, the observed core state and reasons
for a conflict. Normal pending work is not called a restore. A known unsupported
Agent, missing snapshot/pin or pending replacement gets an explanatory message.
No remote error text is incorporated into these messages.

**Confirm repair and Stop** requires both initially unchecked confirmations:
service interruption and stale/missing backup data. The browser sends the exact
reviewed binding/hash and strict boolean confirmations to `/recovery/stop`.
The backend rechecks the review; a changed command/configuration cannot use an
old confirmation. A status refresh discards the review and all checkbox states.

A saved pending operation exposes **Retry the saved Stop**, again with explicit
checkboxes; it calls `/recovery/{attempt}/retry`, not a new Stop or Restart.
It may be re-reviewed explicitly to repair changed inputs via the existing API.
There is no automatic retry, cancellation, synchronization, token rotation,
Start, delete, or enable action in this workspace. Closing/Escape only closes the
view; it cannot undo a request that may have reached the server.

## Failure and security boundaries

A network error clears the actionable view and asks for a GET of saved status.
HTTP 200 alone is not success: results are read back and validated for the selected
node, known phase, exact booleans and well-formed identifiers. Unknown or malformed
responses expose no mutation controls. Integers outside JavaScript's exact safe
integer range are rejected rather than displayed as trustworthy rounded revisions;
this UI does not enlarge the protocol's revision range.

Double clicks are blocked while busy. Late responses cannot revive a closed
view or a lost/changed owner session. All backend owner/session/CSRF/writes_enabled
checks remain authoritative. Read-only mode allows status and comparison only.
Node identifiers and diagnostic fields are HTML-escaped. Credentials, review
hashes, operation identities and consent are not stored in localStorage or
sessionStorage; only the existing panel language preference is read.

A completed receipt describes the earlier verified Stop and sequence repair, not
current liveness. It explicitly warns that configuration was not applied and
missing users/usage were not reconstructed. Later independent resume/enable is not
undone by reopening the dialog. Review restored users, expiry, limits and usage
before a separate synchronization and Start. A registry disable is not a firewall;
a contacted-later node may keep serving until Stop is actually confirmed.

## Test scope

`tests/test_node_recovery_ui.py` uses real Chromium, FastAPI/SQLite, a fake Xray
process and the existing in-process Agent transport. By default it navigates the
full Hub shell over HTTP under `/control`; it is not a public TLS/WAN test.
The dedicated `node-recovery-ui.yml` workflow requires this HTTP mode and runs the
existing recovery protocol tests plus the browser suite, publishing JUnit and
screenshots. It does not replace or weaken the existing CI workflows.

Managed local Chromium may prohibit loopback HTTP. `DARK_BROWSER_TEST_BRIDGE=1`
is an explicitly labelled DOM/API test harness, not full-panel HTTP evidence;
it leaves that browser restriction intact. No provider VPS, DNS, tunnel or old
server shutdown is performed. General pair/token journal recovery, missing-data
reconciliation and real-provider acceptance remain separate work.
