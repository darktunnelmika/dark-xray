# Node pre-expiry Watchdog safety — production incident 2026-10-11

## Why a second fix is required
Production Agents still restarted after PR #145 under systemd watchdog limits of **3–8 milliseconds**. The runtime updated `WATCHDOG_USEC` on every one-second guard tick as `remaining_lease - 1s`, including fractions of a second. A 6-ms OS deadline cannot be serviced by Python or the scheduler, so systemd SIGKILLed the Node Agent and its Xray child, even while the Hub could subsequently re-establish a lease.

## Fix and security boundary
- Never send a metered OS-watchdog notification inside the last ten seconds of the currently acknowledged 60-second accounting lease. No zero, fractional-millisecond, disabled, or unbounded timer while Xray serves traffic.
- When a timely new grant has not arrived, the independent LeaseGuard checkpoints final traffic and proactively stops owned Xray within that ten-second safety window, without writing a manual Stop, deleting any user or overriding the Node's desired intent. This is a safe **earlier stop**, not additional authority.
- On confirmed Xray Stop, explicitly restore the normal unbounded **management-only** OS keepalive so the Agent remains reachable for Hub recovery. A stopped Agent cannot serve unmetered customer traffic.
- Hub renewals are independently authenticated and must still validate policy, metering, pinned revision/hash and the fresh challenge. A renewed grant re-arms a bounded 30-second OS timer and can restart Xray after reconciliation.
- An engine that cannot be stopped remains subject to SIGKILL fencing before the grant expires. A frozen interpreter is still fenced by PID 1.

## Regression tests
- No subsecond watchdog notifications at 10s, 4s, 1.006s, or below; valid longer watchdog budgets preserve the original lease safety margin.
- Real fake-Xray Node: ten-second pre-expiry fence retains identity, intent and accounting; no OS watchdog trigger; a fresh authenticated grant recovers the customer process.
- Renewal just before the fence leaves the existing Xray PID intact; never stop healthy current leases.
- Existing replay/expiry, stopped-core, systemd SIGSTOP, real Xray data plane and Node updater/rollback gates remain mandatory.

## Deployment restrictions
This PR must first pass all exact-commit CI gates and disposable systemd tests. Never disable Fail-Closed Guard, extend the accounting lease, change Client or Tunnel/SWAP state, nor automatically deploy the PR by merging it.
Canary a low-traffic Node after a verified root-only backup and compare persisted identities, inbounds, usage monotonicity, lease recovery and OS restarts. Observe beyond the previous ~10-minute restart period before fleet rollout.
