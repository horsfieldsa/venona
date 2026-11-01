---
status: accepted
date: 2026-09-11
---

# 2026-09-11 — The hub becomes an app behind its own front door (extends 0202, 0203)

The hub's `/admin` was six server-rendered pages styled from hex values
hand-copied out of `web/src/styles/tokens.css`. The ask was to make it look like
the instance's `/admin`. The answer is that the operator half should not be
server-rendered HTML at all.

**The surface outgrew its own design note.** `docs/HUB.md` described "a
dashboard and four managed lists" whose "only writes are the CLI's `requests
grant` / `requests dismiss`". By the time this was picked up it was six pages
and nine write endpoints, with billing, Stripe and a launch queue added
alongside. Every feature added to it was also a fresh transcription of the
design system into plain CSS -- a table treatment here, a button treatment
there, none of it reachable by `lint_tokens.py`, which has never scanned a
Python string.

**Only the operator half.** Epic D recorded the constraint at the start: *auth
pages must not depend on a bundle*. ADR 0202 and 0203 weighed this service's
script posture deliberately, on the grounds that its session cookie is the SSO
master key for every instance, and `docs/DESIGN.md` names the hub as one of only
two surfaces whose theme survives JavaScript being switched off. Sign-in, the
chooser, the domain gallery, request-access and every refusal stay where they
are. What moved is the part only a signed-in operator can reach.

## The three directives this spends

ADR 0203 pinned the CSP's directive list precisely so a relaxation is a
deliberate diff rather than a quiet one, and it spent a whole record justifying
one directive. This spends three:

| Directive | Why | Before |
|---|---|---|
| `connect-src 'self'` | the app calling this origin's own JSON | absent, so `default-src 'none'` refused every fetch |
| `style-src 'self'` | the bundle's stylesheet | `'unsafe-inline'` only |
| `font-src 'self'` | the two self-hosted faces | absent |

All three are `'self'`. No third-party origin appears anywhere in the policy, no
`unsafe-inline` for script, no `unsafe-eval`. `connect-src` is the one whose
absence does not read as a security setting: with no such directive it falls to
`default-src 'none'`, so the page renders and then does nothing -- a SPA is not
merely unstyled without it.

The honest statement of the cost: this service previously executed one script it
generated itself, and now executes a bundle it builds itself and can also talk
to itself over JSON. A separate front site's CSP already carries all three, but
marketing is not the issuer.

**Every CSP test builds the app with `edge_headers: True`.** `main.py` installs
the headers only when that setting is on and `make hub-dev` runs off-edge, so a
missing directive is invisible in local verification and appears only on
example.com.

## The three categories under `/admin`

`/admin` was two surfaces behind one prefix. It is three now, and
`tests/hub/test_ops.py::test_the_admin_prefix_partitions_cleanly` holds the
split exact:

- **the pages** -- six paths answering with the app's document, refused to a
  signed-out browser as a 302 to the front door;
- **the data** -- `/admin/data/*`, the same `require_operator` gate, refused as
  a 401 envelope because a redirect reaches `fetch` as an opaque hop to HTML
  that a client cannot tell from success;
- **the machine** -- `/admin/instances*` and `/admin/grants`, bearer-gated,
  refused bare.

The two 401s are told apart by `WWW-Authenticate`, which only the bearer gate
sends. The partition test asserts that header's presence and absence rather than
the status alone, because the status cannot distinguish them.

**Session-authed JSON is a new route category, not a new gate.**
`require_operator` is unchanged and is still the only thing that decides: the
hub session cookie plus `HUB_OPERATOR_EMAILS`, env rather than a database
column, so the surface that edits the hub's database is not reachable by editing
the hub's database. What changed is the response type.

**CSRF.** The session cookie is `SameSite=Lax`, so it is withheld from every
cross-site request that is not a top-level GET navigation -- which covers
`fetch` and a cross-site form post alike, and is what the nine form POSTs this
replaces already relied on. The bodies are `application/json`, not a
CORS-simple content type, so a cross-origin caller must preflight; the hub grants
`Access-Control-Allow-Credentials` on no route at all, so the preflight has
nothing to answer with. Every side effect stays on POST: a side-effecting GET is
reachable by top-level navigation, which is the one thing Lax still allows.

## The app is its own package, on the app's own utilities

`hub/web` is a separate npm package with its own Vite config and its own
`vue-tsc`, like `extension/`. It imports `web/src/styles/{tokens,theme,base}.css`
directly, which is what makes `h-control` and `bg-surface-inset` mean the same
thing here as in the instance -- so a component in it is a copy of the app's
class list rather than an approximation of its design. `extension/src/content/
card.css` reaches across the same boundary for the same reason.

It owns its components rather than importing the app's, and that turns out to be
the safer half of the decision as well as the independent one: Tailwind's
automatic detection walks this package's root, so it needs no `@source` globs --
and `theme.css` records that explicit globs resolved differently in `vite dev`
than in `vite build`, with production shipping classes the dev server never
generated.

**The bundle's assets are served from `/app/`, not `/admin/`.**
`AdminBearerMiddleware` default-denies everything under `/admin` except an exact
set, so a hashed chunk under that prefix would answer 401 and the page would
arrive unstyled with nothing in the console to explain it. Keeping the assets
outside leaves the gate an exact set rather than something widened to let a
stylesheet through.

**Enumerated, never mounted.** `routers/brand.py` reads the built directory at
import and adds one explicit route per file. A `StaticFiles` mount would answer
its own 404 for an unknown path under that prefix, against this service's whole
routing posture -- and it would be invisible to the partition sweep, which reads
`.routes` and finds a mount empty.

## The front door got the same treatment, without a bundle

Leaving it alone would have traded one mismatch for another: the operator
surface set in the project's type on the project's tokens, one click from a
sign-in page set in the platform's font on hand-copied hex. So the values are
RESOLVED out of `web/src/styles/tokens.css` and the four type files copied from
the fontsource packages -- `scripts/hub_public_styles.py`, committed output,
`make lint-generated` diffing it, the same shape `scripts/graph_field.py`
already uses for the field.

**It is a file, not a `<style>` block, and that made the pages smaller.** Every
rendered hub page is `no-store`, so an inlined stylesheet re-ships on every view
forever; measured, sign-in was 29,019 bytes before any of this. As one immutable
file carrying the whole token set and the real faces, the page is 17,685 and the
sheet is fetched once. `style-src 'self'` is what allows it, and is one of the
three directives above.

The rules moved to `hub/hub/styles/public.css`, a real stylesheet rather than a
Python string -- which is the only reason `lint_tokens.py` can see them. It
never could before: the front door had 37 lines of raw `light-dark(#…, #…)` that
no linter in this repo was able to read. There are none now.

What the front door still does NOT take is a bundle. `pages.py` renders it, the
theme is decided on the server from the cookie, and it works with scripts off.

## Consequences

- `hub/hub/pages.py` loses 628 lines: the six operator renderers and the grid,
  pager, picker and count-block helpers, plus the table CSS only they used. The
  server-rendered stylesheet got *smaller*, because the complexity that made it
  hard to keep on the design system -- tables with forms in their cells -- is
  exactly the part that became components.
- The hub image gains a node stage and its first dependency outside `hub/`
  and `notices/`: `web/src/styles/` and `tooling/`. the deployment config's hub build
  filter names both, or a commit that moves a colour rebuilds the instance and
  silently does not rebuild the hub.
- `scripts/lint_tokens.py` gained `hub/web/src` and `hub/hub/styles`, and
  `tests/test_copy_style.py` gained `hub/web/src`. Neither surface has ever been
  under the token linter: a colour in a Python string was unreachable to it.
- `make hub-web` builds it. Until it has run, `/admin` answers 503 saying the
  surface is not built, rather than 404 -- which would look like the gate
  refusing.
- The `_picker` select became a segmented control over two radios. It was the
  control an operator touches most and the only one with no counterpart on the
  instance; the docstring defending it argued select-versus-checkbox, which was
  never the comparison that mattered.
- **The way out is a named link in the header**, and it had to be added back
  after the fact. The server-rendered pages carried `Portal · Sign out` in a
  footer row; that row went with them, and the way back moved onto the lockup --
  same destination, but a mark that happens to be a link is not an affordance
  anybody looks for, so in practice a signed-in operator could only leave admin
  through the URL bar. It says what the destination calls itself ("Your
  domains"), sits where the app's header keeps its cross-surface nav, and is a
  plain `<a>`: that page is outside this bundle, so a client-side navigation
  would resolve to nothing. The division is the app's own -- the rail holds
  destinations WITHIN admin, and leaving is the header's job.
- The identity line reads `all domains`, not `admin`. On an instance `admin`
  means that domain's administration and here it means across every domain, and
  the byte-identical string at two scopes is the one thing that would make two
  surfaces this similar harder to tell apart.
- What the hub still cannot copy is the theme toggle: writing the cookie is
  `stores/theme.ts`'s and the old landing site's, and a third writer is a
  decision of its own. The operator app reads the cookie through its own copy of
  `theme-boot.js`, a seventh holder of that name and the third render-blocking
  one.
