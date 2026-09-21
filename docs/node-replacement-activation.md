# Replacement activation — 2C4B

Source checkpoint based on `40758e41673934842fcea7f8d67d96090944ceea`.
This is an explicit owner workflow, not provider-WAN acceptance or a deployment script.
No replacement UI is introduced here. No DNS, tunnel, public Host address, or old VPS is changed.

## Owner API

All POST endpoints require an interactive owner, CSRF, and `writes_enabled`.
API keys and non-owner accounts are not authorized. The prefix is
`/api/nodes/{node_id}/replacement/{attempt_id}`.

* POST `/activation/review`: `{ "bindingId": "<32 hex>" }`.
  Restages and validates the committed disabled installation in the stopped state,
  refreshes Hub policy, re-reads TLS material, and requires conditional activation v1.
  Returns `review_ready`, `review_hash`, and a public endpoint plan. This POST can
  perform stopped configuration work; it is not a read-only preview.
* POST `/activation/start`: requires `bindingId`, `reviewHash`, and all four
  **strict JSON true** confirmations: `confirmStart`, `acceptEndpointResponsibility`,
  `acceptUnconfirmedOldServer`, `acceptUnreportedTraffic`.
  First use repeats stopped validation. Changed input requires a new review.
  An unfinished Start uses the same persisted command identity on retry.
* POST `/activation/pause`: `bindingId` and strict `confirmStop:true`.
  Records a superseding Stop before I/O, verifies Stop and stopped health, meters
  the final available traffic, and allows a fresh review. Network ambiguity keeps
  `stopping` state and its Stop identity for retry. A completed activation must use
  the ordinary Node Stop path rather than this pre-publication recovery endpoint.
* GET `/activation`: local read only. No command, remote probe or policy refresh.
  The result distinguishes a historical completed receipt from a fresh cached
  running observation. It must never be treated as a fresh end-to-end connection test.

A successful HTTP response containing `starting`, `stopping`, or `service_activated:false`
is not successful service activation. `target_may_be_running:true` is deliberately
conservative: the Node may have received Start even if the Hub saw no reply.

## Ordering and evidence

The Hub stays disabled and its activation hold remains closed while Start is
pending. This excludes the candidate from normal subscription/failover publication;
**it does not act as a packet firewall**. Existing direct clients with a working
address can connect as soon as the remote Start executes. The endpoint plan and
consent explicitly expose this fact. Operators must independently check DNS,
tunnel forwarding, client addresses/ports and old-server retirement.

A persisted activation journal binds the review to installation, origin, encrypted
credential identity, deployment operation, config revision/hash, validated runtime
hash, public endpoint snapshot and source input snapshot. Journal fields contain
no plaintext credential. Every remote result is fenced before publication.

Agent `POST /node/api/v1/control/activate` requires ordered Start identity plus
`desiredRevision`, `desiredHash` and `validatedHash`. It uses the same final
reauthentication/engine lock as all existing mutations. The applied desired-state
identity and freshly validated runtime hash must match **before** ordered Start.
No unversioned Start fallback is used. Agents lacking the capability require update;
normal existing Start/Stop behavior is not silently changed for other Nodes.

After exact Start ACK, the Hub reads cumulative counters and running health,
refreshes policy again, and verifies desired state/receipt/counts. Receipt, ACK,
hold release and Node enable commit in one local transaction. Publication failure
keeps a retryable journal and a disabled Node; an already-completed retry never
re-enables a subsequently disabled Node. A changed policy/endpoint or newer Stop
causes a durable compensating Stop rather than publishing stale success.

## Accounting and pause

Known stopped checkpoints are established before first Start for clients added
since replacement binding. The first report is counted, not discarded as an
unknown baseline. Accumulated customer consumption, quota, expiry, UUID and
ownership are not reset. Available activation traffic is metered through the
existing cumulative accounting path. Retries do not charge the same sample twice.

A confirmed paused activation may have nonzero counters. Only this explicit
paused state lets stopped staging meter a nonzero snapshot; the original strict
zero-counter requirement remains for never-started candidates. Restaging an
unresolved Start/Stop is forbidden. The normal enable/Start/Restart paths stay
held until activation succeeds.

## Recovery boundaries

Journal state is in the encrypted Hub backup. A backup containing the journal and
key can continue a lost Start without a new command identity. A real Agent restart
retains its database and installed configuration; automatic process recovery does
not mean the Hub reissued Start. Changed credentials, installation identity or
configuration block reuse of stale evidence rather than silently adopting it.

This is **not a physical exactly-once process-start guarantee** across the remote
effect/receipt crash window. It does not repair an older backup that predates the
journal, reconcile all old command revisions, verify old-VPS shutdown, recover
unreported traffic from an inaccessible VPS, migrate the general Pair/token paths,
or automate network cutover. There is no cross-machine atomic policy transition:
policy is checked at defined boundaries, and changed policy triggers a Stop before
Hub publication; a transient direct-connection window is not ruled out.

Database-root access, arbitrary root changes on the target and full-database clones
are outside these application fences. A freshly validated compiled config is not
a proof that every external inbound path works.

## Tests

`tests/test_node_replacement_activation.py` exercises owner/CSRF/read-only gates,
strict consent, config-conditioned Agent Start, missing capability, current-input
invalidation, lost replies, duplicate/concurrent requests, publication rollback,
compensating Stop, delayed Start after Stop, first-byte accounting, retained
consumption after pause, encrypted Hub restore, and Agent process recreation.
FastAPI, SQLite and files are real; Xray is the existing fixture and the socket/TLS
transport is replaced by TestClient. Provider-WAN and physical power-loss acceptance
must be run separately. No production VPS is used by these tests.
