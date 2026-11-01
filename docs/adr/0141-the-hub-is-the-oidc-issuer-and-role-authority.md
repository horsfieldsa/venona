---
status: accepted
superseded-in-part-by: 0168 (raising authority only; reduction and the rest stand)
date: 2026-08-18
---

# 2026-08-18 — The hub is the OIDC issuer, and its role claim is authoritative

A new `hub/` service (own repo directory, own small VPS, caddy + FastAPI +
Postgres, no `venona.*` imports) carries the four cross-instance concerns:
instance directory, login portal, central identity, and a narrow management
channel (0142).

**SSO shape:** instances stop talking to Google directly and treat the hub as
their OIDC issuer; the hub itself authenticates people with Google. The
instance-side change is small because of a property `api/oauth.py` already
documents: claims are trusted for their **provenance** — a direct TLS POST of
the code to a constant token endpoint, authenticated with the client secret —
not for a JWT signature. That carve-out holds identically for
`https://hub.example.com/oauth/token`, so instances need no JWKS fetch and no
discovery; the three OIDC constants just become configurable (`VENONA_OIDC_*`,
falling back to `VENONA_GOOGLE_*`). The hub hand-rolls its provider the way
`api/oauth.py` hand-rolled the OAuth 2.1 AS — the surface is two endpoints and
an RS256 encoder, and authlib's framework fights the tiny schema.

**Role authority:** the id_token carries `venona_role` from the hub's
memberships table, and it **overwrites** the instance row's role at each login.
Local suspension always wins. The alternative (local role wins after
provisioning) keeps per-instance autonomy at the price of a directory that
lies; a directory that lies is worse than no directory.

Provisioning moves from `VENONA_AUTH_ALLOWED_EMAILS` to hub claims; the env
list stays as break-glass bootstrap. A hub outage costs new logins only —
instance sessions are the instance's own HMAC cookie, the same posture as a
Google outage today.

**Portal UX:** no email-probe box (an account-existence oracle the codebase
already refuses to be, twice). Sign in with Google, then a chooser of *your*
instances; zero memberships shows the request-access form.
