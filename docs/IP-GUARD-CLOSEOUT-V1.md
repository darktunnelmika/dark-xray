# IP Guard / IP Limit Closeout V1 (rc37)

## Production audit

DARK exposes a primary Direct listener plus optional `dark-tunnel-*` shadow listeners. Existing Production logs proved that Direct listeners carry diverse end-user source addresses, while tunnel listeners collapse many clients onto a few tunnel peer addresses. A tunnel peer must never consume a customer's IP slot.

## rc37 behavior

- Xray access parsing records the inbound tag.
- Only primary Direct listener events may enter IP lease / violation / ban processing.
- `dark-tunnel-*`, unknown tagged listeners, and tag-less events while an opaque tunnel exists are fail-safe excluded.
- Node Guard root allowlists contain Direct Xray ports only; shadow tunnel ports are never nftables IP-limit targets.
- Direct source verification stays root-owned. The web panel cannot turn an opaque path into a verified one.
- Node `Enforce` intent is applied only to Nodes that have already reported verified Direct source visibility; unverified Nodes remain Observe instead of failing desired-state apply.
- Security Center reports Direct verification, full-path coverage, opaque tunnel ports, ignored opaque events, and per-Node Guard readiness separately.

## Central decision rule

Verified Direct observations are monotonic evidence. If the union of trusted Direct observations already exceeds `limitIp`, Central may safely block the client across synchronized DARK runtimes even when an opaque tunnel exists. An opaque path can only hide additional IPs; it cannot invalidate an already-proven excess.

Incomplete path coverage is never used to clear an existing global IP block and is never presented as complete accounting. Tunnel-only extra IPs therefore remain intentionally undetectable until the tunnel ingress can provide authenticated per-client original-source attribution.

This closeout does not change tunnel routing, add PROXY protocol, or run ad-hoc tunnel health probes.