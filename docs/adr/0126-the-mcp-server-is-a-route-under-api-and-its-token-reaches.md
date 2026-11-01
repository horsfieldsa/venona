---
status: accepted
date: 2026-08-17
---

# 2026-08-17 — The MCP server is a route under `/api`, and its token reaches nothing else


Two facts, both checkable, and together they decide the shape.

`GUARDED_PREFIX = "/api"` is what makes C12 default-deny, and the reverse proxy config
matches `/api/*` and serves everything else from the SPA root. So a `/mcp`
endpoint outside `/api` would have been simultaneously unauthenticated and, from
a client's point of view, an HTML parse error.

Under `/api` it is authenticated by construction. The second half is that
authentication is not enough: a bearer that satisfies the middleware satisfies it
for `POST /api/data/purge` too, and the extension's own copy says "anything
holding this token is you". Hence `Principal.reach`, and hence a token CLASS
rather than a second value of the same one.

**Route, not mount, and the reason is the test suite rather than taste.**
Measured against the installed starlette: `Mount("/api/mcp")` compiles to
`^/api/mcp/(?P<path>.*)$`, so the bare `POST /api/mcp` — the URL a client is
given — 307s. Worse, a `Mount` contributes nothing to `app.openapi()["paths"]`,
which is what `test_every_api_route_requires_auth` walks, and it is not an
`APIRoute`, which is what `test_route_contracts.py` walks. Both would have gone
blind at the largest new attack surface in the app and kept reporting forty-plus
operations checked. `tests/test_mcp_auth.py` now asserts nothing under `/api` is
mounted at all, so the gap cannot reopen quietly.

**What the reach check caught on the way in.** `principal_for` was
`verify_bearer(...) or verify_session(...)`. That `or` is harmless with one
bearer class and a hole with two: a revoked or mistyped `vmcp_` token presented
from a browser fell through to `venona_session` and authenticated as the
operator, unrestricted. The connection test on `/settings/agents` does exactly
that, so it would have reported "connected" for every credential it was given.
A presented bearer is now the credential.
