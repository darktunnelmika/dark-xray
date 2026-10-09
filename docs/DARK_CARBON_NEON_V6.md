# DARK XRAY — DARK CARBON Neon Edition V6

## Goal

The owner found the original V5 green-black style too green/muddy throughout Inbound Edit and the whole panel. Return neutral matte carbon-black with green neon reserved for **selected / active / primary** interactions.

| Role | Value |
| --- | --- |
| Page background | `#080b0a` |
| Panels | `#141918` |
| Raised cards | `#202624` |
| Inputs | `#0d1210` |
| Quiet borders | `#34433b` |
| Primary neon accent | `#19ff86` |

### Scope

- A single `web/dark-carbon-neon-v6.css` visual layer loaded **last** after V5.
- Neutralize green fills in the sidebar, header, dialogs, overview cards, tables, inputs, Clients/Nodes/Restore surfaces, and Inbound tabs and edit forms.
- In Deployment Targets, make Node/Hub cards carbon-neutral, selected indicator/border neon green, tunnel port input nearly black with white digits, OFF muted, ACTIVE green, warning amber, errors red. SWAP stays a separate surface but not purple/green filled.
- The Deployment grid continues using the same two desktop columns and mobile single-column contract. `align-items:start` keeps a short Hub card from stretching to a neighboring Node with an additional SWAP section.
- Retain original V3 rounded geometry, V4 self-hosted Persian/Latin/mono fonts, component event handlers, scroll, focus and existing mobile controls.

### Absolutely unchanged

All JavaScript, Hub, Node, Agent, API, database, client UUID/usage, Xray inbounds, subscriptions, routing, Direct, Tunnel, SWAP, IP limits and quotas.

### QA and rollout

Run focused tests and full `node --test tests/*.test.cjs`; verify full `SHA256SUMS` and `tools/repo-check.py`. Run Chromium browser smoke on all workspaces, multiple narrow widths, and Inbound editor. Confirm neutral background colors via computed styles and unchanged form field names.

Deployment needs only the added CSS plus one `index.html` stylesheet link and theme-color update. Back up `index.html`, verify hashes and HTTP 200, and do not restart Xray.

### Rollback

Remove the final `dark-carbon-neon-v6.css?v=1` link or restore the backed-up `index.html`. Fonts, routes, configurations and runtime remain unchanged.
