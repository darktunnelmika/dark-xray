# Telegram response latency (rc57)

On the rc56 HUB, the traffic ledger contained over 1.1 million events. Bot home
and service lists grouped that complete history while holding the shared database
lock. First-connection checks also searched the ledger without a client activity
index. A running core's configuration was rebuilt once per client in each list.

The fix adds a covering activity index, reads the latest traffic event for the
requested clients, and takes one current core state snapshot per client batch.
Single-client reads and the next batch still see current activity and config
changes. Existing ledger rows, accounting, quotas, activation rules, access
checks and credential redaction retain their behavior.

On the same private production database copy, a 72-client list with a running
core representation took 2.4063 seconds before the fix and 0.0241–0.0279 seconds
after it. Core config builds fell from 72 to 1. This measures server-side list
construction; it is not a Telegram client end-to-end response measurement.

Regression coverage checks bounded SQL work with a 20,000-event history,
existing-database index creation without ledger changes, fresh core state after
a config change, credential redaction, and the customer home response.
