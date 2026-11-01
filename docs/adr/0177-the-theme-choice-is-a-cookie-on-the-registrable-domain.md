---
status: accepted
date: 2026-08-28
---

# 2026-08-28 — The theme choice is a cookie on `example.com`, read by four surfaces

Light, Dark and System were stored in `localStorage` under `venona.theme`, and
localStorage is per-origin. Under ADR 0150 the properties are four origins —
`example.com` (+ `www`) for marketing, `hub.example.com`, and `<slug>.example.com`
per instance — so one reader held up to four unrelated answers to one question,
and the hub held none at all because it has no JavaScript to hold one with.
the old landing site said so in a comment: "A choice made on the
front site does not carry into the app and cannot."

The choice moves to a cookie scoped to the registrable domain:

```
venona.theme=dark; Path=/; Max-Age=31536000; SameSite=Lax; Domain=example.com; Secure
```

`localStorage` stays as a per-origin mirror. The cookie is the shared channel;
the mirror is the migration read and the fallback when cookies are refused.

## Why a cookie and nothing else

The constraint that decides it is **before first paint, with no network, for a
signed-out reader**. The theme has to be resolved by a render-blocking script
(and, on the two server-rendered surfaces, by the server) or the page flashes
the wrong palette, which is the whole reason `theme-boot.js` exists.

- A preference stored against a hub account needs a round trip and an account.
  Most readers of a separate front site have neither.
- A hidden iframe on a shared origin with `postMessage` is the classic
  cross-origin storage bridge. Storage partitioning has made it unreliable, and
  it cannot answer before first paint anyway.
- Passing the choice as a query parameter on cross-property links does not
  survive direct navigation, a bookmark, or a link from an email.

A cookie on `example.com` is first-party on every one of these hosts, is readable
synchronously by a classic script in `<head>`, and arrives on the request for
the two surfaces that render HTML on the server.

## Three values, because absence could not carry the decision

System was the *absence* of the key. Shared, that is a bug with a reachable
path: choose System on `example.com` (cookie deleted), then open
`defense.example.com`, whose own mirror still reads `dark` from an earlier visit.
The mirror-heal below promotes `dark` and rewrites the shared cookie, and System
is unreachable from any property that was ever set to something else.

So storage holds `light`, `dark` or `system` explicitly, and absence means only
"nothing stored". The `data-theme` attribute is untouched: System is still the
absence of the attribute on `<html>`, because that is a CSS fact — `:root`
declares `color-scheme: light dark` and the two `[data-theme]` rules override
it, with no JavaScript resolving anything.

## Precedence, and what heals what

Read the cookie; if it holds one of the three values, that is the answer.
Otherwise read this origin's mirror; if it holds one of the three, that is the
answer and the cookie is rewritten from it. Otherwise System. Writes set both,
always, including System.

Each store is read under its own `try/catch`. `document.cookie` raises
`SecurityError` in a sandboxed frame and under some cookie-blocking settings,
and it is read first, so a single shared guard would skip the mirror fallback
and drop the reader's choice on the floor.

The heal has no recency tiebreak. With the cookie absent and two origins'
mirrors disagreeing, the first property opened wins and then overwrites the
other. That is reachable only before migration finishes or after a cookie
clear, and every outcome is a theme the reader did actually choose, so it is
accepted rather than paid for with timestamps.

## The attributes, one at a time

**`Domain` is a literal, checked against the host.** Each writer emits
`Domain=example.com` only when the hostname is `example.com` or ends in
`.example.com`, and a host-only cookie otherwise. Deriving the parent from the
last two labels would emit a public suffix as the `Domain` on a hosting
provider's own subdomains, and a public-suffix domain is rejected: the cookie would never persist there, silently, in the one place
somebody is likely to be testing.

**Writes clear the host-only spelling first.** Two cookies of one name at
different scopes are indistinguishable in `document.cookie`, and there is no
recovering from that state afterwards.

**Reads take the last match, not the first.** RFC 6265 §5.4 orders same-name
cookies by longer path, then by *earlier* creation time, so a stale host-only
cookie sorts ahead of the domain cookie that was just written.

**`Secure` only on `https:`**, or the cookie is dropped on local dev over http.

**`SameSite=Lax`, and not for the obvious reason.** Every navigation among
`example.com`, `hub.example.com` and `<slug>.example.com` is same-site, so Lax and
Strict are indistinguishable there. The hop that decides it is Google's
cross-site return to the hub's `/auth/callback`: `hub/hub/google.py` asks for
`response_type=code` with no `response_mode`, which comes back as a GET
redirect, and Lax sends the cookie on it while Strict would not. Moving that
flow to `response_mode=form_post` would make the hub render System on the
callback.

**No `HttpOnly`.** The boot scripts have to read it before any module runs.

## What this does not weaken

Two decisions look like they are being cut against, and a reader will raise
both.

`hub/hub/signout.py` chose server-side fan-out "so every browser and device is
covered — no cookie choreography, no third-party-cookie rules". That is about
propagating *authentication*, and it stands. This cookie carries no authority,
is never read to decide who anybody is, and the sign-out path does not touch it.

`backend/venona/main.py` sets `allow_credentials=False` on CORS so that "the
browser will not attach `venona_session` to a cross-origin request at all",
turning CSRF off by construction. Unchanged: with credentials off this cookie is
not attached to a cross-origin API call either, and nothing on the server makes
a decision from it beyond which of two `color-scheme` values to print.

## The cost, stated

WebKit's ITP deletes all script-writable storage after seven days without
interaction on the site — the localStorage mirror on the same clock as the
script-written cookie, so the mirror is not a backstop for it. What answers ITP
is that every visit rewrites the cookie, refreshing `Max-Age`; an active reader
never loses the choice. A Safari reader who stays away for eight days is back on
System everywhere, and that is the accepted loss.

The hub cannot heal, having no JavaScript. A reader who arrives at
`hub.example.com` from a sign-in link during the migration window sees System once,
until the next load of the app or a separate front site writes the cookie.

## Consequences

Five surfaces read the cookie and none can import the string from the others:
two bundler-free render-blocking classic scripts, two modules in unrelated npm
projects, and two Python templates. `tests/test_theme_boot.py` is what holds
them in step, as it already did for the localStorage key.

The hub (`hub/hub/pages.py`) and the instance's OAuth consent screen
(`backend/venona/api/routers/oauth.py`) read the cookie server-side and print
`data-theme` on `<html>`, plus the two `color-scheme` rules that
`web/src/styles/tokens.css` already carries. Neither gains a script: the hub's
CSP is `default-src 'none'` with no `script-src`, and adding one to an identity
provider that interpolates attacker-influencable text is not a trade worth
making for a preference control. Both work with scripts off, which the two
client-side surfaces do not.

The browser extension leaves the shared-string set entirely. Its theme bridge
reads `document.documentElement.dataset.theme`, which its own MutationObserver
was already treating as the real signal.
