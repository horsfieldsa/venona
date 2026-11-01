---
status: accepted
date: 2026-08-27
---

# 2026-08-27 — `localhost` counts as loopback, because refusing it is the whole connection

Dynamic client registration refused `http://localhost:<port>/callback` and told
the client to spell the same machine as `127.0.0.1`, citing RFC 8252 §8.3. The
reasoning was sound and the outcome was that no MCP client could connect:

```
SDK auth failed: Dynamic Client Registration rejected (HTTP 400):
{"error":"invalid_redirect_uri","error_description":"use http://127.0.0.1 rather than http://localhost..."}
```

A native client picks its own callback host before it has ever spoken to us, and
the ones that matter here pick `localhost`. There is no negotiation step in
RFC 7591 where it could learn otherwise — registration either succeeds or the
connection is over, and the error text arrives in a log nobody is watching.

§8.3 puts the literal-address preference on the **client**, not on the
authorization server. Reading it as a server-side rule inverted who it binds.

**What the refusal actually bought.** `localhost` resolves through the host file
and the name service, so an attacker who can answer for it can take delivery of
an authorization code. That attacker already controls name resolution on the
machine the client is running on — the same machine that is about to hold the
token. The code is also PKCE-bound to a verifier that never left the client. So
the defence covered a case where the attacker had already won, and the cost was
every legitimate connection.

`_assert_safe_redirect` now accepts `127.0.0.1`, `::1` and `localhost` over
plain http, https anywhere, and nothing else. Off-loopback http and fragments
are still refused, and those are the two that carry a live credential somewhere
it should not go.

**Registered verbatim, not normalised.** Rewriting `localhost` to `127.0.0.1` on
the way in was the tempting middle path: keep the literal address on the wire,
accept the client's spelling. It breaks two ways. The authorize endpoint matches
`redirect_uri` against the registered list by exact string, so the client's own
request would then mismatch its own registration; and a listener bound by name
may hold `::1` only, in which case redirecting the browser to `127.0.0.1`
connects to nothing. The client's spelling is the client's business.
