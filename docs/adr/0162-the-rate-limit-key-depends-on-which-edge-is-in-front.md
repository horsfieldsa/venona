---
status: accepted
date: 2026-08-25
supersedes: 0151
---

# 2026-08-25 — The rate-limit key depends on which edge is in front: CF-Connecting-IP behind Cloudflare, rightmost hop behind the reverse proxy

`middleware._client_key` takes an `edge: bool`. On the edge path it reads
**`CF-Connecting-IP`**, falling back to the rightmost `X-Forwarded-For` hop; off
it, the rightmost hop as before. The hub gains the same helper
(`hub/ratelimit.py::client_key`), keyed off `settings.edge_headers`.

**Why.** [0151](0151-rate-limit-keys-on-the-rightmost-forwarded-hop.md) reasoned
that the closest trusted proxy appends the address it accepted the connection
from, so the rightmost entry is the one hop a client cannot forge. That is true.
What it assumed — and what production no longer satisfies — is that the closest
trusted proxy is *ours*, one hop in front of an unpublished port. The live chain
is Cloudflare → the platform router → uvicorn, and the hop the platform appends is its own
router: **shared infrastructure, the same address for every visitor on the
platform**. The key does not resolve to the visitor; it resolves to one value
for the entire internet.

Every per-IP bucket therefore collapses into a single bucket. `login` is five
attempts, so five failed logins by anybody lock out everybody — a trivially
reachable, self-inflicted denial of service on the one surface that has no other
way in. The `global-login` backstop is 20/hr, a ceiling on the blast radius
rather than a defence: a merged key exhausts the per-IP bucket long before the
global one matters. Verified against production, whose responses carry
`server: cloudflare`.

**Why `CF-Connecting-IP` is trustworthy, and only there.** Cloudflare sets it on
every request it proxies and overwrites any value the client sent, so on a
connection that genuinely arrived through Cloudflare it names the visitor and
cannot be forged. That property belongs to Cloudflare, not to the header: a
caller who can reach the origin directly can write it themselves. So it is read
only when `edge` says Cloudflare is in front, and ignored otherwise — the flag is
the whole safety argument, which is why it is a constructor argument (mirroring
`SecurityHeadersMiddleware(edge=...)`) rather than an environment read inside the
key function.

**What survives from 0151.** The leftmost hop is still never the answer, and
`--forwarded-allow-ips '*'` still makes `scope["client"]` the leftmost value, so
the socket peer is still not the key. Off the edge path — behind a reverse proxy —
0151's rightmost rule is unchanged and still correct, and its test
(`test_a_spoofed_forwarded_prefix_cannot_mint_a_fresh_bucket`) still passes
unmodified. The `global-write` backstop it added is untouched. This record
narrows 0151's scope rather than contradicting its reasoning: it was right about
the deployment it was written for.

**Rejected: trust `CF-Connecting-IP` whenever it is present.** One env var
simpler and wrong in exactly the direction 0151 warned about — any client that
can dial the origin mints a fresh bucket per request by rotating a header we
chose to believe.

**Rejected: count trusted proxies from the left.** A hop count is a number that
has to stay true as the topology changes, with no signal when it stops being
true. A named header set by a named edge, gated on a flag that already exists for
the same reason, fails visibly instead.

**Consequence.** Load-bearing sites: `api/middleware.py::_client_key` (and
`AuthMiddleware`'s `edge` argument, passed from `main.py` `_EDGE`),
`hub/ratelimit.py::client_key` (passed `settings.edge_headers`, set by
`HUB_EDGE_HEADERS=1` in the deployment config). The hub's request-access form keyed on
`request.client.host` — the socket peer, which is the mirror-image of the same
bug — and now uses the shared helper. Pinned by `test_edge_key_prefers_cf_connecting_ip`,
`test_off_edge_ignores_cf_connecting_ip` and
`test_client_key_charges_the_visitor_not_the_platform`. Moving off Cloudflare, or
putting an origin reachable without it, means revisiting this record: the flag
would then be lying.
