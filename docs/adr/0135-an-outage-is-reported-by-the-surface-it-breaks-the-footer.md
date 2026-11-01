---
status: accepted
date: 2026-08-18
---

# 2026-08-18 — An outage is reported by the surface it breaks; the footer strip is gone


The footer's `HealthStrip` named the sick service — `api · neo4j · claude` as
dots — on a different screen from the symptom. Neo4j going down looked like a
blank graph canvas plus a 5px dot two hundred pixels below it; the CLI logging
out looked like nothing at all until a run failed. Meanwhile the API being
unreachable already had the right shape: `BriefView` renders a full
`StateNotice` saying what stopped, what still works and that retry is
automatic.

**That pattern is now the rule.** `GraphView` reports Neo4j down (an overlay on
the canvas — never unmounting it, so the drawn graph survives the outage),
`RunsView` reports the model down, both with an API-down branch first because
every service diagnosis is a guess while the server is not answering. Every
control that would spend through the model — Rewrite, Refresh definitions,
look-for-more, Look up all, Queue research, the hover card's Look this up — is
disabled with the reason. The derivation is one composable, `useServiceHealth`,
over the app-level health poll.

**"Claude down" means `ok === false` OR `circuit.open`, except in replay
mode.** Under `CLAUDE_CODE_OAUTH_TOKEN` the CLI reports a logged-in session for
an expired token, so `ok` only means a token is present; the breaker is what
knows calls are failing. And `VENONA_OFFLINE` reports `ok: false` from a
healthy server, so `data.offline` exempts it — which also fixed the fullscreen
hairline reading permanently red in replay mode. The hairline (the 2px collapsed
footer in graph fullscreen) keeps the red/olive/inert tone and now reads the
same derivation.

**The strip took two signals down with it, accepted deliberately:** "N sources
disabled" now surfaces only on the sources config screen (and `/api/health`,
`venona sources`), and a `claude` binary/pin mismatch only in `make health`.
If either loss bites, the fix is a notice on the surface it breaks — not a new
strip.

Known coupling: `ui_smoke.py`'s Rewrite and look-up-all checks fail on their
own terms if claude is genuinely down on the smoke instance, because the
buttons are now disabled. Smoke already requires a healthy throwaway instance;
if that failure appears, read it as "the instance's claude is down" before
anything else.

*Trigger to revisit:* a view that depends on a service and cannot carry its own
notice, or a second reader regularly missing outages that no open surface
reports.
