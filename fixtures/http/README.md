# Recorded HTTP corpus

Feed XML and article HTML captured by `make record`, replayed by
`ReplayHttpClient` so the whole pipeline runs with zero network.

`index.json` maps request URL → `{key, final_url, status, headers}`; each body
lives in `<key>.body`. A URL that is not in the index is a **hard failure**, not
a fall-through to the network — that fall-through is how an "offline" suite
quietly starts hitting the internet and stops being reproducible.

## Empty in the published repository

The pages this corpus held were third-party articles, so they are not
published with the code. The offline test suite does not depend on them. To
run the pipeline offline against real publisher markup, record your own:

```bash
VENONA_HTTP_RECORD=1 uv run venona fetch --limit 8
```

Records only successful responses with a non-empty body. Re-recording overwrites
in place, so the corpus tracks whatever the feeds currently serve. Keep what you
record out of any repository you publish.

## Shapes worth keeping

When recording a corpus for local work, make sure these are covered -- each
exercises a distinct code path:

- a feed whose items carry full `<content:encoded>` (skips the body fetch)
- a feed whose items are stubs (forces the body fetch)
- an article trafilatura extracts cleanly
- an article trafilatura handles badly (short/garbled output → skipped)
- a URL carrying `utm_*` parameters (dedup layer 1)
- two outlets carrying the same wire text (dedup layer 2, clustering)
- an article with no machine-readable date (`date_confidence: unknown`)
- a paywalled excerpt
