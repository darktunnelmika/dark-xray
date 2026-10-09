# DARK XRAY — Original Cyber Colors V5

Scope: restore the **original Cyber Classic color identity** while retaining the improvements from later releases.

- Canvas `#000604`, neon green `#19ff86`, muted green borders, pale green readable text.
- Covers sidebar, header, dashboard, controls, tables, dialogs, Inbound editor and Deployment/Tunnel/SWAP surfaces.
- Preserves Vazirmatn, Manrope and JetBrains Mono self-hosted fonts from V4.
- Preserves round-corner geometry, compact Deployment cards, mobile layouts, form input names, Node/client records, Direct/Tunnel/SWAP and all backend behavior.
- Semantic red warnings/errors and amber warnings keep their distinct meanings.
- No changes to JavaScript, database, auth, Xray, Hub/Node agent, subscriptions, or traffic accounting.

Implementation: one new last-in-cascade `web/original-cyber-colors-v5.css` and the linked stylesheet plus theme-color metadata in `web/index.html`. There are no changes to service files or server control planes.

QA: focused visual/layout tests, complete Node.js UI suite, repository SHA256SUMS manifest, and Chromium browser smoke (actual colors, mobile menu, Inbound editing and deployment controls).

Deployment: back up `web/index.html`, copy checksum-verified HTML and CSS, verify HTTP 200 and Hub services. No Xray restart is required.

Rollback: restore backed-up `index.html` or remove its final `original-cyber-colors-v5.css?v=1` link to return to Graphite Eclipse V4 immediately.
