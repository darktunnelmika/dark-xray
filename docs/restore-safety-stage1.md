# DARK RESTORE — Safety stage 1 / ایمنی مهاجرت

This stage closes the gap between subscription eligibility and the actual client
`enable` flag sent to the Hub and connected Nodes. It is not the complete migration
wizard, a financial settlement ledger, or an offline authorization lease.

## Source metadata

New automated scans are complete only when `upload`, `download`, `total`, and
`expire` are all explicitly present, non-negative bounded integers, with no
duplicate/invalid recognized fields. Missing values do not mean zero or unlimited.
A complete explicit zero total/expiry is supported. Failed, partial, unscanned, or
self-referential imports stay isolated but are quarantined: no usable subscription
is delivered and their core identity is disabled before runtime application.

Only response headers are read. Each connection pins a DNS-validated public IP
while preserving HTTPS SNI and CA/hostname verification. Redirects are bounded and
revalidated; HTTPS downgrade is refused. URLs and network exception text are not
logged. A source pointing to the current Hub is refused, not counted as a new
legacy baseline. Existing verified/migrated imports remain protected by the
existing re-import freeze.

Pre-existing verified records become `legacy_saved`, retaining their original
quota/expiry and credentials. Older versions did not persist header field presence;
we cannot retroactively assert it. The UI identifies those records as saved legacy
snapshots, not fresh verified scans. New complete scans use `verified`; explicit
owner review uses `manual` with a timestamp, note and before/after audit event.

## Eligibility and enforcement

Remaining quota is original total minus legacy upload/download minus persisted
DARK usage across the local core and all authenticated Node observations. An
expired, exhausted, suspended, incomplete or missing-identity account cannot
receive a usable subscription. Eligibility also reconciles the Restore core flag
before CoreEngine apply and before the normal Node bundle provider reads clients.
No native managed client, representative credit, subscription token, UUID,
password, route or traffic counter is rewritten by this reconciliation.

Per-engine adapters delegate to the original CoreEngine methods; no additional
polling thread or parallel runtime manager is introduced. Reinitialization updates
the observer without stacking adapters. Unknown external disables are retained;
explicit resume only clears manual suspension and never bypasses expiry, quota or
source review. Changing group destinations still does not revoke old credentials.

The existing runtime apply path may restart a shared Xray process when client
membership changes; it does not provide transparent session continuity. Stable
policy state is a no-op. Node propagation uses the existing desired-state protocol.
The status view compares desired/applied hashes and actual Hub configuration,
including deployed copies on Nodes not currently published in the subscription.
Offline/unverified/pending is never shown as a confirmed new block.

**Bounds:** limits are enforced after observation and reconciliation, not at an
exact byte boundary. An unreachable Node cannot acknowledge new restrictions until
it reconnects. Autonomous Node expiry/lease enforcement is not implemented here.
Lost telemetry cannot be reconstructed. Do not advertise zero overshoot, continuous
native sessions through a runtime apply, or an offline hard cutoff.

## UI and operator actions

Inside DARK RESTORE, group safety distinguishes eligibility, source review,
expiry/exhaustion, subscription received, and observed DARK traffic. A subscription
GET is delivery evidence, not proof that a human customer's connection succeeded.
HEAD diagnostics do not mark migration. Runtime status is separate from those counts.
No new main-menu entry is added.

Owner-only source review requires all values, a note, explicit confirmation and a
revision token. It preserves the DARK ledger. Suspension/resume has an independent
confirmed action; permission, CSRF and optimistic conflict checks remain required.
Neither opening a review nor cancelling it applies a change.

فارسی: برای کاربران موجود نیاز به اسکن دوباره، تغییر DNS یا ساخت لینک جدید نیست.
اطلاعات ناقص جدید فعال نمی‌شوند. قطع سهمیه یا زمان روی تنظیم واقعی اتصال اعمال
می‌شود، نه فقط هدر ساب. نود آفلاین تا همگام‌سازی مجدد تضمین قطع مستقل ندارد.

## Evidence and rollout

- Parser and policy tests cover unknown/zero/unlimited, corrupt inputs, partial
  quarantine, manual review, stale revisions, role/CSRF boundaries, replay,
  reinitialization and native isolation.
- Browser acceptance uses real HTTP/login/SQLite with a missing test core and
  explicitly synthetic source metadata, for English/Persian at 1440/390/320.
- Real Xray acceptance uses disposable Hub + two TLS Agents, a real shadow
  listener, real counters and natural expiry time. Previously delivered Direct and
  Tunnel URIs are reused after a block; native control clients remain authorized.
  Schedulers are driven explicitly; this does not prove an offline lease.
- Before live rollout, rehearse the additive schema and predicted blocks against a
  temporary database copy. Preserve original fields and traffic; compare identity
  hashes ignoring only intentional enable-flag changes. Use the standard updater
  and rollback snapshots. Verify live desired-state convergence and subscriptions.

## Remaining stages of the approved migration plan

1. Group workspace and persisted step-by-step migration wizard; scan preview,
   durable resumable jobs and explicit cutover readiness.
2. Restricted system-service actions for in-panel domain/TLS/old-port activation,
   shared-domain dependency checks and read-only diagnostics.
3. Consistent group defaults with explicit per-user exceptions.
4. Historical group usage ledger, archive rather than hard delete, period reports.
5. Separately designed and tested offline Node policy/lease behavior and low-impact
   runtime membership changes; do not silently enable a fail-closed lease on live Nodes.
