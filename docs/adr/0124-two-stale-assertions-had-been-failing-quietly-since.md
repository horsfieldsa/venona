---
status: accepted
date: 2026-08-16
---

# 2026-08-16 — Two stale assertions had been failing quietly since ontology v3


Found while verifying the above, and worth recording because both were green-to-
the-eye failures in the gates that exist to prevent exactly this.

`aa3b049` split `Organization` into `company`/`govt`/`unit`/`research`, which
deleted the `--entity-org` token and every `org:` id. Two things still named
them:

- `extension/scripts/finish.mjs` asserted `--entity-org:#6f9f88` was in the
  bundle, so **`make ext-build` had been failing outright** — and its message
  said "the content bundle is missing things it cannot work without", which
  reads as a broken build rather than a stale assertion. It now names
  `--accent-muted`, a tier-2 token nothing generates: what that line needs to
  prove is only that `var()` got resolved, and an ontology-derived token will
  keep moving. `lint_tokens.py` already guards the entity family.
- `scripts/ext_smoke.py` named `org:lockheed-martin`, `org:u-s-army`,
  `gloss:mosa` and `gloss:cjadc2`.

That second list carries the more interesting lesson. MOSA and CJADC2 have since
been **grounded out of the glossary into `tech:` and `prog:` entities**, which is
the pipeline working exactly as designed — so an assertion naming a `gloss:` id
was an assertion that grounding never happens. It was guaranteed to fail
eventually, and nothing runs this on a schedule, so it did so unobserved. Those
checks are now structural: a paragraph resolves several distinct mentions with
well-formed type-prefixed ids (C4), and the card either carries a grounded
definition with its provenance or says plainly that it has none (invariant 3).

**One of them was worse than a failure.** "the card shows the grounded
definition, not a placeholder" asserted `"Modular Open Systems Approach" in
text` — and once MOSA became an entity, that string was its *name*. The check
went on passing while measuring nothing. A false green is worse than a red.

Two harness robustness fixes came out of the same run: `_contrast_on_white` no
longer raises on `None`, because a page that annotated nothing took the whole
run down inside a colour helper before printing a single result; and the harness
now waits for the MV3 service worker, which starts lazily and can be present
with `chrome.scripting` still undefined — both of which present as "the
extension is broken" rather than "ask again in a moment".
