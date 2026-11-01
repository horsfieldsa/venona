---
status: accepted
date: 2026-08-26
---

# 2026-08-26 — The reader's brief is written on arrival, and the warm pass gets a population

ADR 0158 said the personal brief is "lazy on first ask, pre-warmed after the
daily for readers seen within `personal_brief_warm_days`". Both halves were
built. Neither worked.

**The warm pass had never had a population.** `warm_recent_readers` asks
`users_repo.recently_seen`, which filters `last_seen_at >= since`, and
`last_seen_at` was written in exactly one place: `record_oidc_identity`, at the
OIDC callback. With auth off there is no callback, and `upsert_by_email` does
not write the column, so the value stayed NULL and `NULL >= since` is false --
an empty population, forever. With the hub in front it was worse than empty, it
was backwards: a session cookie lasts weeks, so a reader who signs in once and
reads every morning keeps a `last_seen_at` from the day they signed in and ages
out of the window while being one of the most active readers. The setting read
"seen in the last 7 days" and meant "signed in afresh in the last 7 days".
Neither query nor pass had a single test, which is why.

**So every brief came from the lazy path, and the lazy path needed a button.**
The reader landed on `/`, the toggle sat on `shared`, and nothing wrote their
brief until they found "Write my brief" and watched a spinner for two minutes.
The poll lived inside the click handler and discarded the `run_id` it was
given, so a reload showed an idle page with an enabled button that silently
coalesced onto the run already in flight. The app's headline personalization
feature was one most readers never saw.

Now: **the server reports that a generation is due and the client acts on it,
on the reading surface.** `GET /briefs/for-you` gains `generating` and
`auto_due` and still makes zero model calls -- it reports, and the POST that
already existed spends. `auto_due` is six clauses, each a brake: tailored, not
already generating, no document for today, no `personal_brief` run today at
all, something in the window, and a shared brief newer than the reader's own
last one.

Decisions that survived review, and why:

- **Auto fires at most once a day even when it fails.** The clause is
  `count_personal_briefs_today == 0`, and that query counts `runs`, not
  documents. Without it a quietly failing model would spend the reader's whole
  daily allowance across three page loads and leave them nothing to retry with.
  The two remaining rewrites stay in the reader's hands.
- **"Newer than their last brief", not "no brief for today".** A new UTC date
  is not new reading. At 00:05 the window holds exactly what yesterday's brief
  was written from, and the date test alone would buy a near-duplicate every
  midnight. Comparing the two documents' `generated_at` is timezone-free, which
  a comparison against a schedule in local time is not, and a reader who has
  never had one still fires.
- **Staleness does not make a brief due.** A reader who changes their emphasis
  gets the Rewrite button. An input change stays deliberate.
- **The client polls the payload, not the run.** `GET /api/runs/{run_id}` is
  not in `READER_ROUTES`, so `waitForRun` and the SSE stream both answer a
  reader 403. Opening that route was rejected: it would expose run params and
  the model transcript, including other people's runs, to every reader.
- **A brief that lands mid-session never moves the page.** The auto-switch now
  requires the document to have been present at first load. It keyed on the
  document id changing while the reader had not touched the toggle, and reading
  is not touching, so it could swap the document mid-paragraph.
- **`last_seen_at` is stamped on use, once a day**, by a guarded UPDATE from
  `_session_info` -- the pre-mount probe, the one place that sees a reader
  arrive whatever page they opened. `touch_daily` copies `token_service.touch`:
  a bare UPDATE outside `db.transaction` (a blind write with no read to be
  consistent with), an in-process date guard so the common case costs no round
  trip, and swallowed database errors. The swallow matters more here than for
  tokens, because `main.ts` will not mount the app without this probe and an
  access-log write must never take the SPA down.
- **`personal_brief_warm_days` drops to 3.** With the stamp fixed it means days
  of use. Warm covers regulars so their brief is waiting; arrival covers
  everyone else. Widening it buys a shorter wait for the occasional visitor and
  pays for every reader who never returns.
- **`personal_brief` and `questions` join `usage.STAGE_FOR_TASK`.** Both
  ledgered `stage=NULL`, so a `GROUP BY stage` over `llm_calls` attributed the
  reader-facing spend to nothing. "Is the personal brief worth what it costs"
  was unanswerable from the ledger that exists to answer it.

Rejected: refund accounting for the one wasted generation when a reader edits
their emphasis mid-flight. The tailor screens warn while a run is in flight and
the landing toast says so when a document arrives already stale, which prevents
the surprise; inventing a refund for a case this narrow buys less than it costs
in places to be wrong.

**Consequence.** Spend is now genuinely O(readers who read) rather than zero
warm plus one button press. A `PERSONAL_BRIEF_FORMAT` bump no longer
auto-rewrites the readership: a document still exists for today, so `auto_due`
stays false and every reader chooses. 0160 assumed the warm pass would carry
recent readers through a format change and it never could. C3's touch list is
unchanged -- the two new `ForYou` fields are additive with defaults.

**Amended by 0185.** The unit of currency is the shared edition, not the
calendar day. "No document for today" made the `generated_at` comparison beside
it unreachable for a reader who visits around UTC midnight, and their brief ran
eleven hours behind the reading it derives from.
