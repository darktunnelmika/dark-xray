# Node accounting lease stability (0.10.0-rc29)

## Incident and fix

The Hub used one serial monitor for every enabled node. Network waits and policy/configuration work accumulated across the fleet before the next lease renewal. This could exceed the existing 60-second accounting grant and cause systemd to stop otherwise healthy node agents and their Xray children.

The Hub now runs exactly one non-overlapping monitor worker per enabled node, with independent start-to-start cadence and no fleet-wide completion barrier. Accounting callbacks cap the legacy polling interval at five seconds. Disabled or removed nodes retire their worker; cancellation is checked between operations and before a grant. The supervisor remains alive until its workers drain, preventing overlapping generations during shutdown/restart.

Traffic import, quota/security evaluation, desired-state acknowledgement and lease renewal retain their existing order and fail-closed conditions. The cycle is pinned to one installation identity. No lease duration, watchdog, firewall, tunnel, transport or subscription format changes are made. The change is Hub-side; no node restart is required to activate the scheduling fix.

## Diagnostics

Authenticated node-list responses include a `monitor` object with the current stage, cycle duration, cadence, last successful cycle, last successful lease callback and last failure stage/time. This state is in memory, resets with the Hub, and is separate from transport health so a successful HTTP probe cannot erase a failed accounting cycle. Unexpected exceptions are logged and retried on the next independent cycle rather than terminating monitoring.

## Regression coverage

`tests/test_node_monitor_parallel.py` covers blocked-node isolation, multiple blocked peers, non-overlapping workers, policy failure/recovery, shutdown and disable races, dynamic enable/add, lease ordering, interval validation and the accounting cadence cap. Existing accounting, replay protection, expiry, manual-stop and watchdog tests remain applicable. Production tunnel health tests are outside this change.
