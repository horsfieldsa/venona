---
status: accepted
date: 2026-09-15
---

# 2026-09-15 — A batch is a default a reader can lift, not a ceiling

"Look up every undefined acronym" takes 100 surfaces a press
(`pipeline.acronym_lookup_all_cap`, ADR 0114). The dialog in front of it now
carries a toggle — *look up every one waiting* — that drops the cap for
that press. `?uncapped=true` on `POST /glossary/lookup-all`, and on its preview.

**The cap protects against an accident, and the modal has already ruled the
accident out.** 0114's measurement stands: a real backlog is 558 surfaces, six
and a half hours, and one of three light slots held for all of it. Nobody should
buy that by mistake. But by the time this toggle is reachable the reader has the
count, the price measured off this machine's own ledger, the duration in hours,
and the head of the queue on screen; and the run is cancellable from the Runs
page with everything grounded so far kept. That is the most informed press in
the application. A person who has read all four and wants the queue emptied
overnight was being told to press the same button six times because the button
could not be told otherwise.

**Blank in `settings.yaml` was never an answer for the reader.** 0114 noted that
uncapped is still available by clearing the setting. That is a file on the
machine, it changes every future press rather than this one, and `/admin` shows
no control for it. A knob only reachable by editing configuration is a knob the
person using the app does not have.

**The preview takes the same parameters as the run, resolved by the same
expression.** `retry`, `limit` and `uncapped` all move both. A preview quoting
one batch in front of a button that takes the queue is the defect the preview
exists to prevent (2026-08-09), and it would have been reintroduced by adding
the parameter to only one of them. The preview had been ignoring `limit`
already; it does not now.

**What still bounds it.** `llm.max_calls_per_run` — widened for the sweep in
proportion to the rows it took on, as it already was for a batch. A run that
exhausts it defers the rest, says how many, and ends `succeeded_partial`; the
deferred surfaces stay undefined and stay eligible. The light lane still admits
one sweep at a time (C11), so an uncapped run holds that slot for its whole
length, which is what the duration in the modal is telling the reader.

**Consequence.** `backend/venona/api/routers/glossary.py` (both endpoints) and
`web/src/components/curation/LookupAllButton.vue`. The toggle renders only when
the cap is actually biting — more waiting than this press would take — or when
it is already on. Extends ADR 0114 rather than reversing it: the default is
still a batch.
