---
status: accepted
date: 2026-08-16
---

# 2026-08-16 — The extension's allowlist became a grant, not a list


`manifest.json` carried `content_scripts.matches` as five literal hosts. Nothing
generated it and nothing checked it against the corpus it mirrored, so it
drifted, and the gap was measurable: **five hosts named, fourteen domains in
`documents`**. `warontherocks.com` (40 documents), `gao.gov` (25) and
`darpa.mil` (10) were all configured feeds the extension had never once
annotated, and every domain the eight discovery sweeps found was invisible by
construction — a `kind: discovery` source has no `url` for anyone to copy
across. Adding one meant editing JSON, rebuilding, and reloading.

`GET /api/sources/sites` now reports the union of the enabled `rss` hosts and
`documents.site_domain`, and the options page asks the browser for permission
per host. The worker registers one content script over the granted set
(`chrome.scripting.registerContentScripts`) and rebuilds it whenever intent,
grants or pauses move.

**What was deliberately NOT traded away.** The five-host list was not laziness —
`matches` briefly carried `https://*.gov/*` and `https://*.mil/*`, which is
login.gov and va.gov and irs.gov, and the conclusion recorded in the extension's
README was "add hosts one at a time, by name". That property is preserved
exactly: the API **offers** a list and never **applies** one. Every host still
needs a permission the user approves and can revoke. What went away is the
manifest edit, not the consent.

**The filter that is a security control, not tidiness.** The toolbar button
POSTs *any* http(s) page to `/ingest` with no allowlist — deliberately, since
the article worth adding is usually from a source `sources.yaml` has never heard
of — and those documents are stored under `source_id = 'manual'`. Reporting
their hosts would put every intranet, webmail and paywalled-reader domain
anyone ever ingested in front of a one-click grant to inject into and POST the
text of every page on it. The hostname validator cannot catch that:
`webmail.internal.example` is perfectly well formed. Only the `source_id` filter
can, and `tests/test_sites_endpoint.py` pins it.

Two smaller things, both because the endpoint's output feeds
`chrome.permissions.request` and a bad row is a grant: the match pattern is
built and validated server-side through `sources/urlguard.py::check_static`
(the guard the fetcher already uses, rather than a second idea of a safe host),
and the feed host is derived with `normalize.py::canonical_url` — the same
function that produced `site_domain` — so the two halves of the union cannot
disagree about `www.`.

**Registration derives from stored intent, not from `permissions.getAll()`.**
`getAll().origins` also returns the manifest's own `host_permissions`, so a set
built from it would register the content script against `127.0.0.1:5173` and
annotate the venona app itself. Intent is stored in `storage.sync` and
intersected with `permissions.contains`, which is also what makes a revocation
from `chrome://extensions` take effect without any bookkeeping.

**The worker is driven by `storage.onChanged`, and that is not a test seam.**
`chrome.runtime.sendMessage` from one extension context is not delivered to the
sender's own listener, and the worker is dead most of the time. A storage write
wakes it, carries the intent, and keeps a second open options tab in step. It is
also the only handle `scripts/ext_smoke.py` has: the worker is an ES module, so
its top-level bindings are not on `globalThis` and CDP cannot call `reconcile`
directly.

**Cost, stated plainly.** `content_scripts.matches` grants *injection*, not
*host permission*, so `permissions.contains` was already false for those five
hosts. Removing the block means they must be granted once from the options page;
nothing annotates until that happens, and the page labels them so it is obvious
which they are.
