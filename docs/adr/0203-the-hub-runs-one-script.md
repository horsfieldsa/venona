---
status: accepted
date: 2026-09-03
---

# 2026-09-03 — The hub runs one script, and the field moves there too (reverses part of 0202)

0202 stopped at the still field and recorded the animated canvas as refused:
the hub's CSP was `default-src 'none'` with no script directive at all, and
trading a categorical no-execution posture on the OIDC issuer for a moving
background looked like the wrong side of the deal. That was put to the owner as
a decision rather than taken, and the decision was to take the animation.

**What actually changed is one directive.** `script-src 'self'`, and nothing
else: no `unsafe-inline`, no `unsafe-eval`, no third-party origin anywhere in
the policy. The hub serves exactly one script, from its own origin, and renders
no user-supplied content into any page. `tests/hub/test_security.py` pins the
directive list and asserts the absence of each name that would have to be added
before an injected script could run, so a later relaxation is a deliberate diff
rather than a quiet one.

The honest statement of the cost: the hub previously could not execute
JavaScript at all, and now it can execute JavaScript it serves itself. That is
a real reduction in a defence that was absolute, on the service whose session
cookie is the SSO master key for every instance.

**One script, two surfaces, one source.** `constellation.ts` is 360 lines of
canvas work that names no colour — it resolves every value off a probe element
so the graph follows the theme. It now names no class either: the live class
and the canvas class are a parameter, because the hub's field is prefixed
`graph-field` and hardcoding `curtain__` would have meant either a second copy
or a stylesheet naming another surface's classes.

`make field` bundles `hub/canvas/constellation.hub.ts` with the
hub app's esbuild and writes `hub/hub/static/constellation.js`,
committed and diffed by `make lint-generated` like every other generated copy
of this artwork. The hub has no bundler and no package.json, which is exactly
why the copy is generated rather than written. `routers/brand.py` serves it as
one more fixed URL — no catch-all mount, so an unknown path still gets the
hub's own 404.

**The eight colour tokens are emitted too.** The script probes for
`--grey-500`, five entity hues and two accents. The hub has no token pipeline,
so `scripts/graph_field.py` resolves each out of `web/src/styles/tokens.css`
(and, for `--accent-bright`, marketing's, which is where it is declared) and
writes them into the hub's inline stylesheet. The ink stays `--border-strong`
rather than marketing's `--grey-500`, as the still field's already did.

**The bug this shook out.** The still field's opacity was a per-theme RULE,
whose selector (`:root:not([data-theme='light']) .graph-field__still`)
outranked the one-class `--live` rule that fades it. Both layers drew at once
and the field looked wrong in a way that reads as a canvas bug. The opacity is
a custom property now, varying per theme with one rule for the element, so
source order decides — which is how the old landing site has always
done it, and why marketing never had this.

## Consequences

- the reverse proxy config carries the same policy byte for byte; the compose
  path serves these headers from the reverse proxy and the host serves them from the app, so
  a relaxation applied to one reaches half the deployments.
  `test_the_caddy_copy_of_the_csp_has_not_drifted` holds them equal.
- `make lint` now needs `hub/web/node_modules` for the hub's bundle, as it
  already does for `vue-tsc`. A missing esbuild fails loudly and names the fix.
- The script tag is only on pages that carry a field, so the operator pages
  request nothing. Scripts off and `prefers-reduced-motion` keep the still
  field, which is a complete composition rather than a placeholder.
- Frame-by-frame motion cannot be sampled from an automated browser tab:
  Chrome parks `requestAnimationFrame` when a tab is hidden, and the marketing
  curtain measures equally frozen under the same conditions. What is verified
  is that the script loads, the canvas is created and sized, the live class is
  set and the still fades to zero.
