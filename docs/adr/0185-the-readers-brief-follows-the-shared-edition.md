---
status: accepted
date: 2026-09-01
---

# 2026-09-01 — The reader's brief follows the shared edition, not the calendar day

ADR 0163 built the right rule and left a wrong one in front of it. `auto_due`
carried six clauses, two of which asked the same question of different things:
"no document for today" and "a shared brief newer than the reader's own last
one". The second is correct and the first made it unreachable, so for one class
of reader the feature ran half a day late for months and nothing said so.

**The trace, from production.** The operator reads in the late afternoon
Pacific, which is past midnight UTC. His brief for 2026-08-31 was therefore
written at 00:53 UTC, from the reading of the 30th. The daily fired at 13:00,
delivered `brief:2026-08-31` at 13:25, and the warm pass 200ms behind it found
a document already stamped 2026-08-31 and returned `already_current`. He read
the previous day's selection until 00:05 the next morning, when the date rolled
and the whole thing repeated. Eleven hours, every day, on the surface the
product is named for. The second reader on the same instance never saw it,
because they do not visit around midnight.

Two smaller costs travelled with it: the 13:25 no-op still wrote a `runs` row,
and the cap counts runs, so the midnight reader silently lost one of three
daily generations to a run that did nothing; and a reader's document is keyed
to the day, so at 00:01 a perfectly current brief written at 23:50 stopped
being shown at all.

**The unit of currency is now the shared edition.** `_covers_latest_edition` is
one predicate over two `generated_at` stamps, and every caller reads it: the
auto path, the payload's new `outdated` field, and the run's own no-op check.
Comparing timestamps is timezone-free, which a date comparison against a
schedule in local time is not.

Decisions:

- **Reversed: "no document for today" as a brake.** A day is not a unit of
  reading. Replaced by "my document is newer than the newest shared brief",
  which is 0163's own midnight clause promoted from unreachable to the whole
  rule.
- **Reversed: auto fires at most once a DAY even when it failed.** The reason
  for the brake is unchanged and still load-bearing: `count_personal_brief_runs`
  counts runs, so a quietly failing model must not spend a reader's whole
  allowance across three page loads. The period was wrong. It is once per
  edition now, bounded by `since=latest_shared`.
- **Reversed: the reader's document is today's row.** It is their newest row.
  `latest_personal_brief` replaces both `personal_brief_for` and
  `latest_personal_brief_at`; the storage key stays `(user_id, date)` and the
  day's row is still replaced in place, so no migration.
- **Reversed: `hadBriefOnArrival` as the mid-session guard.** 0163's decision
  survives untouched -- a brief that lands mid-session never moves the page --
  but the proxy does not. It was safe only while a mid-session landing implied
  no document on arrival, and landing over an existing document is now the
  ordinary case. `arrivalBriefId` compares the id.
- **The daily cap stays a UTC calendar day, and is a separate counter from the
  failure brake.** They were one query serving two jobs. The cap is a hard
  ceiling on what one reader can cost; re-basing it on the edition would let an
  operator who runs three extra briefs in an afternoon multiply every reader's
  ceiling by three. The 429 copy said "you can write it again after the next
  daily brief" while the button's own tooltip said the allowance resets
  tomorrow; both now say the day.
- **`auto_due` must respect the cap.** It got that for free from `used == 0`.
  The client now re-checks whenever new reading lands, so an `auto_due` that
  outlived the cap would POST into the rate limiter on every check, forever,
  and the refusal is swallowed by design.
- **Which shared briefs pull the readership along is a setting, not a
  choice made here.** `llm.personal_brief_fanout` is `every_brief` (default),
  `scheduled_only` or `off`. A brief written outside the schedule is still new
  reading, and whether fanning out to every recently-seen reader is worth what
  it costs depends on how many readers an instance has. `settings` is editable
  through Admin, so it is adjustable per instance without a deploy.
- **The client's re-check rides the existing `briefs` freshness stamp.** No new
  endpoint, no listener, no SSE: `usePoll` already pauses on a hidden tab and
  fires the moment it returns, and the `briefs` domain moves exactly when a new
  edition lands. `refreshForYou` is the two-branch shape (join a run, or start
  the due one) that first paint and the freshness callback both want.

Survives from 0163 unchanged: the server reports and the client acts, with the
GET at zero model calls and spend behind the POST; staleness does not make a
brief due, because an input change stays deliberate; the client polls the
payload rather than the run, which stays admin-only; `last_seen_at` stamped on
use; `personal_brief_warm_days` at 3. And a `PERSONAL_BRIEF_FORMAT` bump still
does not auto-rewrite the readership, because the run's no-op needs BOTH the
inputs hash and the edition -- the edition alone would have let a format bump
pass unwritten, which is the one thing the warm pass exists to carry.

`stale` and `outdated` are two fields because they are two questions. `stale`
is "you moved what matters since this was written" and offers the Rewrite
button. `outdated` is "reading has landed that this was written before" and is
what auto acts on. `outdated` also brings the Rewrite button back, which is the
reader's only way to a second attempt when the automatic one failed.

**Consequence.** Steady state is unchanged at one paid generation per reader
per day, moved from 00:0x to 13:2x, plus one fewer wasted run row for the
midnight reader. One extra generation per reader on the deploy day, as
everyone's document is behind the current edition once. Under `every_brief`,
each extra shared brief an operator runs costs one generation per recently
active reader, ceilinged by the day cap of three. C3's touch list is unchanged:
`outdated` is additive with a default.
