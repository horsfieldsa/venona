---
status: accepted
date: 2026-08-05
---

# 2026-08-05 — The `must_read` cut was unreachable in principle


43 of 45 analyzed clusters scored `background`; corpus mean 10.1 against a
`must_read` cut of 70. The obvious reading — "the model is being harsh" — was
wrong. The arithmetic was:

```
a story scoring 5/5 on product_overlap + technology_signal + customer_program
  -- i.e. exactly on thesis, exactly the right customer, exactly the right
     technology direction --                                    54/100
must_read cut                                                       70
```

Weights spread across seven axes; real articles are about two or three things.
So the ideal item for this reader could not reach the top tier unless it *also*
carried a competitor angle and a named award. The cut was unreachable by
construction, not by strictness.

Two free changes (`POST /api/rescore`, zero model calls):

- **Reweighted toward the core bet.** `product_overlap` .22 → .26 and
  `technology_signal` .14 → .18; `competitive_threat` .20 → .16,
  `partnership_channel` and `talent_people` .08 → .06. Tracking one's own thesis
  should outweigh tracking competitors, and it did not.
- **Cuts lowered to what is reachable:** 70/45/20 → 50/25/10. A perfect singular
  story now scores 62 and clears `must_read` with room.

**This did not fill the brief, and that is the finding.** After rescoring, 2 of
40 real articles are `worth_knowing` and 38 remain `background`. Per-axis means
say why: `product_overlap` averages 0.24/5 with **zero** articles scoring ≥3.

The rubric was reporting the truth — nine RSS feeds of general defense trade
press do not carry on-thesis edge-inference news. Scoring was never the
bottleneck; collection is. Fixing the cuts was still correct, and it matters
once collection improves — but it is not the lever.
