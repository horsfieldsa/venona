"""What a `lookup_all` sweep actually achieved, and why the rest failed.

    uv run python scripts/lookup_report.py            # the latest sweep
    uv run python scripts/lookup_report.py run:06a7…  # a specific one

A sweep reports `grounded 34, rejected 66` and stops there, which is enough to
know it half-worked and not enough to do anything about it. The sixty-six are
not one problem: a search that returned no pages, a host that refused us, a
page that genuinely never spells the acronym out, and a definition we READ and
threw away on the initials check want four different fixes, and only the last
two are about the model at all.

So this reads the per-surface trace out of `run_events` -- where the sweep
already puts it -- and answers three questions:

  1. **What is the success rate**, and what did it cost per definition landed.
  2. **Where do the failures concentrate**, ranked, with examples to click.
  3. **What did it actually write.** A sweep that grounds ninety of a hundred
     with ten wrong expansions is worse than one that grounds fifty and is
     right, and no counter can tell those apart -- so the definitions are
     printed for a human to read. That is the point of the exercise, not a
     footnote to it.

Read-only. It opens the database `mode=ro` and never writes.
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from venona.db import core as db  # noqa: E402
from venona.db.repositories import diagnostics as diag_repo  # noqa: E402
from venona.pipeline import web_grounding  # noqa: E402
from venona.settings import get_settings  # noqa: E402

# The reason -> funnel-stage table is `web_grounding.REASON_STAGE`, beside the
# code that writes the strings. A copy here drifted from it once already: a
# reason added to the pipeline reads as `unknown` in this report, which is the
# one column nobody investigates.
#
# `no_trace` is this script's own invention -- it stands for a surface whose
# lookup recorded nothing at all -- so it is added rather than assumed.
STAGE_OF = {**web_grounding.REASON_STAGE, "no_trace": "unknown"}

#: An acceptance, printed on its own terms rather than counted as a failure:
#: a definition taken over the initials check is the row to read hardest.
OVERTURNED = "adjudicated"

EXPLAIN = {
    "search_failed": "the search call itself errored",
    "no_candidates": "the model proposed no URLs at all",
    "fetch_error": "the request never completed (DNS, TLS, timeout)",
    "fetch_status": "the server refused us (403/404/5xx)",
    "no_extractable_text": "fetched, but nothing extractable -- usually a PDF",
    "read_failed": "the read call errored",
    "not_defined_in_page": "readable page that never spells the acronym out",
    "incomplete": "the model returned a partial answer",
    "quote_not_in_page": "quote was not in the text we fetched (invention)",
    "expansion_not_in_quote": "the quote does not contain the expansion",
    "acronym_not_in_quote": "the quote never mentions the acronym itself",
    "initials_mismatch": "read a definition; neither the initials nor the adjudicator",
    "off_domain": "the corpus uses this surface to mean something else",
    "no_trace": "no trace recorded",
}


def connect() -> db.Conn:
    # No `row_factory`: the Postgres `Conn` shim has no such attribute and
    # already yields rows that subscript by name. Setting it raised on the
    # first line of every run of this script.
    return db.connect(get_settings().database_url)


def latest_run(conn: db.Conn) -> str | None:
    row = diag_repo.latest_lookup_all_run(conn)
    return row["id"] if row else None


def elapsed(started: str | None, finished: str | None) -> float | None:
    if not started or not finished:
        return None
    try:
        return (
            datetime.fromisoformat(finished.replace("Z", "+00:00"))
            - datetime.fromisoformat(started.replace("Z", "+00:00"))
        ).total_seconds()
    except ValueError:
        return None


def bar(n: int, total: int, width: int = 28) -> str:
    filled = round(width * n / total) if total else 0
    return "█" * filled + "·" * (width - filled)


def main() -> int:
    conn = connect()
    run_id = sys.argv[1] if len(sys.argv) > 1 else latest_run(conn)
    if not run_id:
        print("no lookup_all run has ever been recorded")
        return 1

    run = diag_repo.run_row(conn, run_id)
    if run is None:
        print(f"no run {run_id}")
        return 1

    events = [json.loads(r["data"]) for r in diag_repo.stage_progress_events(conn, run_id)]
    # Two shapes on one event type: the one emitted BEFORE each surface carries
    # `done`/`total`, the one emitted after a rejection carries `reason`.
    starts = [e for e in events if "done" in e]
    rejects = [e for e in events if e.get("reason") and e["reason"] != OVERTURNED]

    calls = diag_repo.run_spend(conn, run_id)

    attempted = starts[-1]["done"] + 1 if starts else 0
    # The sweep's own count, which is authoritative once it has finished; the
    # event stream is what makes a RUNNING sweep readable.
    grounded = starts[-1]["grounded"] if starts else 0
    counts: dict = {}
    stage = diag_repo.lookup_stage_counts(conn, run_id)
    if stage and stage["counts"]:
        counts = json.loads(stage["counts"])
        attempted = counts.get("considered", attempted)
        grounded = counts.get("grounded", grounded)

    seconds = elapsed(run["started_at"], run["finished_at"])

    print("─" * 72)
    print(f"  {run_id}   {run['status']}")
    print("─" * 72)
    rate = f"{100 * grounded / attempted:.0f}%" if attempted else "n/a"
    print(f"  attempted        {attempted}")
    print(f"  grounded         {grounded}   ({rate})")
    print(f"  spend            ${calls['c']:.2f} over {calls['n']} model calls")
    if grounded:
        print(f"  per definition   ${calls['c'] / grounded:.2f}")
    if seconds:
        each = seconds / max(attempted, 1)
        print(f"  wall clock       {seconds / 60:.0f} min  ({each:.0f}s each)")

    # ── where the failures are ───────────────────────────────────────
    if rejects:
        print()
        print(f"  FAILURES ({len(rejects)})")
        print()
        by_stage = Counter(STAGE_OF.get(e["reason"], "unknown") for e in rejects)
        total = len(rejects)
        for stage_name, n in by_stage.most_common():
            print(f"    {n:3}  {100 * n / total:3.0f}%  {bar(n, total)}  {stage_name}")
        print()
        for reason, n in Counter(e["reason"] for e in rejects).most_common():
            print(f"    {n:3}  {reason:24} {EXPLAIN.get(reason, '')}")
            for example in [e for e in rejects if e["reason"] == reason][:3]:
                url = ""
                for entry in reversed(example.get("trace") or []):
                    if entry.get("url"):
                        url = entry["url"][:58]
                        break
                print(f"         {example['surface']:10} {url}")

    # ── definitions the initials check refused ───────────────────────
    overturned = counts.get(OVERTURNED) or []
    if overturned:
        print()
        print(f"  OVERTURNED ({len(overturned)}) -- accepted over the initials check")
        print()
        for row in overturned:
            print(f"    {row.get('surface', ''):10} {(row.get('expansion') or '')[:56]}")
            print(f"               {(row.get('how') or '')[:60]}")

    # ── what it actually wrote ───────────────────────────────────────
    defined = diag_repo.definitions_written_since(conn, run["started_at"])
    if defined:
        print()
        print(f"  DEFINITIONS WRITTEN ({len(defined)}) -- read these; counters cannot check them")
        print()
        for row in defined:
            print(f"    {row['surface']:10} {row['expansion'][:56]}")
            print(
                f"               conf {row['confidence']}  "
                f"{row['corroboration_count']} source(s)  {(row['grounding_url'] or '')[:52]}"
            )

    print("─" * 72)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
