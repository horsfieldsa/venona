---
status: accepted
date: 2026-08-16
---

# 2026-08-16 — The brief was told to write a quiet day, on a false premise


`build_user_content` emitted *"nothing today scored above the 'background' tier
for this reader"* whenever `retrieval.quiet_day` was set — but `quiet_day` fires
on `len(strong) < QUIET_DAY_FLOOR`, which is a different claim. On
`run:06a8160f` three clusters cleared the bar, one of them a `must_read` scoring
60, and the model was told none had. It wrote what it was told and titled the
brief "A thin day".

Both halves of that day were manufactured: the material was thin because analyze
spent 94% of its budget out of window, and the *description* was wrong because
the prompt asserted something the retrieval contradicted.

**Consequence:** two messages, chosen on `strong_count`. The anti-padding
instruction survives both, because that part was always right.
