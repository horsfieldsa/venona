---
status: accepted
date: 2026-08-20
---

# 2026-08-20 — Performance rides the freshness stamp: cache by default, no new invalidation machinery, nothing user-facing waits on a probe

Epic M's decisions, made together because they only cohere together. Baseline
and after numbers live in the epic file (`docs/pm/epics/m-performance.md`).

**Caching posture: every list-shaped GET a view repeats gets a loaded-guard
store, invalidated by the freshness stamps.** The stamp protocol
(`services/freshness.py`, ADR-era design in `useFreshness.ts`) already answers
"did this change outside my browser" for five domains; a sixth (`config`,
stat-derived because config lives in files) extends it to the admin rail. No
SWR library, no service worker, no HTTP-cache/ETag layer — a second
invalidation mechanism would compete with the one that works, and the host's
edge already brotli-compresses responses (measured: `content-encoding: br` on
API JSON and the SPA), so server compression middleware lost too. The stamp
contract is unchanged and load-bearing: a moved stamp causes a refetch, an
unmoved one never suppresses a load — every store keeps load-on-mount, guarded
only by its own prior success.

**First paint waits on the session probe and nothing else.** `setup_required`
moved onto the session payload (`SessionInfo`), so the router's first-boot
gate stopped being the reason `main.ts` awaited `/health` before `mount()` —
health probes Neo4j and shells out to the claude CLI under a 30s ceiling, and
the operator's instruction was explicit: the claude CLI is not touched on any
page-load path, only by runs and the background poll. The executor's probe
cache now serves stale and refreshes out of band (`llm/executor.py::health`)
for the same reason: `/api/health` sits on the poll path, and an in-request
subprocess spawn on TTL expiry was a page paying for a run-side probe.

**The eager/lazy line is the reader's path, pinned by lint.** Entry chunk
carries Brief, Archive, Lookup, Search and the two public pages; every
operator view and the settings pages load on first navigation. Measured:
96.4KB → 54.9KB gzipped entry. `scripts/lint_bundle.py` pins the split
(`EAGER_VIEWS`) because it regresses one innocuous-looking static import at a
time. `manualChunks` stays rejected for the reasons recorded in that script.
The fontsource barrel imports stay — `unicode-range` already keeps non-latin
subsets off the wire, so trimming them saved nothing and broke non-latin
quotes — but the two latin text faces are preloaded from the built HTML
(`vite.config.ts::preloadFonts`), collapsing the HTML→CSS→woff2 discovery
chain.

**Rejected: expanding `<KeepAlive>` beyond Brief and Graph.** With
loaded-guard stores, a remount renders from memory in one frame; keeping
component trees alive would spend memory to save that frame and grow the
include list nobody audits. Revisit only if a view's remount is measurably
slow for reasons a store cannot hold (the graph's fcose layout is the
existing, justified case).

**Consequence:** shared lists live in stores (`stores/configDocs.ts`,
`stores/glossary.ts`, guards in `stores/curation.ts`, `stores/briefs.ts`);
a view that refetches per mount now needs a reason. `apiGet` coalesces
concurrent bare GETs (second consumer gets a `structuredClone` — do not rely
on object identity across stores). The session payload is the boot contract:
anything else the app must know before first paint belongs there, not on
`/health`. `make lint` holds the eager set; `scripts/ui_smoke.py::perf_checks`
holds the request counts (one boot health call, session first, one `/config`
per admin round trip).
