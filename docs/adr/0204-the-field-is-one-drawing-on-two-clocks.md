---
status: accepted
date: 2026-09-04
---

# 2026-09-04 — The field is one drawing on two clocks, and the canvas is cached by name (amends 0203)

0203 put the living graph on the hub and listed what it verified: "the script
loads, the canvas is created and sized, the live class is set and the still
fades to zero." Every one of those was true in production and the field still
looked wrong. Two defects, reported together as "a flash of a graph that goes
away, and sometimes the whole animation disappears".

**Nothing checked that anything was ON the canvas.** `initConstellation` added
the live class — which starts the still SVG fading — and only then asked for
the first `requestAnimationFrame`. Chrome parks rAF for a tab nobody is
looking at, so a hub page opened in a background tab reaches its resting state
as a faded still over a canvas that never got a callback. Measured on
hub.example.com: `graph-field--live` set, canvas created and sized at
2523x1388, **zero painted pixels**. The first frame is drawn synchronously now,
before the class; the same probe reads 51 painted samples. This is the whole of
the "disappears" report, and it applies to both surfaces — the fix is in
`constellation.ts`, not in either surface's copy.

**The crossfade was a second opinion.** `scripts/graph_field.py` resolves the
hub's colours out of the token files and had its motion typed in: `600ms ease`
against marketing's `--motion-reveal` / `--ease`, which are 240ms and
`cubic-bezier(0.2, 0, 0.2, 1)`. The handover moment is the same on both
properties — the still stands for ~85ms on each, measured from the resource
timings on both live sites — so the entire visible difference was 2.5x on the
dissolve, which is long enough to watch the bold seven-hub still turn into the
finer canvas mesh. The cadence, the opacities, the reveal and the easing now
all come from `hub/canvas/tokens.css` through `scalar()`, so there is
no value left in the generator for the two surfaces to disagree on. The hub
also kills the still's transition under `prefers-reduced-motion`, which
marketing's blanket rule in `tokens.css` had always done for it.

**The canvas is served at a URL that names its own bytes.** `SCRIPT_URL` is
emitted into `hub/field.py` beside the drawing — a twelve-character digest of
the bundle — and both the `<script>` tag and the route in `routers/brand.py`
read it from there. That is what makes `public, max-age=31536000, immutable`
safe: the URL cannot outlive its content, so a rebuild moves the tag rather
than leaving a stale file in somebody's cache. It was the largest thing a hub
page fetched and it was refetched on every load of every page; on a second
visit it is now `transferSize: 0`.

`no-store` stays the default and stays right — every hub page is rendered for
whoever asked, in their palette, and the chooser must not survive a sign-out —
but it is a **default** now rather than a stamp. `SecurityHeadersMiddleware`
appends, so a header written over one a route already set ships beside it and
`no-store` wins; the caching pair is skipped as a group when the response has
answered for itself. `vary: Cookie` goes with it: on a URL naming its own
content it asks a shared cache to keep a copy per reader of a file that is the
same for everyone, which is most of what the year was bought for. The security
headers are unconditional and a route cannot opt out of them.

## Consequences

- The hub's motion cannot drift from the old landing page's without
  `hub/canvas/tokens.css` changing, and `make lint-generated` fails
  on a stale copy the way it already did for the colours.
- `hub/field.py` is imported by `routers/brand.py` as well as `pages.py`. The
  generated module now carries a URL, not only artwork.
- Caching is the one header a hub route may answer for itself, keyed on
  `cache-control`. `tests/hub/test_brand.py` holds all of it: the digest
  matches the bytes, exactly one `cache-control` ships, no `vary`, the whole
  security posture is still stamped, and a rendered page is still `no-store`.
- The unhashed `/constellation.js` is gone rather than kept as an alias. There
  is no catch-all mount, so it is a 404 like any other unknown path.
- What 0203 verified is still verified, plus the thing it did not: that the
  canvas has pixels on it before the still is asked to leave.
