---
status: accepted
date: 2026-08-05
---

# 2026-08-05 — `verify_expansion` rejected `DOD`


Found by the first live Tier-3 lookups. `RFI` came back rejected with
`initials_mismatch` on a **verbatim FAR quote** — "requests for information",
straight off `acquisition.gov`. Checking the neighbours:

```
DOD      Department of Defense              False   ← the most common acronym in the corpus
RFI      Request for Information            False
SECDEF   Secretary of Defense               False
DHS      Department of Homeland Security    True
C2       Command and Control                True
```

`acronym_variants` dropped stopwords before taking initials, so `of` and `for`
never contributed. But English is inconsistent: *Department of Defense* is DoD
and *Department of Homeland Security* is DHS. One rule cannot serve both, so
variants are now generated from **both** the significant words and all the
words.

This does not weaken the gate. The acronym must still equal a structural
function of the words, so `verify_expansion("DOD", "Defense of Departments
Everywhere")` fails exactly as before — the negative tests are unchanged and
still pass. `SECDEF` still fails, correctly: it is a syllabic abbreviation, not
an initialism, and that is what `profile/lexicon.yaml` is for.

This was live in **Tier 1** as well as Tier 3, so every article writing
"Department of Defense (DoD)" had its parenthetical silently rejected.
