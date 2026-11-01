---
status: accepted
date: 2026-08-05
---

# 2026-08-05 — A rejected lookup has to say WHY


`lookup_web` returned `None` for every failure mode — search failed, page 403'd,
no extractable text, model found nothing, quote not in page, initials mismatch.
Two live lookups came back rejected and there was no way to tell a working gate
from a broken chain.

It now takes an optional `trace` list and records a reason per candidate, which
the lookup endpoint emits on the run's event log. That is what turned "Tier 3
rejects everything" into the `DOD` bug above, in one run.

**What the traces show about Tier 3's real hit rate:** the search model reliably
proposes authoritative pages — `dau.edu/glossary`, `acqnotes.com`,
`acquisition.gov` — and **most of them 403 an honest User-Agent**. Of six
candidates across two `RFI` lookups, four were 403. `UAS` succeeded because the
model proposed an FAA page, which serves anyone.

So Tier 3's success depends on whether the model happens to name a fetchable
source. Not something to fix by faking a browser UA — that is exactly the
circumvention this project does not do. Worth knowing before reading anything
into a rejection.
