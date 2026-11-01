---
status: accepted
date: 2026-08-05
---

# 2026-08-05 — Numbering schemes are judged by the expansion, not the acronym


`"Division I (DI)"` and `"Division II (DII)"` are numbering schemes, not
acronyms worth defining.

The first attempt rejected Roman-numeral-shaped *acronyms*. That is wrong twice
over: the character class omitted `D`, so `DI` passed anyway, and adding it
would have rejected `CIV` and `MIL` — valid numerals that are also real
abbreviations.

**Now:** `is_numbering_scheme` tests whether the EXPANSION's last word is a
numeral. Only the thing being numbered gives it away.
