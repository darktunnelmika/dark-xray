# Node Lease / Watchdog hardening — October 11, 2026

## Incident evidence
- systemd killed healthy-looking Node Agents with contracted watchdog limits between 300 and 850 ms, while the guard thread waited a full second.
- Hub logged expired/stale accounting lease challenges and transient connection failures.
- Running production Agents are currently on mixed versions and mixed runtime source hashes. No destructive migration is part of this patch.

## Fix contract
1. Serialize each PID 1 watchdog notification with the very same lease lock that guards Hub acknowledgement, preventing an old subsecond deadline from arriving after a new grant.
2. Re-arm PID 1 synchronously after a successful authenticated lease acknowledgement and reconciled Node control, never on rejected challenges.
3. Use faster guard polling only when the already-bounded watchdog deadline contracts below the normal one-second cadence; do not extend the lease or suppress fail-closed fencing.
4. Skip a redundant full state apply only when desired/applied revision AND hash agree and there is no pending state or error. Strict snapshots, Hub quota/policy, challenge binding and revision checks remain mandatory.
5. Correct stale tests and isolate the system-root logrotate step from unprivileged rollback fixtures.
6. Reconcile source SHA256SUMS drift before running immutable release checks.

## Mandatory rollout gates
- All GitHub checks on the exact commit must pass (including real Xray, packet guard, OS watchdog SIGSTOP and installation rollback).
- Save a verified, isolated code + database + credential snapshot before a Node update; never restore older usage counters over newer ones.
- Canary only ONE low-traffic Node, compare Client UUIDs, inbound/node assignment counts, monotonic usage, Xray state, lease age and NRestarts before/after.
- No Agent restarts or feature migrations are authorized by the PR itself. Preserve customer Direct, Tunnel, DARK SWAP, WARP, fail-closed Guard and all accounts.
