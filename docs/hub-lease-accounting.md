# Hub accounting lease — 0.10.0-rc18

## Contract

An armed Node serves customer traffic only with a fresh accounting acknowledgement
from its Hub. The maximum grant is 60 seconds measured from the Node's traffic
snapshot, using Linux CLOCK_BOOTTIME (monotonic fallback). It is not a ping-based
heartbeat and it is not a promise of millisecond-precise network failure detection.
Both direct and tunnel listeners owned by the Node's Xray process are covered.
SSH, management credentials, client identities and configuration are not deleted.

Each Node checkpoints cumulative per-user upload/download independently at an
interval capped at 5 seconds. An unchanged Xray configuration no longer bypasses
this checkpoint. Normal reports use non-resetting Xray counters, and Hub imports
only their deltas under a SQLite transaction. A complete pre-apply Agent snapshot
seeds zero baselines for absent new mirrors, preventing loss of their first bytes.

## Grant and recovery ordering

1. The authenticated Agent traffic endpoint takes a strict durable checkpoint and
   returns a random single-use challenge with the complete cumulative snapshot.
2. The Hub imports usage, checks its accounting/policy engine, handles quota,
   expiration, representative credit and pending client state, then sends the
   resulting configuration to the Node.
3. Only after the current revision/hash is acknowledged may the Hub return the
   challenge with the matching configuration and ordered-control revisions.
4. A grant deadline is snapshot issue time + 60 seconds, never arrival time + 60.
   Challenges older than 30 seconds, unknown/out-of-order challenges, mismatched
   revisions and modified duplicates are rejected. Identical retries do not
   extend authority. Health, configuration pushes and Start/Restart do not grant.
5. After expiry, existing and new Xray connections are stopped. Recovery imports
   outstanding usage and applies current policy before eligible users resume.
   A manually stopped Node remains manually stopped.

## Independent process protection

A dedicated Node safety loop supervises expiry and provides systemd keepalives
while a durably acknowledged Hub lease has sufficient time remaining (or the
customer process is already stopped). Starting with 0.10.1-rc3, validation or a
temporary statistics delay cannot revoke an already-issued grant. The safety
loop checks that grant independently of the engine lock; it never extends its
deadline and stops feeding before remaining time reaches the OS watchdog budget
plus two seconds. A frozen engine therefore still cannot serve beyond the grant.
The production unit
uses WatchdogSec=30, WatchdogSignal=SIGKILL, TimeoutAbortSec=1 and
KillMode=control-group. Child Xray processes do not inherit the notification socket.
This does not depend on the HTTP route or an unconditional keepalive thread.
Normal shutdown retains its existing 60-second budget for final traffic snapshots.

The Hub's five-second per-node cycle prioritizes strict checkpoint, durable import,
quota/configuration reconciliation and lease acknowledgement before telemetry.
Security observations are read once per cycle after the grant; resulting blocks
are included in the next cycle's reconciliation. Health probes are limited to
once per 15 seconds, including failed attempts, and report their own error without
undoing the accounting result. This reduces repeated work; it does not make a
slow optional request fully independent of the following cycle. Exact executable
configurations matching the currently running validated hash reuse validation;
changed executable configurations still pass Xray validation. An unchanged
executable hash also updates the desired revision and metadata without stopping
the live process; strict pre-update counters and the live cumulative baseline
are preserved. No grant duration,
quota semantics, tunnel service or systemd kill policy is relaxed.

Xray remains an owned child of the Agent in this release. A genuinely failed
Agent or an administrative Agent restart still terminates its owned Xray cgroup;
separate process supervision needs its own durable accounting/recovery design.
No host firewall rules or unrelated services are modified by this feature.

The required-enforcement latch is persisted, but a grant is never persisted.
After an armed Agent restart or a server reboot, customer Xray remains stopped
until a new reconciled Hub grant arrives. New installations start armed. Existing
upgrades become durably armed on their first valid Hub accounting grant; deploy
the Hub first and verify every upgraded Node has `hub_lease.required=true`.
The lease implementation resides in existing Node runtime source files so older
Node updaters with a fixed source allowlist can safely install this release.

## Status and operating checks

Authenticated Node health exposes `hub_lease` (required, valid, state, remaining
seconds, last grant, trip count and last error), `lease_watchdog` and maintenance
checkpoint age/error. The Hub Node card labels this as the **last report**, not a
live countdown. An offline Node is explicitly shown without a fresh report.
States are `legacy`, `awaiting_hub`, `active` and `expired`.

A healthy route/HTTP response alone does not prove correct accounting. Verify
fresh Node checkpoints, active leases, current configuration acknowledgements,
no policy errors and agreement of Hub user totals with local plus remote usage.
A permanently failing final statistics query is recorded; it must not prevent
fail-closed shutdown. Repair the statistics failure before restoring service.

## Boundaries and evidence

Periodic checkpointing does not guarantee zero-byte loss during abrupt power
failure or an ungraceful Xray crash. The unsaved in-memory tail since the latest
successful checkpoint can still be lost. A lease is a bounded authorization
window, not a zero-overshoot globally atomic quota reservation. Very fast traffic
can exhaust a shared quota within one healthy synchronization interval.

`tests/test_hub_lease.py` covers replay/order/expiry, durable arming, restart,
manual stop, stats failures, independent checkpoints, notification constraints,
policy failure and zero-baseline accounting. These use isolated SQLite/FastAPI
and an explicit fake core; they are not a real packet proof.

`tests/test_hub_lease_real.py` uses the pinned official Xray v26.3.27, two
isolated Agents over certificate-verified HTTPS, real VLESS/SOCKS transfers and
already-open TCP echo streams. It verifies offline checkpoints, existing/new
connection closure, management availability, exact cumulative reconciliation,
no duplicate charge, quota-before-recovery and pending Start recovery. The focused expiry cases use an injected monotonic clock; an additional test waits
for the actual 60-second wall-clock deadline with live TCP streams and independent
Node loops. These remain isolated tests, not a production outage. The disposable
Node systemd CI also suspends the Agent with SIGSTOP and verifies that the OS
watchdog removes the entire owned Xray cgroup and restarts in awaiting-Hub state.


## rc18 updater permission boundary

The root update broker uses umask 077. Candidate venv creation and dependency
installation now use an explicit public-code umask, and permissions are normalized
only inside the new staged runtime. Interpreter symlink targets, state, credentials
and rollback permissions are never recursively widened.

Before stopping a live Node, the updater stages the exact install allowlist and
executes Python imports with the real darkxray UID/GID, no supplementary groups
and no user-site/PYTHONPATH inheritance. Failed execution/import leaves the running
Node untouched. A second service-account import checks the final installed path.
Fresh installation uses the same final-path check before enabling the service.
The root disposable CI update/rollback fixture now runs under umask 077.

Existing rc9 updaters cannot fix their own in-flight permission handling. For the
first upgrade, execute tools/update_node.py from the reviewed, exact rc18 commit
using system Python, with --ref set to that same immutable commit. It performs
normal snapshots, installation and rollback and installs the corrected updater
permanently. Do not chmod the live application recursively or delete its venv.
