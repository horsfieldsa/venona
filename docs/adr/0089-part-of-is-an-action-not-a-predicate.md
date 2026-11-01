---
status: accepted
date: 2026-08-07
---

# 2026-08-07 — "part of" is an action, not a predicate


The curation queue proposed `Government of Canada` / `Canada` as `part_of`, and
pressing Relate got a 422:

    PART_OF object must be one of ['Organization', 'Program'], got Country
    -- correct form e.g. PEO C3N --PART_OF--> United States Army

The queue offered a decision the API then refused, which is the worst shape a
control can have. Three separate things were wrong, and each is worth keeping
straight because they fail differently.

**`/options` was the only reader of a conflict that did not resolve merges.**
`resolved_pair`'s docstring already warned about this class in as many words:
"everything that reads a conflict has to ask this rather than trust the columns
-- and everything that did not, disagreed with everything that did."
`list_conflicts` resolves, so the card showed `Canada`, a **Country**. `relate`
resolves, so it type-checked against Country and refused. `relate_options` read
the raw stored ids, typed the `org:canada` tombstone as an **Organization**,
offered PART_OF and never offered BASED_IN. The 422 named a type that had never
appeared on screen. One line, and the regression test the suite was missing.

**The adjudicator's verdict is an ACTION, and `part_of` was being read as a
predicate.** The model is asked "same thing, one part of the other, or
different" — and "one is part of the other" is the plain-English shape of
BASED_IN, WORKS_FOR, MANAGES and PRODUCES as much as of PART_OF.
`config/ontology.yaml` is explicit that a governing body is an Organization
BASED_IN its country and never the country itself; nothing in the pipeline could
say so, because there was nowhere to put it. `_relation_for` now translates the
verdict through `check_relation` — PART_OF where PART_OF is legal, the single
legal alternative where there is one, and NULL where several fit — and
`alias_conflicts.suggestion_predicate` carries the answer.

Deliberately done in Python AFTER the model answers, not by teaching
`ADJUDICATE_SYSTEM` about types. The C2 cache key hashes the system prompt, so
editing it invalidates every `fixtures/llm/analyze/*` and makes `make test`
require a `make record` — real subscription spend to fix a typing bug that
`check_relation` can decide for free. `pipeline/anchors.py::file_proposal`
already had this gate; this brings the other producer in line.

**The UI guessed, and it guessed the one predicate that could not apply.**
`predicate` was `ref('PART_OF')` with a `list[0] ?? 'PART_OF'` fallback, the
chips only rendered for MORE than one option, and `RelateRequest.predicate`
defaulted to `"PART_OF"` server-side as well. So a pair with exactly one legal
predicate showed no chips and submitted PART_OF; a pair with none showed no
chips and submitted PART_OF; and a failed `/options` request — swallowed by the
store into an empty list — was indistinguishable from "no relationship connects
these types". Now: `null` is a representable state, the chips render for one
option as well as several, the server default is gone, and a pair with no legal
predicate says so and points at `x` to swap the direction.

The `r` binding's label was the hardcoded string "Relate (PART_OF)" in the `?`
overlay, whatever pressing it would actually write. It is derived now.
