# venona — browser extension

Puts venona's underlines on somebody else's article. Read a story on
DefenseScoop and every program, acronym and organisation the corpus knows is
hoverable, with the same definition and the same grounding quote you would get
in a brief.

## Build and load

```
cd extension
npm install
npm run build          # -> dist/
```

Then `chrome://extensions` → enable Developer mode → **Load unpacked** → pick
`extension/dist`.

The extension id is pinned by the `key` in `manifest.json`, so it is the same on
every machine: `hjkknjcomgnlnnkhgcdenhdkejnjiphg`.

## Configure

Options page. Defaults to `http://127.0.0.1:5173/api` with no token, which is
what `make dev` serves — dev runs with `VENONA_AUTH_ENABLED=0`.

**The Vite proxy, not uvicorn**, and the reason is the card's footer. That one
setting is also where "Open in venona →" points, with `/api` stripped off. In
production the reverse proxy serves the SPA and the API from one origin so the two uses
agree; in development they only agree at :5173, because :8000 has no frontend to
send anyone to. `changeOrigin: false` means the API still sees the extension's
own `Origin` through the proxy. Both ports stay in `host_permissions`, so
pointing the URL straight at the API is still a supported thing to do — you just
lose the link.

**For a deployment, type its URL** (ending in `/api`) and save; the browser asks
for permission to reach that origin once. Then paste a SCOPED token:

```
uv run venona mcp token new --label "extension on the laptop" --scope annotate
```

The token is stored in `chrome.storage.local` and never syncs.

**Why scoped, and why this changed.** The instruction here used to be `venona auth
token`, which prints the root `VENONA_AUTH_API_TOKEN` — a credential that can
start a research run, spend the subscription, rewrite the relevance profile and
purge the corpus. That was defensible for one operator on one machine and stopped
being defensible the moment there were readers: a browser profile is not a place
to keep the key to everything, and an extension that only ever needs to annotate a
page should not be able to do anything else.

An `annotate`-scoped token reaches exactly four paths — `/api/annotate`,
`/api/entities/cards:batch`, `/api/sources/sites`, `/api/health/live` — enforced
pre-routing in `AuthMiddleware` against `api/authz.py::ANNOTATE_REACH`, so
anything else is a 403 no matter what the token's owner is allowed to do. It also
belongs to a user and inherits that user's role, so revoking the person revokes the
extension. The root token still works, and is still the break-glass path; it is no
longer the documented one.

**Any host works.** `host_permissions` names the local dev port and nothing
else, and that permission — not the setting — is
what decides whether a request can be made at all. So saving a base URL now asks
the browser for its origin first (`optional_host_permissions` covers every https
host). Before that, a URL you could type was not a URL you could reach: the
field accepted it and the fetch failed with `TypeError: Failed to fetch`, with
nothing connecting the two. A declined prompt still saves the URL — you may be
about to grant it from `chrome://extensions` — but says plainly that requests
will fail until you do.

**The default stays local**, deliberately. `scripts/ext_smoke.py` never sets
`baseUrl` and relies on it, and a development checkout that silently pointed at
production would annotate against the live corpus without saying so.

**It does not need `VENONA_CORS_ORIGINS`.** Measured, not assumed —
`host_permissions` is what grants an extension cross-origin access, and a
`POST /api/annotate` succeeds against an API with no CORS middleware installed
at all. See `docs/adr/`, 2026-08-10. Pointing the extension at a new
host does need that host's permission, which the browser prompts for.

**Where it runs is granted, one host at a time.** The Sites section of the
options page lists what `GET /api/sources/sites` reports — the domains your own
corpus has actually pulled articles from, with their document counts — and
ticking one asks the browser for permission to read that site. The worker then
registers a content script for exactly the hosts you granted
(`chrome.scripting.registerContentScripts`), and deregisters them when you
withdraw one from here or from `chrome://extensions`.

**The reasoning that produced the old five-host list is why this is a grant and
not a download.** `matches` briefly carried `https://*.gov/*` and
`https://*.mil/*`, which is not "defence trade press" — it is every US
government and military host, login.gov and va.gov and irs.gov included. On each
of them the content script would inject at `document_idle`, mutate the DOM, POST
the page's text to `/api/annotate`, and have the service worker cache the
response body against that URL. None of that is acceptable on a page someone is
signed in to, and none of it is what this is for. So the API **offers** a list
and never **applies** one: nothing is granted until you tick it. Hosts still
arrive one at a time, by name — what went away is the manifest edit and the
rebuild, not the consent.

The same reasoning runs on the server. `GET /api/sources/sites` only reports
domains whose documents came from a source in `config/sources.yaml`. Pages added
with the toolbar button are stored under `source_id = 'manual'` and are
deliberately **not** offered, or every intranet host and webmail domain you ever
ingested would show up as a one-click grant.

**Pause vs untick.** Pausing stops annotating a site and keeps its permission,
so resuming does not prompt again. Unticking withdraws the permission.

`permissions` is `storage`, `activeTab`, `alarms`, `scripting` and
`contextMenus` — `scripting` because the registration above is the whole
mechanism, `contextMenus` for the instance switcher on the toolbar button.
`optional_host_permissions` is `https://*/*` plus the hub's localhost dev
port; the wildcard grants nothing on its own — it is what makes a later
per-origin `chrome.permissions.request` legal.

**Several instances.** The options page configures any number of venona
deployments (or adds them from the hub's directory); the toolbar sends to the
active one, switched from the button's context menu. When exactly one
configured instance's site catalog claims the tab's host, annotation routes
there automatically; the badge shows a two-letter code of the instance that
would handle the tab once more than one is configured.

## What it does

**Underlines**, in the same colours a brief uses, from the same tokens. An
undefined acronym is dashed and says so, because invariant 3 does not stop at
the app's edge.

**A hover card** — the app's own `EntityCardBody.vue`, mounted into a shadow
root. Definition, grounding quote, provenance, sources, mini-graph. Hover, or
tab to a mention and press Enter; click pins it, Escape closes it.

**Two things that spend, both behind a click:**

- *Look this up* on an ungrounded term. A C11 light-lane run, so it is not
  refused while a research run is going — which is exactly when you are reading
  and hit a term you do not know. Polled from the content script, not the
  worker: the worker is killed after ~30s idle and this takes up to three
  minutes.
- *The toolbar button* ingests the page you are on. Fetch, store, and analyse
  that one article — research lane, because it writes `documents` and
  `clusters`. The badge shows `…`, then a tick or a cross. It works on any page,
  not just granted ones, which is most of the point: the article worth adding is
  usually from a source `sources.yaml` has never heard of. Those documents are
  stored under `source_id = 'manual'`, which is also why they never appear in
  the Sites list.

**A `!` on the badge means the API refused the token.** A 401 used to be a
console warning and a page with no underlines, which looks exactly like a page
with nothing on it; the card says so too, rather than claiming venona has no
record of the term.

## Checking it

```
make ext-smoke
```

Forty-odd checks in headless Chrome against `fixtures/ext/article.html`. Unlike
`make ui-smoke` this only reads and never spends, so it is safe to run against
anything. It needs the API up.

It drives the REAL grant path rather than faking one: the staged copy declares
the fixture origin in `host_permissions` (an unpacked extension's declared hosts
are granted without a prompt), then the harness writes `sites:enabled` in the
worker exactly as the options page does, and the worker's own `storage.onChanged`
listener registers the script. It asserts that nothing is registered beforehand,
and that pausing deregisters while keeping the permission.

Add a check there whenever a defect gets past you — the failures this catches
all render identically as "it looks fine, there are just no mentions".

## How it works

```
content script            service worker         API
extract + collapse ws  →  POST /api/annotate  →  alias index
splice runs back       ←  runs + refs         ←
                       →  cards:batch         →  entity_service
```

The content script never calls the network and never sees the token. It talks to
the worker over `chrome.runtime.connect` — a long-lived port, because the MV3
worker is killed after ~30s idle and a request in flight would die with it.

### The two things that are subtle

**Whitespace.** The alias index matches literally and every multi-word alias has
one space between its words. A DOM text node carries the HTML source's newlines
and indentation, so `Lockheed\n      Martin` would match `Lockheed` alone. The
text we *send* is whitespace-collapsed; the text we *splice* is the original.
`extract.ts` keeps the map between them. We do not normalise the page, because
rewriting someone's text nodes to suit our matcher is not ours to do.

**Case.** `strict_case=True` on the annotate call. Without it, filling `tier_a`
with the whole corpus makes 525 of 1003 aliases match case-insensitively, and
*"a lattice of horizon effects on the foundry floor"* draws three confident
underlines. Sentence-initial capitalisation is still allowed, in one direction
only — `the Army` may appear as `The Army`, but `Neutron` may not appear as
`neutron`.

Splicing never changes the document's text. That is asserted, not assumed:
`document.body.textContent` is byte-identical with and without the extension
loaded.

## Layout

```
manifest.json          MV3, pinned key, icons; NO content_scripts -- see below
scripts/emit-styles.mjs  generates mentions.css + host-tokens.css from the app's
src/background.ts      the only place fetch is called
src/shared/            client (baseUrl + bearer), port protocol, wire types,
                       sites (the catalog + the registration reconciler)
icons/                 committed PNGs, rasterised by `make icons`
src/content/           extract -> splice -> observe
src/options/           settings
```

`generated/` and `dist/` are build output and git-ignored. The stylesheets are
generated from `web/src/styles/{tokens,prose}.css` rather than copied, so adding
an entity type or restyling a mention propagates on the next build — and no file
of resolved hex literals ever enters the tree, which `scripts/lint_tokens.py`
would rightly refuse.
