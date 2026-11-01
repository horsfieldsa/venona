---
status: accepted
date: 2026-08-10
---

# 2026-08-10 — The extension did not need CORS, and `host_permissions` is why


C12 shipped a `CORSMiddleware` behind `VENONA_CORS_ORIGINS` with the browser
extension as its motivating caller: "A browser extension is the first client
that is not same-origin." Building that client, the first task was to check the
assumption rather than inherit it. It does not hold.

**Measured**, against the API running with `VENONA_CORS_ORIGINS` unset — so
`CORSMiddleware` was not installed at all, and a cross-origin `GET` returned 200
with zero `access-control-*` headers and `OPTIONS` returned 405:

| from the extension's service worker | result |
| --- | --- |
| `GET http://127.0.0.1:8000/api/health/live` | 200 |
| `POST /api/annotate`, `content-type: application/json` | 200, `refs=["org:u-s-army","gloss:mosa"]` |
| `GET https://example.com` (not in `host_permissions`) | `TypeError: Failed to fetch` |

The POST is the one that settles it. A cross-origin POST carrying a JSON
content-type is not a simple request; under CORS it requires a successful
preflight, and there was nothing to preflight against. Chrome grants an
extension's own contexts cross-origin access to hosts listed in
`host_permissions` without a CORS handshake at all. The third row shows the
grant is real and bounded: a host outside `host_permissions` fails, which is
what makes the permission the security control.

**So `VENONA_CORS_ORIGINS` is belt-and-braces for this caller, not a
requirement**, and the extension is configured by `host_permissions` plus
`optional_host_permissions` for a base URL the user changes later.

**C12 is not wrong and nothing was removed.** It says CORS decides who may READ
a response and is off unless an origin is named; both remain true, and the day a
caller arrives that is a *page* on another origin — which is what CORS is
actually for — it will need exactly what C12 built. The correction is narrower:
the extension was named as the motivating case and is not one.

**What this saved.** The dev-time export (`cors_origins()` reads bare
`os.environ` at import, `make dev` passes no `--env-file`, so `.env` would have
been silently ignored) turns out to be unnecessary, and so does a planned
options-page triage flow for distinguishing "origin not allowed" from "token
refused". One fewer configuration step that only fails in production.

**The pinned manifest key survives on its own merits.** An unpacked extension's
id derives from its install path and changes on every machine and reinstall;
`host_permissions` does not name the id, but a stable id still matters for
`chrome.storage` continuity and for anyone who later does want the CORS entry.
Verified: the loaded extension reports exactly the id derived from the committed
public key.
