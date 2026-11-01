---
status: accepted
date: 2026-08-05
---

# 2026-08-05 — A quiet day is a real outcome, and the brief must say so


The retrieval rule was "top 12 clusters above the `background` tier." Against a
real day, exactly zero cleared it — an honest read, since nothing that day
touched edge inference. But that would have produced an empty brief.

Emitting nothing is useless, and padding with background items dressed up as
significant is worse: a brief that manufactures importance to fill space
destroys trust in the whole product faster than any bug.

**Now:** when fewer than 4 clusters clear `background`, retrieval tops up with
the best of the rest (above a noise floor) and sets a `quiet_day` flag that
tells the model plainly: *nothing scored above background, say so, keep it
short, do NOT inflate.* The first real brief opened with "A quiet day is worth
recording as such. Nothing in the day's traffic either supports or undermines
the on-platform inference bet." That is the product working.
