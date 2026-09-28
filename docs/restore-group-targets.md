# Restore group destinations

DARK RESTORE groups can inspect and edit the subscription destinations of every
member, including members on other pages. This does not create native
representatives or move Restore customers into native Clients.

## Selection contract

- `nodeMode: all` follows Nodes assigned to the selected inbound IDs, including
  later assignments. It does not select unassigned Nodes or deploy inbounds.
- `nodeMode: selected` publishes only the selected assigned Node IDs. An empty
  list explicitly means no Nodes, not inheritance.
- `includeLocal` controls the Hub, and only applies to inbounds deployed locally.
- New imports with no Node subset default to whole-inbound inheritance. The UI
  always sends the mode explicitly and shows the resolved target list.
- Pre-existing saved mappings remain `selected` on upgrade. An operator can
  switch a group to whole-inbound mode after inspecting its preview; the schema
  upgrade does not silently widen any existing group.

Only enabled, online Nodes with deployed error-free assignments are eligible.
Offline and pending destinations are shown in the editor but not published as
working connections. Existing Node assignment health also checks desired-state,
control state, core state and dirty/error flags.

For every selected eligible inbound/runtime, Restore delivers exactly one Direct
config. An enabled, format-eligible explicit Direct Host satisfies that runtime;
otherwise a request-local Direct default uses the runtime data address and the
inbound's actual Direct port. Configured Tunnel Hosts are additional connections,
including each address of a multi-address Host. No implicit Tunnel is invented.
A missing Node Direct is never built by rewriting a Tunnel URI.

A private render view reuses `CoreEngine.links()` and `CoreEngine.subscription()`
for every supported protocol and format. Native client/failover generation and
the persisted Host list are not changed.

## Owner workflow

DARK RESTORE -> select a group -> Edit group destinations -> Preview changes ->
confirm Apply to whole group. The dialog reopens with the saved selection. Mixed
per-user mappings are identified rather than presented as a false uniform state.
A group-wide selection is saved as a default for its import dialog; individual
mapping edits remain available.

The preview returns a revision over the current group membership and mappings.
Saving with a stale revision returns HTTP 409. Mapping updates to Restore records
and core inbound assignments use one transaction. A missing core identity aborts
the whole update instead of recreating credentials. Actual runtime apply failures
are reported as saved-but-pending, not as successful execution.

UUIDs/passwords, subscription tokens/URLs, legacy quota/expiry, recorded usage,
group membership and first-delivery timestamps are not reset by destination
edits. Node-only selection changes do not restart Xray. Inbound changes may
require runtime apply and background Node reconciliation.

## Scope and limitations

Destination selection controls subscription publication, not revocation of
previously delivered configurations. Node deployment, permissions and credential
revocation remain separate operations. Customers must update their existing
subscriptions to obtain the changed destination list. Switching destinations does
not reset post-migration consumption, and no old subscription re-scan is needed.
