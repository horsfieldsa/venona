---
status: accepted
date: 2026-09-01
---

# 2026-09-01 — A call's timeout ceiling is per TASK; the global stays what it is

`llm.timeout_seconds` was one number for every call venona makes, and the
wizard's largest document does not fit it. Recording the setup wizard live
(epic C) killed three `setup_ontology` attempts at the 300s ceiling — fifteen
minutes, no draft, and the run degraded with the ontology missing, which then
refused anchors behind it. The identical prompt returned in about six minutes
when the ceiling was raised to 900s on the throwaway instance.

So: `llm.task_timeout_seconds`, a map from task name to seconds, resolved in
`ClaudeCliExecutor.call` through `settings.timeout_for`. A task resolves to the
instance's own entry, then to a shipped default, then to `timeout_seconds`. An
explicit `timeout=` argument still outranks all three: a caller that has already
decided is not second-guessed by config.

**Raising the global was the alternative and it loses on what a long timeout
costs elsewhere.** The timeout is the only thing that reclaims a subprocess
holding a lane slot, and there are two lanes (C11) sized in single digits. At
900s globally, an `analyze` call whose CLI has wedged holds one of two research
slots for fifteen minutes, and an on-demand lookup someone is watching a spinner
for waits behind it — the failure `light_concurrency` exists to prevent, bought
back by a setting meant for a document nobody is watching. The wizard's ontology
call is one call in a rare run; `analyze` is ~90 in a first run.

**The shipped defaults sit UNDER the instance's file rather than in it**
(`TASK_TIMEOUT_DEFAULTS`, today `{"setup_ontology": 900}`). A dict field
replaces its default wholesale, so an operator adding an unrelated ceiling to
`settings.yaml` would otherwise drop the shipped one and put the wizard back at
300s — silently, discovered on the next scaffold. Falling through per task means
an override adds rather than replaces, and an operator who wants the ontology
call shorter can still say so explicitly.

Entries are refused below 1 second (zero reads as "no limit" and is the
opposite: `asyncio.wait_for` takes it literally) and above `TASK_TIMEOUT_CEILING`,
an hour — beyond that a call holding a slot is indistinguishable from a wedge,
and nothing else would reclaim it.

**Consequence.** `settings.timeout_for` is the only place a task's ceiling is
decided; a call site that wants a different one passes `timeout=`, which the
protocol has always carried. `llm.timeout_seconds` keeps its meaning as the
default for everything unlisted, and stays out of `FROZEN_FIELDS`. New per-task
ceilings are a `TASK_TIMEOUT_DEFAULTS` entry with the measurement in the commit,
not a raised global. `tests/test_call_timeouts.py` holds the resolution order,
the refusals and the executor wiring.
