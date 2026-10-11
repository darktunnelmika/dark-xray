# Remove redundant Node Apply and double traffic checkpoint (Issue #153)

Production monitor source still sent a full `/node/api/v1/state/apply`
and a second `/node/api/mirrors/traffic` EVERY 5-second cycle, even after
exact desired revision/hash had already been durably acknowledged. This
made otherwise healthy Nodes vulnerable to expensive queueing before
the next 30-second challenge ACK. PR #145 had eliminated duplicate Apply
inside `renew_accounting_lease`, but not this outer monitor path.

This surgical optimization skips the full Apply and the post-Apply traffic
snapshot only if:
- Node is enabled (original checkpoint is retained)
- Exact desired and already-applied revision + SHA hash match
- No pending desired, error, or pending control command
- The original first traffic snapshot has already imported current counters
  and policy callbacks still run before the Lease grant.

On changed/missing/error state, or pending Start/Stop/Restart, the complete
original sync and second checkpoint remain mandatory. Nodes with legacy
sync-provider-only mode also retain full original behavior. Final HUB
strict accounting + Node Lease remain unchanged; this optimization neither
extends a lease nor disables proactive Fail-Closed.

Strict disposable regression tests assert the skip and forced-Apply paths,
including no lost usage import. Keep Production unchanged pending CI, full
real Xray acceptances, and one low-traffic canary.
