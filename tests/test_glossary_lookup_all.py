"""Look up all -- the bulk tier-3 sweep over the undefined acronym queue.

The sweep itself is `lookup_web` in a loop, which
`test_acronyms_grounding.py` already covers surface by surface. What is new,
and what these cover, is the part that decides HOW MUCH IS SPENT:

  * which rows are eligible, since every one of them is a search call plus page
    fetches plus a read call;
  * that the preview a person confirms against is counting the same rows the
    run will attempt, because a modal that quotes a price nobody can hold it to
    is worse than no modal at all.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from helpers import NOW
from venona.api.routers import glossary
from venona.db import core as db
from venona.pipeline import orchestrator
from venona.pipeline.acronyms import undefined_candidates
from venona.services import run_types
from venona.settings import get_settings


def _entry(
    conn: db.Conn,
    surface: str,
    *,
    status: str = "undefined",
    expansion: str | None = None,
    sightings: int = 1,
    articles: int = 1,
    attempted: str | None = None,
    entry_id: str | None = None,
) -> None:
    conn.execute(
        "INSERT INTO acronym_definitions (id, surface, normalized, expansion, method, "
        "confidence, status, sighting_count, article_count, first_seen, last_seen, "
        "last_attempt_at, attempt_count) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            entry_id or f"gloss:{surface.lower()}",
            surface,
            surface.lower(),
            expansion,
            "undefined" if expansion is None else "web_grounded",
            0.0 if expansion is None else 0.7,
            status,
            sightings,
            articles,
            NOW,
            NOW,
            attempted,
            1 if attempted else 0,
        ),
    )
    conn.commit()


def _surfaces(rows: list[db.Row]) -> list[str]:
    return [r["surface"] for r in rows]


#: The service the estimate is scoped to. The suite pins the instance to this
#: one (`conftest._the_suite_owns_its_model_service`), and a sample row has to
#: agree with it or it is a measurement of the wrong thing.
HARNESS = "claude_code"


def _lookup_run(
    conn: db.Conn, run_id: str, *, cost: float, seconds: int, harness: str = HARNESS
) -> None:
    """A finished single-surface lookup, which is what the estimate is measured
    from."""
    finished = (
        (datetime.fromisoformat(NOW.replace("Z", "+00:00")) + timedelta(seconds=seconds))
        .isoformat()
        .replace("+00:00", "Z")
    )
    conn.execute(
        "INSERT INTO runs (id, kind, status, cost_usd, started_at, finished_at, harness) "
        "VALUES (?,?,?,?,?,?,?)",
        (run_id, "lookup", "succeeded", cost, NOW, finished, harness),
    )
    conn.commit()


@pytest.fixture
def empty(conn: db.Conn) -> db.Conn:
    """The seeded glossary, cleared. These tests are about WHICH rows are
    eligible, so the fixture's own acronyms are noise -- and asserting an exact
    list against them would break every time the seed gains a term."""
    conn.execute("DELETE FROM acronym_definitions")
    conn.commit()
    return conn


# ── who is eligible ──────────────────────────────────────────────────


def test_the_sweep_takes_the_undefined_queue_most_seen_first(empty: db.Conn) -> None:
    _entry(empty, "DDIL", sightings=2)
    _entry(empty, "MOSA", sightings=9)
    _entry(empty, "CJADC2", sightings=5)

    assert _surfaces(undefined_candidates(empty)) == ["MOSA", "CJADC2", "DDIL"]


def test_ties_break_on_the_surface_so_two_previews_agree(empty: db.Conn) -> None:
    """Determinism, not taste. The preview and the run call this separately; if
    the order were arbitrary, a cap would quote one set and attempt another."""
    _entry(empty, "ZULU", sightings=4)
    _entry(empty, "ALPHA", sightings=4)

    assert _surfaces(undefined_candidates(empty)) == ["ALPHA", "ZULU"]


@pytest.mark.negative
def test_a_rejection_is_never_looked_up_again(empty: db.Conn) -> None:
    """`rejected` is a human verdict. Paying to overturn it is the same mistake
    as the pipeline re-grounding a rejection, which `decided_at` exists to
    stop -- except this one would do it three hundred times and bill for it."""
    _entry(empty, "WARSAW", status="rejected")

    assert undefined_candidates(empty) == []


@pytest.mark.negative
def test_a_surface_that_is_already_defined_is_not_bought_twice(
    empty: db.Conn,
) -> None:
    """The rows are keyed `(normalized, expansion)`, so a grounded definition
    and a stale undefined row for the same surface can both exist. Only the
    JOIN proves it, and this is exactly the caller that would otherwise pay for
    every one of them at once."""
    _entry(
        empty,
        "MOSA",
        status="active",
        expansion="Modular Open Systems Approach",
        entry_id="gloss:mosa-modular-open-systems-approach",
    )
    _entry(empty, "MOSA", status="undefined", entry_id="gloss:mosa")

    assert undefined_candidates(empty) == []


@pytest.mark.negative
def test_a_surface_already_tried_is_skipped_unless_retry_is_asked_for(
    empty: db.Conn,
) -> None:
    """A tier-3 lookup that grounded nothing rejected every candidate it found.
    Running it again over the same web costs the same and fails the same way.

    `last_attempt_at` is the column the hover card already reads to stop
    offering a first-time lookup for a question that has been asked.
    """
    _entry(empty, "NEVER")
    _entry(empty, "TRIED", attempted=NOW)

    assert _surfaces(undefined_candidates(empty)) == ["NEVER"]
    assert sorted(_surfaces(undefined_candidates(empty, retry=True))) == ["NEVER", "TRIED"]


def test_the_cap_drops_the_tail_a_reader_is_least_likely_to_meet(
    empty: db.Conn,
) -> None:
    _entry(empty, "COMMON", sightings=30)
    _entry(empty, "RARE", sightings=1)

    assert _surfaces(undefined_candidates(empty, cap=1)) == ["COMMON"]
    # `None` is uncapped, which is what the button asks for by default.
    assert len(undefined_candidates(empty, cap=None)) == 2


# ── the number the modal quotes ──────────────────────────────────────


def _request():
    """Only `request.app.state.settings` is read."""
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(settings=get_settings())))


def test_the_preview_counts_exactly_what_the_run_would_attempt(
    empty: db.Conn,
) -> None:
    """The whole reason the confirmation is worth having. A preview that counted
    the 303-row queue while the run attempted the 180 never tried would quote
    nearly double and be believed."""
    _entry(empty, "ONE", sightings=3)
    _entry(empty, "TWO", sightings=2)
    _entry(empty, "DONE", attempted=NOW)

    preview = glossary.lookup_all_preview(_request(), retry=False, conn=empty)

    assert preview["eligible"] == 2
    assert preview["will_attempt"] == len(undefined_candidates(empty))
    # The gap between the queue and the estimate, named rather than left as an
    # unexplained difference.
    assert preview["extra"]["skipped_attempted"] == 1
    assert preview["sample"] == ["ONE", "TWO"]


async def test_the_whole_queue_is_reachable_from_the_same_modal(api_client, api_db) -> None:
    """The batch is a default, not a ceiling. `uncapped` drops it, and the
    preview has to drop it too -- a modal quoting one batch in front of a button
    that takes the queue is the same defect as one counting the wrong rows, and
    the price is the whole reason a person is allowed to ask for this.

    Over the wire rather than by calling the function, because the query string
    is half of what is under test: `uncapped=false` arrives as a string, and a
    boolean parameter that is only ever read in Python looks fine while every
    press of the button sends the opposite.
    """
    api_db.execute("DELETE FROM acronym_definitions")
    api_db.commit()
    _entry(api_db, "ONE", sightings=3)
    _entry(api_db, "TWO", sightings=2)
    _lookup_run(api_db, "run:l0", cost=0.10, seconds=20)

    batched = (await api_client.get("/api/glossary/lookup-all/preview?limit=1")).json()
    assert (batched["eligible"], batched["will_attempt"], batched["cap"]) == (2, 1, 1)
    assert batched["estimate_usd"] == pytest.approx(0.10)

    whole = (await api_client.get("/api/glossary/lookup-all/preview?limit=1&uncapped=true")).json()
    assert (whole["eligible"], whole["will_attempt"], whole["cap"]) == (2, 2, None)
    assert whole["estimate_usd"] == pytest.approx(0.20)


def test_the_estimate_is_measured_from_this_machines_own_lookups(
    empty: db.Conn,
) -> None:
    """A constant in the source is a guess about someone else's corpus. The only
    figure a person can check against the Runs page is the one taken from it."""
    _entry(empty, "ONE")
    _entry(empty, "TWO")

    blind = glossary.lookup_all_preview(_request(), retry=False, conn=empty)
    assert blind["cost_sample"] == 0, "nothing measured yet"
    assert blind["per_item_usd"] == glossary.FALLBACK_LOOKUP_USD

    _lookup_run(empty, "run:l0", cost=0.10, seconds=20)
    _lookup_run(empty, "run:l1", cost=0.20, seconds=40)

    measured = glossary.lookup_all_preview(_request(), retry=False, conn=empty)
    assert measured["cost_sample"] == 2
    assert measured["per_item_usd"] == pytest.approx(0.15)
    assert measured["estimate_usd"] == pytest.approx(0.30)
    # Sequential, so the total is the sum -- which is what makes a three hundred
    # row sweep hours rather than minutes, and is half of what the modal warns
    # about.
    assert measured["per_item_seconds"] == pytest.approx(30.0)
    assert measured["estimate_seconds"] == 60

    # AND ONLY THIS SERVICE'S. Two services do not cost the same, and one may
    # be metered while the other is not -- so a sweep priced from the other
    # one's history is a confident figure for work nobody is about to do. A
    # $9.00 lookup from somewhere else must not move this quote at all.
    _lookup_run(empty, "run:other", cost=9.00, seconds=40, harness="codex")
    after = glossary.lookup_all_preview(_request(), retry=False, conn=empty)
    assert after["per_item_usd"] == pytest.approx(0.15), (
        "the estimate took its price from a service this instance is not using"
    )


@pytest.mark.negative
def test_the_estimate_ignores_lookups_that_cost_nothing(empty: db.Conn) -> None:
    """`cost_usd > 0` in the sample. Offline replays and coalesced runs write
    0.0, and a mean that includes them trends to zero -- which would put a $24
    sweep behind a modal quoting pennies."""
    _lookup_run(empty, "run:free0", cost=0.0, seconds=1)
    _lookup_run(empty, "run:free1", cost=0.0, seconds=1)
    _lookup_run(empty, "run:paid", cost=0.12, seconds=30)

    usd, _seconds, sample = glossary._lookup_stats(empty, harness=HARNESS)
    assert (usd, sample) == (pytest.approx(0.12), 1)


@pytest.mark.negative
def test_a_lookup_still_in_flight_does_not_read_as_instant(
    empty: db.Conn,
) -> None:
    """A running row has no `finished_at`. Counting it as zero seconds would
    drag the average toward instant and quote a two-hour sweep as minutes."""
    _lookup_run(empty, "run:done", cost=0.10, seconds=40)
    empty.execute(
        "INSERT INTO runs (id, kind, status, cost_usd, started_at, harness) VALUES (?,?,?,?,?,?)",
        ("run:live", "lookup", "running", 0.05, NOW, HARNESS),
    )
    empty.commit()

    _usd, seconds, _sample = glossary._lookup_stats(empty, harness=HARNESS)
    assert seconds == pytest.approx(40.0)


# ── the run type ─────────────────────────────────────────────────────


def test_the_sweep_runs_in_the_light_lane(empty: db.Conn) -> None:
    """Lanes are about what a run WRITES, not how long it takes. This writes
    `acronym_definitions` and nothing else, so it cannot collide with a pipeline
    the way a second `documents`/`clusters` writer would -- and in the research
    lane a glossary sweep and the nightly run would refuse each other for no
    reason either could give."""
    assert run_types.lane_for("lookup_all") == run_types.LIGHT
    assert run_types.type_for("lookup_all").detail == "single"


def test_the_sweeps_stage_stays_out_of_the_pipelines_stage_list() -> None:
    """`--only` validates against `orchestrator.STAGES`. A `lookup` in it would
    make `venona run --only lookup` a valid-looking no-op."""
    assert run_types.type_for("lookup_all").stages == ("lookup",)
    assert "lookup" not in orchestrator.STAGES


def test_its_counters_are_frozen_names(empty: db.Conn) -> None:
    """Contract C6: the summary may only name counters that exist, or the Runs
    page renders a column that is never populated."""
    from venona.pipeline.types import RunCounters

    for name in run_types.type_for("lookup_all").summary:
        assert hasattr(RunCounters(), name), name
