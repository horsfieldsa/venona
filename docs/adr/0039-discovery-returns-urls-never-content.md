---
status: accepted
date: 2026-08-05
---

# 2026-08-05 — Discovery returns URLs, never content


The second half of the day-one requirement — "go find what the feeds did not
carry" — finally shipped. It could have been much bigger than it is, and the
reason it is small is that the model is used as a *search tool*, not a source.

WebSearch and WebFetch hand the model a sub-model **summary**, not page text.
Anything it asserted about an article would be a summary of a summary,
ungroundable by construction (invariant 3). So `discovery_schema` permits only
`{url, title, publisher, why, published_hint}` — and `published_hint` is
advisory, never stored. The ordinary fetch path then fetches, extracts,
normalises and dedups each candidate, which means:

- a discovered URL is indistinguishable downstream from an RSS one
- a story that also arrived via RSS hits `documents.url_key` and becomes a
  **sighting**, not a duplicate document
- dates come from the fetched page, not from the model

First live sweep: 8 documents from `defensedaily.com`, `soldiersystems.net`,
`army.mil` and `afcea.org` — four publishers with no feed in `sources.yaml` at
all, one of which (`army.mil`) is in the `removed:` list precisely because its
RSS 403s every non-browser client. The article bodies fetch fine.

Themes rotate off `ctx.rng` (seeded offline), not off the date. Rotating off the
date would rekey the LLM cache every midnight and make every recorded fixture
unreplayable within 24 hours. `user_content` carries no timestamp for the same
reason.
