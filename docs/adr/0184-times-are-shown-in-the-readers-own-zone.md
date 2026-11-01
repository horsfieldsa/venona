---
status: accepted
date: 2026-08-31
---

# 2026-08-31 — Times on the reading surface are shown in the reader's own zone, and say which zone that is

Two comments in the frontend argued against this, in almost the same words.
`BriefView.vue`, on the brief's dateline: "Sliced, not `Date`-parsed: the wire
is UTC and parsing would shift this out of step with the archive column and the
CLI." `ArchiveView.vue`, on the archive's time column: "Slicing beats `Date`
here: the wire is already UTC and parsing would shift every row into local
time, so a brief generated at 22:10Z would disagree with the timestamp the CLI
prints."

Both were right about the consequence and wrong about the trade. Consistency
with the CLI is worth something to the one person who reads logs. Every reader
pays for it, all the time, in a number that is silently in somebody else's
zone. And the consistency was never real anyway: `BriefItem.vue` had gone
through `dayTimeLabel` since it was written, so a single brief already showed a
UTC dateline three lines above a local timestamp, with nothing on screen saying
which was which.

`QuotaWindows.vue` had already made this call and written down the reason:
"Local time, because the reader is deciding whether to wait."

## What moves, and what does not

**Instants move. Editorial labels do not.** `generated_at` and `published_at`
are moments in time and now render in the reader's zone. `briefs.date` is a
`YYYY-MM-DD` with no offset, assigned as the UTC calendar date at generation,
and embedded in `briefs.id` — it is the document's masthead date, not a
timestamp, and shifting it would put the archive out of step with the ids,
search and the CLI all at once for no reader benefit.

**A date and a time shown TOGETHER always come from the same instant.** This is
the decision that cost the most thought. The obvious implementation pairs the
editorial date with a localized time, and it renders a moment that never
existed: the daily fires at 13:00–14:00 UTC, so a reader at UTC+13 sees
`2026-08-06 · 11:10 NZST` — a date and a time a day apart. Not an edge case for
them; every single day. So the brief's identity derives **both halves** from
`generated_at`, and `briefs.date` survives only where it stands alone with no
time beside it to disagree with: the archive's sortable date column, search,
the command palette.

**The brief lost its in-article dateline, and that follows from the same
rule.** It had one, protected by `docs/DESIGN.md` as part of the document
rather than chrome. But the ContextBar states the identity too, and once the
dateline was built from the instant while the bar was built from the editorial
date, the two named different days on one screen for a far-east reader — which
reads as a bug, because it looks exactly like one. Two identities that can
disagree are worse than one, so the bar carries the whole of it, time included:
`brief · 2026-08-22 01:39 GMT+12 · 7 items`. This is the same argument that
took the masthead off Curation and Runs, arriving late at the one page that had
been exempt. The identity's day can therefore sit one day from `briefs.id` and
the archive row, which are the editorial label; the day shown is always the day
the time beside it belongs to. The archive's two columns
are read as a pair of separate facts, which is what its header now says.

**The zone is named once per surface, not once per row.** `22:10 PDT` on every
archive row overflows a 4.5rem column, breaks `tabular-nums` alignment, and
repeats three characters a few hundred times. `timeLabel` therefore returns a
bare time, and `zoneAbbrev` is a separate function, read off a formatted part
so it follows its own date across a DST boundary rather than being appended as
a string.

**And on the archive, "once per surface" turned out to be zero.** Naming the
zone in the time column's header was the plan, and it was measured at 390px
before shipping: a `width` on a table column is a floor rather than a cap, so
`time (GMT+12)` took 130px over cells holding five characters — twice the date
column, squeezing the title to a sliver on a phone. The identity line was the
other candidate and is a fixed track that already truncates two ISO dates. So
the archive's times are simply bare. That column's job, stated in its own
comment, is telling two briefs on one day apart; it is a disambiguator, not a
clock anyone reads an hour off, and the brief it opens names the zone on its
dateline. Bare and local is in any case strictly better than the bare UTC it
replaces.

## Where the zone comes from

Detected from the browser, overridable on `/settings/account`, stored in a new
`user_preferences` table. Three things about that:

- **The override is stored; the default is not.** NULL means "ask the browser",
  which is right for almost everybody, follows a reader who travels, and is the
  only answer available before anyone has opened a settings page. A column
  holding a copy of what the client already knows would go stale in a way
  nothing could detect.
- **Not a column on `relevance_settings`.** The presence of that row IS a
  signal — `SessionInfo.relevance_tailored` reports it, and it decides whether
  the first-visit tailor invitation appears. A reader who only ever set a clock
  would have been recorded as having tailored their relevance and stopped being
  asked.
- **Read on the session, written on its own route.** The zone is needed for the
  first paint of the brief, and `main.ts` calls the session probe as the one
  thing it waits on before mount; a second blocking fetch in front of the page
  would answer a question the session can carry for free. The write is `PATCH
  /api/preferences` and resolves its subject through `learning._actor`, NOT
  `authz.actor_id`: `actor_id` is None under `via="disabled"`, which is how
  `make dev` runs, so the local operator would have been refused their own
  clock by a feature with no reason to refuse anyone. `_session_info` resolves
  the same person through its own `subject` local, which already had the
  matching fallback. The two must agree, and `tests/test_preferences.py` writes
  through one and reads back through the other for exactly that reason.

## What this accepts

**The web now disagrees with the CLI, deliberately.** That is what the zone
suffix buys: `09:14 PDT` cannot be misread as `09:14Z`, which is precisely what
the bare `22:10` it replaces could. (`timeZoneName: 'short'` yields a real
abbreviation only where the locale has one — US zones give `PDT`, while London,
Auckland and Tokyo give `GMT+1`, `GMT+12`, `GMT+9`. Unlovely, but unambiguous,
and the readable `shortGeneric` spells them "United Kingdom Time" and "New
Zealand Time", which do not fit an identity line and drop the DST distinction
besides.) Only the web surfaces moved; the CLI still
prints UTC, and the operator surfaces that are read against logs — the run
detail page, the Runs table — still slice UTC on purpose.

**Three UTC day boundaries stay UTC**, and are not made worse by this: the
personal brief's daily allowance (`briefs.py`), the study streak
(`learning.py`, which deliberately made streaks UTC so they do not punish
timezones), and the learning day axes. Moving any of them is a behaviour change
to a spend gate or a streak, not a rendering change. What did move is the two
sentences that named a time: the 429 said "it refreshes again tomorrow
morning", which at UTC+13 means one o'clock in the afternoon. Once every other
clock on the page is local, a lone UTC promise is the one a reader tests, so
the refusal now names the event it is tied to rather than an hour.

## Also here: the reader can see when the next brief lands

`scheduler.schedule_status()` has put `next_run_at` on `/api/health` for a long
time, `useAppPolling` polls health for every reader, and `useSchedule` already
derived a countdown from it. The only consumer was `RunsView`, which is
operator-only — so a reader could not find out when the next brief arrived, and
the only thing that stated the cadence to them was a sentence on the account
page saying there was a schedule without saying what it was.

The line renders below the document, not above it: the question is "when is the
next one", and a reader asks it having finished this one.

- **Keyed on `next_run_at`, never on `enabled`.** `enabled: false` with a fire
  time is a real deployment — the in-process timer is off and an external cron
  drives the trigger endpoint, which is the documented pattern for a host that
  sleeps. Branching on `enabled` the way the operator's page does would blank
  the line on exactly the deployments that have a cadence to report.
- **The hour comes from `next_run_at`, not `schedule.timezone`.** That field is
  the SERVER's cron zone. A 06:00 cron in Los Angeles is 01:00 in Auckland, and
  naming the hour in somebody else's city is worse than naming no hour.
- **`imminent` gets its own sentence.** `untilFrom` answers that bare word
  under 45 seconds and clamps an overdue timestamp to zero, so interpolating it
  yields "Next brief imminent, at 01:00" for a minute around every firing.
- **The personal view says the follow-on in words.** The reader's brief is not
  on a timer at all: `_auto_due` writes it when they next open the page, after
  the shared one lands. One clock, and the dependency stated rather than
  implied.
