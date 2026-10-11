# P1 — refreshed accounted challenge under slow HUB policy (Issue #153)

The previous lease renewer held a Node-issued 30-second challenge while
performing potentially expensive HUB Xray stats, quota, client policy,
configuration and control checks. On busy hosts this token may be rejected
as expired before the final HTTPS acknowledgement.

The HUB now records a conservative monotonic age at the START of the original
authenticated /mirrors/traffic GET. If the age exceeds 18 seconds before POST,
it must acquire a new challenge through a full traffic checkpoint, not via a
lightweight unmetered endpoint. Fresh traffic is durably imported and matched
to native/Restore identities. If new bytes were charged, the strict HUB
stats/quota/policy check runs again. Desired config/hash, controls and policy
must be acknowledged before POST. At most two refreshes are allowed. An
authenticated Node's exact stale-challenge 409 can also invoke this bounded
re-accounting path; all other HTTP, identity, TLS and policy failures deny.

Token expiry remains 30 seconds and maximum authority 60 seconds. Node
watchdog, Fail-Closed and Guard remain unchanged. Warnings contain timing
metadata and node ID only; no bearer tokens or customer identities.

This is a code-only candidate. Requires tests and a low-traffic canary after
separate valid backup. Do not restart production for this PR alone.
