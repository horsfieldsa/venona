---
status: accepted
date: 2026-08-23
---

# 2026-08-23 — One generator writes every surface's icons, and the hub serves its own

The app's tab icon was a single `<link rel="icon">` to an SVG. That covers
Chrome and Firefox on a desktop and nothing else: Safari and iOS read
`favicon.ico` and `apple-touch-icon.png`, Android's launcher reads the web
manifest's PNGs, and every one of those URLs fell through the SPA catch-all
and answered `200 text/html`. A separate front site had the same single SVG,
hashed into `/assets/` by Vite, so its `/favicon.ico` was a 404. The hub's
pages — where `defense.example.com` lands anyone whose session has lapsed —
linked nothing at all, and `/favicon.ico` there was a JSON 404. Three surfaces,
three different ways to show a blank tab, none of them an error anywhere.

**Decision.** `scripts/brand_icons.py` (`make icons`) rasterises the one mark,
`web/src/assets/icons/venona-mark-aqua.svg`, into an identical set for each
surface — `web/public/`, `hub/hub/static/` — and the
extension's toolbar PNGs. Every document that paints a tab carries the same
four links (`.ico` with `sizes`, SVG, touch icon, manifest); the hub gains
one explicit route per file (`hub/hub/routers/brand.py`) because it has no
static tier and a catch-all mount would replace its JSON 404s. The manifest's
colour is read from the mark's own plate rect, never restated.

**Committed, not built.** The Makefile's reasoning for the extension PNGs
holds for the whole set: these change when the logo does, and building them per
deploy would make three builds depend on librsvg. `tests/test_brand_lockup.py`
holds the three sets byte-identical, checks each raster's pixel size and the
`.ico`'s entries, and checks that every head links the whole set — a missing
file renders as "the icon is gone", so the guard has to be offline.

**Rejected.** Serving PNGs at `/favicon.ico` (a PNG body under an `.ico`
content type, behind `nosniff`, is exactly the thing that works in one browser
and not the next); a `StaticFiles` mount at the hub's root (it answers every
unmatched path with its own 404); the extension's options page getting a link
of its own (Chrome paints extension pages with the manifest icon already).

**Consequence.** `make ext-icons` is `make icons`. The hub's CSP and
the reverse proxy config carry `manifest-src 'self'` (the parity test holds
them equal), as does marketing's in the deployment config. A new surface that renders
HTML adds its directory to `SURFACES` in the generator and its document to
`HEADS` in the test.
