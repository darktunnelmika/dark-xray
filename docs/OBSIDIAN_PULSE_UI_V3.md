# DARK XRAY — Obsidian Pulse UI V3

## Why V3

The owner found Classic Gangster V2 too dark, heavy, square and visually lifeless. V3 deliberately moves to a **luminous graphite / slate / midnight-blue** design with brighter, calmer surfaces, curved navigation, elevated panels and subtle teal/iris accents. The entire presentation changes, not the production data plane.

## What is in scope

- A single additive, scoped stylesheet at `web/obsidian-pulse-v3.css`, loaded last from `web/index.html`.
- Sidebar, header, brand, navigation pills, tabs, buttons, notices, tables, dialogs, status chips, overview cards, Clients/Nodes/Restore surfaces.
- Inbound editor colors, panels and spacing, including slimmer rounded Deployment Targets, Tunnel Ports and SWAP rows.
- Consistent warning/error/success semantics, focus styles, Persian RTL, mobile breakpoints and reduced-motion overrides.

## Deliberately unchanged

- JavaScript structure and handlers; `web/inbounds-v3.js` and all other JS are unchanged.
- Every deployment checkbox, Port/Tunnel enable value, custom SWAP, Direct behavior, Node status/readiness, permissions, accounting, UUID, subscription and generated client config.
- All settings, data migrations and Xray runtime. No server restarts required.
- Dense Clients list geometry, mobile single-column layout and server/node routing architecture.

## Acceptance & QA

- `node --test tests/*.test.cjs`
- `sha256sum -c SHA256SUMS`
- CI Chromium `tests/browser-smoke.py`: actual colors/radii, mobile overflow/focus, Inbound editing and form payload, compact Deployment fields.
- Critical browser operations for Node Pairing / Recovery / Replacement and broader CI status.

## Release and rollback

Install the verified **CSS and index.html only**, backing up index first. Static assets update on Hub without a backend/Xray restart. Remove the single `obsidian-pulse-v3.css` link to revert instantly to the existing V2 skin. Keep all earlier CSS and JS intact. After deploy, inspect the served files with host-scoped HTTP 200 and matching SHA256.

The visual implementation lives entirely within this add-on layer; if the owner dislikes the outcome, revert it without touching any server data or runtime state.
