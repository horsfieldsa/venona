---
status: accepted
date: 2026-08-29
---

# 2026-08-29 — A model may judge how letters derive from words, and nothing else


`verify_expansion` generates the acronym forms a name can be written as and
requires the surface to be one of them. Every form it generates is built from
first initials. That is one way English makes a short form and not the common
one in this corpus, and the gate has been refusing the rest since it shipped.

**Measured on production, over 533 recorded candidate outcomes.** 88 died on
`initials_mismatch`, and reading them, they are one family:

| surface | expansion the gate threw away | how it is actually built |
|---|---|---|
| `SINKEX` | Sinking Exercise | SINK + EX |
| `MANPADS` | Man-Portable Air-Defense System | MAN + P + A + D + S |
| `DEVCOM` | Combat Capabilities Development Command | DEV + COM |
| `GEOINT` | geospatial intelligence | GEO + INT |
| `SATCOM` | Satellite Communications | SAT + COM |
| `HIMARS` | High Mobility Artillery Rocket System | HI + M + A + R + S |
| `CONSOL` | consolidated cargo replenishment at sea | CONSOL |
| `CXMT` | ChangXin Memory Technologies | C + X from inside one word |

`SINKEX` was refused six times, on six independent pages, four of them
government or academic. `MANPADS` has been bought three times for $0.80 and
could never have succeeded. No list of variant classes fixes this honestly:
each of those is a different construction, the next one will be a ninth, and a
generator that admits all eight admits far more than eight.

**So the deterministic check keeps its job and loses its finality.** It stays
the fast accept — it lands most definitions and costs nothing. When it refuses,
one no-tools call is asked a single question: given these words, do these
letters follow. It is shown the acronym, the expansion, and the sentence; it
holds no tools; it returns a boolean and its working.

## Why this is not the thing invariant 3 exists to prevent

The surface reading is that a model now decides what an acronym means. It does
not, and the distinction is the whole of this record.

A definition still requires everything it required before. The sentence is
still text **we** fetched and extracted (`quote_not_in_page`). It still has to
contain the expansion (`expansion_not_in_quote`) and the acronym itself
(`acronym_not_in_quote`). Those three are what keep a model's memory out of the
corpus, and none of them is adjudicable — `adjudicable` is a frozenset holding
exactly `initials_mismatch`, and the comment above it says why nothing else may
join it.

What the adjudicator is asked is downstream of all of that and independent of
it: a question about English morphology, not about the subject domain and not
about what is true. It cannot propose an expansion; the schema has no field for
one. It cannot reach the web; `tools=()`. A failed call is not an acquittal —
the rejection stands.

This is ADR 0003's shape, one layer further in. There the model was demoted
from source of facts to reader over text we control. Here it reads two strings
we already hold and answers a question about their construction.

The prompt carries **no domain fragment**, deliberately. How letters are taken
from words is a fact about English, not about defence, and a fragment would add
a key to the closed `FRAGMENT_KEYS` set — which every instance's on-disk
`domain.yaml` would then be missing, and a missing key is a hard `DomainError`
at load. A prompt that cannot be deployed without a synchronised config write
is a prompt that will one day take the API down.

## The audit trail is the price of the loosening

An overturn is worthless if nobody can see it afterwards, so `how` — the
model's mapping of each part of the acronym to the word it came from — is
carried everywhere the decision goes:

- the grounding trace records `reason: adjudicated` with `overturned` and `how`
- a **failed** appeal records the rejection with the model's reasoning under
  `adjudged`, so a wrong refusal is as visible as a wrong acceptance
- a single lookup's success outcome carries `adjudicated`, because the trace is
  otherwise kept only on the failure branch and the one row resting on a verdict
  would be the one row with no record of it
- the sweep carries the same, per surface
- the nightly stage emits `acronyms/web_overturned`, never `web_reject_*` — an
  acceptance filed as a rejection reason would report the fix as a new failure
- `scripts/lookup_report.py` prints them in their own section, under a heading
  that says what they are, because a count cannot tell a right answer from a
  wrong one

## Budget and reversal trigger

**False-definition budget: 2 in 100 overturned definitions.** An overturn is a
definition accepted over a structural check, and the check exists because a
wrong expansion becomes an alias and corrupts entity resolution downstream. The
recovery is worth a small error rate and not a large one: the population it
applies to is ~25% of failed candidates, so at 2% the expected cost is well
under one wrong alias per hundred surfaces swept, against the ~30 correct
definitions the same change recovers.

**Reversal trigger.** Read the `OVERTURNED` section of `lookup_report.py` after
each of the first three sweeps. If more than two definitions per hundred
overturns are wrong, or if any wrong one has already been written as an alias
and reached the resolver, revert to `verify_expansion` as final and fall back to
enumerated derivational classes in `acronym_variants` — provable, bounded by a
list a person wrote, and the option this record rejected as dishonest rather
than as unsafe.

`unmerge` and the glossary's `rejected` status make an individual bad row
recoverable. Neither makes a systematically bad rule recoverable, which is what
the trigger is for.
