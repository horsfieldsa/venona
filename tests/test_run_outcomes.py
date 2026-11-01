"""Contract C11 / ADR 0183 -- a run's two verdicts, and the wall between them.

`runs.status` answers "did the machinery work". `runs.outcome` answers "was
there an answer, and did we get it". They were one field, and the collision was
not theoretical: 44 of the 68 lookups on the defence corpus read
`succeeded_partial` with an empty `warnings` array and a null `error`, because
"the web holds no definition for this" had nowhere else to go.

What is pinned here is the WALL. Three rules, each of which the single field
made impossible to state:

  * A run that found nothing is not degraded.
  * A run that degraded may still have delivered.
  * A run that failed has no outcome at all.
"""

from __future__ import annotations

import pytest
from venona.api.schemas import run as run_schema
from venona.pipeline.types import FrozenClock, RunContext, RunMessage, SeededIdGen
from venona.services import run_service, run_types
from venona.settings import get_settings

# ── the vocabulary ───────────────────────────────────────────────────


def test_the_outcome_vocabulary_is_four_words() -> None:
    """Frozen deliberately, like `RunCounters`. Every phrase map, the pydantic
    Literal and the migration's backfill all enumerate these, so a fifth value
    is a five-file change and should feel like one."""
    assert run_service.OUTCOMES == ("delivered", "some", "nothing_found", "nothing_to_do")


def test_no_outcome_value_collides_with_a_status() -> None:
    """`outcome='partial'` beside `status='succeeded_partial'` would be two
    near-identical words meaning different things in one row, which is the
    confusion this column exists to remove. `some` is the spelling that keeps
    them apart."""
    for outcome in run_service.OUTCOMES:
        for status in run_service.TERMINAL_STATUSES:
            assert outcome not in status


def test_the_wire_enum_matches_the_service() -> None:
    """`RunOutcome` is written as a Literal so it reaches the OpenAPI document
    as an enum and the generated client can narrow it. That makes it a second
    copy of the list, so it is pinned rather than trusted."""
    from typing import get_args

    assert get_args(run_schema.RunOutcome) == run_service.OUTCOMES


# ── validation at the boundary ───────────────────────────────────────


def test_finish_run_refuses_an_outcome_it_does_not_know(conn) -> None:
    """No CHECK constraint on the column, matching `status`: a `runs` table
    that raises on a row an older release wrote is worse than one that renders
    it plainly. So the vocabulary is held HERE instead, at the one place a
    terminal row is written."""
    run_service.create_run(
        conn,
        run_id="run:bad",
        kind="lookup",
        params={},
        profile_hash=None,
        now="2026-08-31T00:00:00Z",
    )
    with pytest.raises(ValueError, match="not a run outcome"):
        run_service.finish_run(
            conn,
            run_id="run:bad",
            status="succeeded",
            counters={},
            warnings=[],
            brief_id=None,
            cost_usd=0.0,
            error=None,
            now="2026-08-31T00:00:01Z",
            outcome="partial",
        )


def test_a_run_may_finish_with_no_outcome_at_all(conn) -> None:
    """A failed or cancelled run never reached an answer. Stamping one anyway
    would be the same overload `status` is being relieved of, running the other
    way."""
    run_service.create_run(
        conn,
        run_id="run:none",
        kind="lookup",
        params={},
        profile_hash=None,
        now="2026-08-31T00:00:00Z",
    )
    run_service.finish_run(
        conn,
        run_id="run:none",
        status="failed",
        counters={},
        warnings=[],
        brief_id=None,
        cost_usd=0.0,
        error="boom",
        now="2026-08-31T00:00:01Z",
    )
    assert run_service.get_run(conn, "run:none")["outcome"] is None


# ── the reader's words ───────────────────────────────────────────────


def test_every_declared_phrase_names_a_real_outcome() -> None:
    """A phrase for a value that does not exist is a string nothing can ever
    render, and it looks exactly like coverage."""
    for run_type in run_types.RUN_TYPES.values():
        for value in run_type.outcomes:
            assert value in run_service.OUTCOMES, f"{run_type.kind} declares {value!r}"


def test_every_kind_can_say_that_it_delivered() -> None:
    """`delivered` is reachable for every kind of run there is -- that is what
    a run is FOR -- so every kind owes it a phrase. The other three are
    per-kind: a lookup can never return `nothing_to_do`, since there is always
    exactly one surface to look up, and a phrase for it would be a lie waiting
    to render."""
    for run_type in run_types.RUN_TYPES.values():
        assert "delivered" in run_type.outcomes, run_type.kind


@pytest.mark.parametrize("run_type", run_types.RUN_TYPES.values(), ids=lambda t: t.kind)
def test_no_phrase_breaks_a_copy_rule(run_type) -> None:
    """Three rules, each with a scar behind it.

    `undefined` is `scripts/ui_smoke.py`'s marker for a JS value rendered into
    the DOM, and a legitimate label carrying the word disarms that check for
    every row on the page. Em and en dashes are banned on every user-facing
    surface (`tests/test_copy_style.py` holds the other half). And these render
    to a person, so they carry no schema vocabulary.
    """
    for phrase in run_type.outcomes.values():
        assert "undefined" not in phrase, run_type.kind
        assert "—" not in phrase and "–" not in phrase, run_type.kind
        assert phrase == phrase.lower(), f"{run_type.kind}: {phrase!r} is not sentence case"
        for word in ("entity", "cluster", "assertion", "corpus", "gloss", "lane", "run_id"):
            assert word not in phrase, f"{run_type.kind}: {phrase!r} says {word!r}"


def test_a_row_carries_its_outcome_in_its_own_kind_of_words() -> None:
    """The same stored value, two readers. `decorate` resolves it server-side
    for the same reason it resolves `label`: every surface that reports a
    finished run needs the phrase, and a second copy of the map in TypeScript
    would drift inside a release."""
    assert run_schema.decorate({"kind": "lookup", "outcome": "nothing_found"})["outcome_text"] == (
        "not found"
    )
    assert run_schema.decorate({"kind": "daily", "outcome": "nothing_to_do"})["outcome_text"] == (
        "nothing new"
    )


def test_an_unknown_kind_gets_no_invented_phrase() -> None:
    """`type_for` never raises -- the `runs` table outlives any release's idea
    of what a run is. What it must not do is put words in a stranger's mouth."""
    assert run_types.type_for("something_new").outcomes == {}
    assert (
        run_schema.decorate({"kind": "something_new", "outcome": "delivered"})["outcome_text"]
        is None
    )


# ── what a shell sees ────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("status", "outcome", "code"),
    [
        ("succeeded", "delivered", 0),
        ("succeeded", "some", 0),
        # ⭐ The code that could not exist before. `venona lookup` exited 0 when
        # the web held no definition and 1 when the web was unreachable, so the
        # only two states a script could tell apart were the wrong two.
        ("succeeded", "nothing_found", 2),
        ("succeeded", "nothing_to_do", 2),
        # A degraded run that still delivered is a success to a shell; the
        # warnings are for a person.
        ("succeeded_partial", "delivered", 0),
        ("succeeded_partial", "nothing_found", 2),
        ("failed", None, 1),
        ("cancelled", None, 1),
    ],
)
def test_the_exit_code_reports_both_verdicts(status, outcome, code) -> None:
    from venona.cli import exit_code

    assert exit_code(status, outcome) == code


# ── the message's own level ──────────────────────────────────────────
#
# `status` says whether the machinery worked and `outcome` says whether there
# was an answer (ADR 0183). Neither could say which of a run's own MESSAGES
# was the degradation: `warn` and `note` appended to one array of bare
# strings, so `RunDetailView` picked one heading for all of them out of the
# run's status. A degraded run rendered its curation backlog as a reason it
# degraded; a clean one rendered a genuine warning as an idle remark.


def _bare_context() -> RunContext:
    """No database and no executor: these assert on the channels alone."""
    return RunContext(
        run_id="run:t",
        db=None,
        llm=None,
        settings=get_settings(),
        clock=FrozenClock(),
        ids=SeededIdGen(0),
    )


def _finish(conn, run_id, *, status, warnings, outcome=None, error=None):
    run_service.create_run(
        conn,
        run_id=run_id,
        kind="daily",
        params={},
        profile_hash=None,
        now="2026-09-01T00:00:00Z",
    )
    run_service.finish_run(
        conn,
        run_id=run_id,
        status=status,
        counters={},
        warnings=warnings,
        brief_id=None,
        cost_usd=0.0,
        error=error,
        now="2026-09-01T00:00:01Z",
        outcome=outcome,
    )
    return run_service.get_run(conn, run_id)


def test_only_warn_degrades_a_run() -> None:
    """The rule the whole split rests on, asserted on the context itself."""
    ctx = _bare_context()
    ctx.note("folded two readings into one")
    ctx.todo("42 names to decide on", to="curation")
    assert not ctx.degraded, "a note or a todo is not a degradation"
    ctx.warn("two feeds refused to fetch")
    assert ctx.degraded


def test_a_todo_must_name_a_destination_the_client_can_resolve() -> None:
    """`to` is symbolic, and the symbol has to be one the web app maps.

    A raw route would go stale silently the day the router is edited; an
    unknown symbol renders as a todo with no link and no error, which is the
    same silence one layer along. So it is refused at the source.
    """
    ctx = _bare_context()
    with pytest.raises(ValueError, match="unknown destination"):
        ctx.todo("something", to="/curation")


def test_each_message_carries_its_own_level_to_the_row(conn) -> None:
    run = _finish(
        conn,
        "run:levels",
        status="succeeded_partial",
        outcome="delivered",
        warnings=[
            RunMessage("warning", "two feeds refused to fetch"),
            RunMessage("todo", "42 names to decide on", "curation"),
            RunMessage("note", "folded two readings into one"),
        ],
    )
    assert [m["level"] for m in run["warnings"]] == ["warning", "todo", "note"]
    assert run["warnings"][1]["to"] == "curation"
    # A note carries no destination at all rather than a null one.
    assert "to" not in run["warnings"][2]


def test_a_legacy_row_is_read_as_what_its_status_said(conn) -> None:
    """Rows written before messages had levels are a bare `list[str]`.

    The level is DERIVED, never guessed: `ctx.warn` is the only route to
    `succeeded_partial`, so on a `succeeded` run every message provably was a
    note. On a degraded row we cannot tell which were which, so they stay
    warnings -- that is what the row said at the time, and ADR 0183's rule is
    that a status is not rewritten after the fact.
    """
    assert run_service.hydrate_messages('["one feed 403d"]', status="succeeded_partial") == [
        {"level": "warning", "text": "one feed 403d"}
    ]
    assert run_service.hydrate_messages('["86 open conflicts"]', status="succeeded") == [
        {"level": "note", "text": "86 open conflicts"}
    ]
    # And a row already carrying levels is passed through untouched.
    tagged = '[{"level": "todo", "text": "x", "to": "curation"}]'
    assert run_service.hydrate_messages(tagged, status="succeeded") == [
        {"level": "todo", "text": "x", "to": "curation"}
    ]
