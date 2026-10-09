# DARK XRAY — Classic Gangster V2

A darker, more distinctive high-contrast **obsidian / emerald / steel / antique gold** visual language. V1 was too pale and the Inbound editor remained difficult to navigate.

## What changed

- Stronger, readable sidebar active and hover states, bolder headers, visibly different primary/secondary/danger actions
- Consistent rounded cards, controls, menus and modals without excessive neon overload
- Inbound form shows **Basics / Network / Security** prominently; **Sniffing / Fallbacks / Advanced JSON** remain available in the Advanced Tools disclosure
- More readable labels and helper text, inset panels for Reality target scans and per-node Direct/Tunnel settings
- Responsive group tabs for phone screens, keyboard focus rings, RTL support and reduced-motion styles

## Preserved behavior

- The old six inbound tabs and their original `data-v3-action`, `data-tab`, `data-v3-pane`, `name`, `id` and submit handler remain intact
- Existing per-node tunnel toggles, manual ports, REALITY key generation, search and scan, and JSON roundtrip are unaffected
- No backend or SQLite schema changes; no impact on subscriptions, client usage, actual Xray, Node pairing, Restore or WARP
- Mobile rows, pagination and persisted current page retain their existing owners and CSS geometry

## Scope / revert

Files: `web/classic-gangster-v2.css`, `web/inbounds-v3.js`, `web/index.html`, UI regressions and a browser smoke check. The CSS layer is loaded **after** Premium V1. To roll back only color/surfaces, remove its stylesheet link from `index.html`; to restore the original Inbound tab structure, also revert the change to `inbounds-v3.js`.

Apply via verified commit + checksums, backup each HTML/JS file. Static assets do not require an Xray restart.

## Next enhancement

Potential follow-up phases: sidebar navigation re-grouping, settings category simplification, Clients/Restore dense-table readability, Representative/mobile polish and a limited transition system. These should be independently scoped and QA'd.
