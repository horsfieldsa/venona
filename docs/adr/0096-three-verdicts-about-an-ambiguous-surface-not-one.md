---
status: accepted
date: 2026-08-08
---

# 2026-08-08 — Three verdicts about an ambiguous surface, not one


`choose` says *these are different things and this one is right here*, so the
others become `rejected`. That is one of three real situations, and the queue
could express only it.

| Situation | Example | Verdict |
|---|---|---|
| different, all real | `CPE` — Capability Program Executive, customer premise equipment, Common Platform Enumeration | **keep both** |
| one thing, several spellings | `FY` — "Fiscal Year", "Fiscal Years", "In fiscal year" | **same thing** |
| different, one right here | `FCC` | `choose` (already existed) |

**Keeping both already worked** — it is the default, the card lists the
alternatives, and `pick_sense` resolves per brief. What was missing was
recording that a person had decided: `decided_at` was NULL on all 26 ambiguous
surfaces, so a surface reviewed and deliberately kept was indistinguishable from
one nobody had opened, and never left the queue. This is the definitions
counterpart of the merge queue's `keep-apart`, down to the `reopen`.

**"Same thing" is not `choose`.** The duplicates are not wrong, they are the
same claim from other documents — so their SOURCES fold into the survivor,
which is what corroboration means and raises its confidence honestly through
`confidence_for`. What is deliberately not copied is the losers' quote and URL:
three of the four tier-3 checks are about the quote containing the expansion,
and a sentence spelling "Fiscal Years" is not evidence for the string
"Fiscal Year". Over half this corpus's ambiguity is this case.

Two display bugs surfaced while validating, both older than the verdicts and
both newly load-bearing because these verdicts create rejected siblings:
`GET /glossary/{surface}` computed `ambiguous` over every row with an expansion
including rejected ones, so a surface just settled still reported itself
ambiguous; and `glossary_card` offered rejected readings to the reader as
"alternatives". Rejection is what makes a verdict durable — it must not also
make the verdict invisible.
