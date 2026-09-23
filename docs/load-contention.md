# Contended edits: unchanged metering writes

This is a narrow follow-up to the load failure recorded in Source Run 895 on
`8aabe9f`. That report completed creation/listing of 1,000 clients and 200 reads,
then recorded `ReadTimeout` before completing the 100-PATCH phase. It did not
retain per-request timings or server stacks. A retry passed. The investigation
below does **not** establish the sole cause of that historical timeout.

## Reproduced defect and product change

A normal client edit holds the Manager lock while reconciliation checks the
managed fleet. A stable client was metered twice during a tick; each observation
rewrote its policy total and advanced the local ledger sequence even without a
new local sample. Expiry and expected-enable values were also unconditionally
written. Sampled local stacks confirmed serialized edits and repeated metering;
this is not evidence of a deadlock.

For a disposable fixture with 100 initialized, unchanged, unscheduled clients,
one idle tick on the baseline affected 600 SQLite rows, advanced 200 sequences,
and grew its checkpointed WAL by 824,032 bytes. The corrected tick affected zero
rows, advanced zero sequences and added zero WAL bytes. These are affected rows
and measured WAL growth, not 600 independent disk flushes. The fixture uses the
project test engine and real SQLite, not real customer traffic.

The Manager now writes a meter total only when it changes, and advances its
local checkpoint/sequence only when local counters change or initialization is
needed. Expiry and expected-enable updates are null-safe conditional writes.
Every sample still reads current remote counters and checks overflow; there is
no local-counter-only early return. Ledger insertion and checkpoint changes stay
in one transaction. Reset observations, missing traffic, failed writes, quota,
expiry, manual disable and global security decisions remain checked. WAL/FULL,
locks, worker timing and API response contracts are unchanged.

## Load evidence and limitations

The original local 1,000-client/12-worker workload passed without reproducing the
historical ReadTimeout: 100 PATCH requests completed in 43.354 seconds. One run
with only the Manager correction took 41.882 seconds; the final diagnostic tool
run took 38.399 seconds (all 100 accepted; longest complete request 5.064 seconds).
These few observations are not a controlled speed benchmark or a guarantee that
the historical timeout is fixed. Full-fleet scanning and serialization remain.

The load report now identifies its current phase and records at most 100 PATCH
index/duration/outcome entries, without adding request bodies or credentials to
those entries. Timeouts remain errors, not successful retries. Counts, concurrent
workers and the existing 45-second PATCH timeout are unchanged. The final state
checks all 100 exact requested device-limit values, rather than a 20-client subset.
The same 64 unique resource-credit events and their idempotent retries, client
count, allocation total and SQLite integrity checks are retained.

Run from the repository root, in a disposable test environment:

```sh
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_meter_write_amplification.py -q
python tests/load-scale-smoke.py --clients 1000 --concurrency 12 --report qa/load-scale.json
```

The regression file contains 19 cases and is wired into `tests/run-tests.sh`.
The local checks use real HTTP/SQLite but the test engine. Exact-commit full CI,
real-Xray accounting/policy suites and the load job are separate acceptance
checks; their status must be verified rather than inferred from the local run.
No installed VPS, customer database, DNS, tunnel or firewall is modified by this
investigation. The known redundant writes are fixed; final attribution of the
old timeout, production performance and release acceptance are not claimed.
