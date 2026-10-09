# DARK XRAY Graphite Eclipse V4 — Typography & Neutral Color

## Owner direction

Keep the improved V3 ergonomics and rounded deployment cards but remove the dominant navy cast. The final colors are neutral charcoal + graphite, with mint/cyan reserved for control states and a soft violet for SWAP. The UI should remain dark without looking overly blue or lifeless.

## Bundled typefaces

The stylesheet `web/graphite-eclipse-fontfaces-v4.css` references same-origin variable WOFF2 files in `web/fonts/`, so it does **not** make requests to Google Fonts or a third-party CDN when users open the panel.

| Text | Typeface | Purpose |
| --- | --- | --- |
| Persian/Farsi | Vazirmatn variable | Readable labels, Persian text and menu sections |
| English/Latin | Manrope variable | Modern English headings, labels and controls |
| Technical | JetBrains Mono variable | IPs, ports, UUID snippets, statuses and numbers |

All three families are sourced from Fontsource variable packages and are licensed under the SIL Open Font License 1.1. The repository includes the three WOFF2 assets and pinned origin metadata rather than linking to live third-party font URLs. The panel's strict `Content-Security-Policy: default-src 'self'` allows these same-origin font requests; inline data fonts would be blocked.

Source files and SHA256:
- `@fontsource-variable/vazirmatn@5.2.8/files/vazirmatn-arabic-wght-normal.woff2` — `84a382e46c30fb4f73d0e3800c16d0af15888e2731e57fa5f93e2c29a2c6a957`
- `@fontsource-variable/manrope@5.2.8/files/manrope-latin-wght-normal.woff2` — `a30ddcd349703aff7464c34bef3fffdff405ee50c113440d7c8693c02d210972`
- `@fontsource-variable/jetbrains-mono@5.2.8/files/jetbrains-mono-latin-wght-normal.woff2` — `18be452724bfdc236c074ca94a249a7f41a86752c7d04ab258ce9ed5651f6a7e`

The bundle reserves Arabic/Persian shaping glyphs and zero-width joiners to Vazirmatn, falling back to Manrope for Latin. All are loaded with `font-display:swap`. Existing UI fonts remain fallbacks during font loading.

## Design colors

- Main background `#171a1b` — carbon
- Sidebar `#202526` — dark graphite
- Cards `#272d2e` and elevated `#303839`
- Accent `#7af0c0` — mint
- SWAP accent `#baa8ff` — muted violet

## Safety / rollback

The final two linked CSS assets, three self-hosted WOFF2 files under `web/fonts/` and `index.html` change. No app JS, Hub/Node, client, Xray, database, WARP, Direct/Tunnel/SWAP or accounting code changes. Remove those last two stylesheet links or restore the backed-up HTML to return to V3. This is a static-only deployment and must not restart Xray.

## Acceptance

- All Node.js UI regression tests
- SHA256SUMS for all repository assets
- Chromium browser smoke verifies computed neutral colors, actual Persian/Latin/mono font availability, mobile pages, Inbound editor/Deployment geometry and unchanged form input names
- Verify CSS HTTP 200 from the Hub and all runtime services remain active before/after rollout
