"""What a run type declares about its own result, and how it resolves.

The pattern is `tests/test_run_outcomes.py`: the registry is a table, so what a
test can hold is that every row of it is complete and says nothing a reader
cannot read. Contract C11.

This file exists because nothing pinned the OTHER half of a run's words. The
chip's phrase came from `RunType.outcomes` and was tested; the sentence under it
was an if-chain in `RunResultHeader.vue` over keys nothing declared, and the
result panel was shaped for a lookup. So a reader's brief -- which emits none of
`considered`, `grounded` or `pages_read` -- rendered `sourced: no` on a run that
had just written a document, and no test could see it.
"""

from __future__ import annotations

import re

import pytest
from venona.services import run_facts, run_service, run_types

ALL_KINDS = list(run_types.RUN_TYPES.values())
IDS = [t.kind for t in ALL_KINDS]

#: Same three forms `run_facts._SLOT` reads. Duplicated here on purpose: a test
#: that imports the regex it is checking cannot catch the regex being wrong.
SLOT = re.compile(r"\{(\??)([a-z_]+\.[a-z_0-9]+)(?:\|[^|{}]*\|[^|{}]*)?\}")

NAMESPACES = ("counts.", "params.", "run.")


# ── the declaration is complete ──────────────────────────────────────


@pytest.mark.parametrize("run_type", ALL_KINDS, ids=IDS)
def test_every_kind_says_what_it_produced(run_type) -> None:
    """A kind with no facts renders an empty panel, which is what the hardcoded
    lookup shape was hiding: the panel looked full because it was showing
    another kind's fields."""
    assert run_type.facts, run_type.kind
    assert run_type.lines, run_type.kind


@pytest.mark.parametrize("run_type", ALL_KINDS, ids=IDS)
def test_every_outcome_with_a_phrase_also_has_a_sentence(run_type) -> None:
    """The chip and the line answer the same question at two lengths, so a kind
    that can say "not found" on a chip and nothing underneath it has a page that
    stops mid-thought. This is the rule that makes "consistent across every run
    type" a fact rather than an intention."""
    for outcome in run_type.outcomes:
        assert outcome in run_type.lines, f"{run_type.kind} has no sentence for {outcome!r}"


@pytest.mark.parametrize("run_type", ALL_KINDS, ids=IDS)
def test_every_sentence_names_an_outcome_that_kind_can_reach(run_type) -> None:
    """A sentence for a value that does not exist is a string nothing can ever
    render, and it looks exactly like coverage."""
    for outcome in run_type.lines:
        assert outcome in run_service.OUTCOMES, f"{run_type.kind} declares {outcome!r}"


@pytest.mark.parametrize("run_type", ALL_KINDS, ids=IDS)
def test_the_last_candidate_always_fills(run_type) -> None:
    """Half of what guarantees a sentence on every run, including one recorded
    before any of these keys existed. A trailing candidate with a slot in it can
    fail to fill, and then the outcome has nothing to say after all."""
    for outcome, candidates in run_type.lines.items():
        assert candidates, f"{run_type.kind}/{outcome} declares an empty list"
        assert not SLOT.search(candidates[-1]), (
            f"{run_type.kind}/{outcome}: the last candidate {candidates[-1]!r} "
            f"carries a slot, so this outcome can render blank"
        )


@pytest.mark.parametrize("run_type", ALL_KINDS, ids=IDS)
def test_no_fact_names_a_frozen_counter(run_type) -> None:
    """The other half of the "two panels, two jobs" split.

    `summary` already names which of the thirteen frozen `RunCounters` are worth
    showing, and `lib/runCopy.ts` already gives each of them a reader's word. A
    fact naming one would render the same number twice on one page under two
    labels, and would put a second counter-to-words map in Python for the first
    one to drift against.
    """
    for fact in run_type.facts:
        assert fact.key.startswith(NAMESPACES), f"{run_type.kind}: {fact.key!r}"


@pytest.mark.parametrize("run_type", ALL_KINDS, ids=IDS)
def test_every_slot_names_a_declared_fact_namespace(run_type) -> None:
    """Slots are qualified for the same reason keys are: `counts` is merged
    across stages, so a bare `{documents_new}` would have three possible sources
    and no stated winner."""
    for candidates in run_type.lines.values():
        for candidate in candidates:
            for _, key in SLOT.findall(candidate):
                assert key.startswith(NAMESPACES), f"{run_type.kind}: {key!r}"


@pytest.mark.parametrize("run_type", ALL_KINDS, ids=IDS)
def test_a_kind_that_summarises_model_calls_admits_it_uses_a_model(run_type) -> None:
    """`uses_model` decides whether the page offers the session behind the run.
    A kind counting `llm_calls` and declaring otherwise would hide the log for
    exactly the runs that have one."""
    if "llm_calls" in run_type.summary:
        assert run_type.uses_model, run_type.kind


@pytest.mark.parametrize("run_type", ALL_KINDS, ids=IDS)
def test_a_flag_says_what_it_means(run_type) -> None:
    """`analysed: yes` is a fact; `document: new` / `already held` is an answer.
    A flag with no words falls back to the value, which for a bool is `True`."""
    for fact in run_type.facts:
        if fact.shape == "flag":
            assert len(fact.words) == 2, f"{run_type.kind}: {fact.key} has no words"


@pytest.mark.parametrize("run_type", ALL_KINDS, ids=IDS)
def test_a_link_has_somewhere_to_go(run_type) -> None:
    for fact in run_type.facts:
        if fact.shape == "link":
            assert fact.href and "{value}" in fact.href, f"{run_type.kind}: {fact.key}"


#: Kinds whose work hands back the candidates it turned DOWN, as a list.
#: `lookup` does, from `routers/glossary.py`. `definitions` increments an
#: integer in `routers/entities.py::refresh_definitions`, and declaring the
#: trace shape there rendered the number nowhere at all.
TRACE_KINDS = {"lookup"}


@pytest.mark.parametrize("run_type", ALL_KINDS, ids=IDS)
def test_only_a_kind_that_emits_a_trace_declares_the_candidates_shape(run_type) -> None:
    """A candidates fact whose value is a NUMBER renders nowhere, silently.

    `RunFacts.vue` splits the facts two ways: `plain` drops everything shaped
    `candidates`, and `traces` keeps only those whose value `Array.isArray`. A
    kind that declares the shape and emits an int falls out of both, so the
    panel is missing a line and nothing anywhere says so. `RunDetailView.vue`
    guards the same way.
    """
    declared = sorted(f.key for f in run_type.facts if f.shape == "candidates")
    if run_type.kind in TRACE_KINDS:
        return
    assert not declared, (
        f"{run_type.kind} declares {declared} as candidates -- add it to "
        "TRACE_KINDS only if its work emits a list, not a count"
    )


@pytest.mark.parametrize("run_type", ALL_KINDS, ids=IDS)
def test_the_subject_key_is_reachable_from_a_list_row(run_type) -> None:
    """`subject_text` resolves on the history list, whose projection selects
    `params` and the run's own columns but deliberately not stages. A kind
    naming a `counts.` key would silently render nothing there."""
    if run_type.subject:
        assert run_type.subject.startswith(("params.", "run.")), run_type.kind


# ── none of it breaks a copy rule ────────────────────────────────────


def _visible(text: str) -> str:
    """The sentence as a reader sees it, with the slots taken out."""
    return SLOT.sub("", text)


@pytest.mark.parametrize("run_type", ALL_KINDS, ids=IDS)
def test_no_label_or_sentence_breaks_a_copy_rule(run_type) -> None:
    """The same three rules `test_run_outcomes.py` holds for the chip, with one
    difference: a chip phrase is lower case and a sentence is a sentence. So the
    case rule inverts rather than being dropped.

    `undefined` is `scripts/ui_smoke.py`'s marker for a JS value rendered into
    the DOM, and a legitimate label carrying the word disarms that check for the
    whole page. Em and en dashes are banned on every surface a person reads
    (`tests/test_copy_style.py` holds the other half). And these render to a
    reader, so they carry none of this repo's vocabulary.
    """
    jargon = ("entity", "cluster", "assertion", "corpus", "gloss", "lane", "run_id", "projection")
    for fact in run_type.facts:
        assert "undefined" not in fact.label, f"{run_type.kind}: {fact.label!r}"
        assert "—" not in fact.label and "–" not in fact.label, f"{run_type.kind}: {fact.label!r}"
        # Sentence case, not lower case: "Venona" keeps its capital wherever it
        # appears (it is the product's name), so the rule is about the FIRST
        # letter. Title Case is what this is here to catch.
        assert fact.label[:1] == fact.label[:1].lower(), f"{run_type.kind}: {fact.label!r}"
        for word in jargon:
            assert word not in fact.label, f"{run_type.kind}: {fact.label!r} says {word!r}"
    for candidates in run_type.lines.values():
        for candidate in candidates:
            visible = _visible(candidate).strip()
            assert "undefined" not in visible, f"{run_type.kind}: {candidate!r}"
            assert "—" not in visible and "–" not in visible, f"{run_type.kind}: {candidate!r}"
            assert visible[:1] == visible[:1].upper(), f"{run_type.kind}: {candidate!r}"
            assert visible.endswith("."), f"{run_type.kind}: {candidate!r} has no full stop"
            for word in jargon:
                assert word not in visible.lower(), f"{run_type.kind}: {candidate!r} says {word!r}"


# ── resolution ───────────────────────────────────────────────────────


def _run(kind: str, *, outcome=None, status="succeeded", counts=None, params=None, **rest):
    return {
        "id": "run:x",
        "kind": kind,
        "status": status,
        "outcome": outcome,
        "params": params or {},
        "stages": [{"counts": counts or {}}],
        **rest,
    }


def test_a_missing_key_is_omitted_rather_than_shown_as_nothing() -> None:
    """How a run recorded before a key existed degrades. The alternative is a
    panel of blanks and zeroes, which reads as a run that did nothing rather
    than one nobody was recording this about yet."""
    facts = run_facts.resolve_facts(_run("lookup", outcome="delivered", params={"surface": "MOSA"}))
    assert [f["key"] for f in facts] == ["params.surface"]


def test_a_zero_count_is_not_a_fact_but_a_false_flag_is() -> None:
    """`names new 0` is one of the eleven zeroes `summary` exists to stop. A
    false flag is the opposite: "already held" is the whole answer."""
    facts = run_facts.resolve_facts(
        _run("ingest", outcome="nothing_to_do", counts={"entities_new": 0, "is_new": False})
    )
    assert [(f["key"], f["text"]) for f in facts] == [("counts.is_new", "already held")]


def test_zero_and_absent_are_different_sentences() -> None:
    """A lookup that read no pages KNOWS it read none. One recorded before
    `pages_read` existed knows nothing, and must not be handed the confident
    sentence -- which is what a presence-only slot separates."""
    read_none = run_facts.resolve_line(
        _run("lookup", outcome="nothing_found", counts={"pages_read": 0})
    )
    unrecorded = run_facts.resolve_line(_run("lookup", outcome="nothing_found"))
    read_six = run_facts.resolve_line(
        _run("lookup", outcome="nothing_found", counts={"pages_read": 6})
    )
    assert read_six == "Read 6 pages and none of them defined it."
    assert read_none == "No page could be read, so the question is still open."
    assert unrecorded == "The work ran and the answer was no."


def test_a_count_carries_its_own_noun() -> None:
    one = run_facts.resolve_line(_run("personal_brief", outcome="delivered", counts={"stories": 1}))
    many = run_facts.resolve_line(
        _run("personal_brief", outcome="delivered", counts={"stories": 9})
    )
    assert one == "Wrote about 1 story for this reader."
    assert many == "Wrote about 9 stories for this reader."


@pytest.mark.parametrize("status", ["succeeded", "succeeded_partial"])
def test_a_succeeding_run_with_no_outcome_still_says_something(status) -> None:
    """Reachable three ways, none of them exotic: `--only fetch` and
    `skip_brief` finish with no outcome by design, an exception after committed
    work clears it, and migration 0018 left it NULL on every historical row it
    could not classify. Keying the sentence on `outcome` alone blanks all of
    them, which is most of what clicking back through history lands on."""
    assert run_facts.resolve_line(_run("cli", status=status, outcome=None))


def test_a_failed_run_says_why_before_it_says_anything_else() -> None:
    line = run_facts.resolve_line(_run("cli", status="failed", error="BriefError: no clusters"))
    assert line == "BriefError: no clusters"


def test_a_failed_run_with_no_error_still_says_something() -> None:
    assert run_facts.resolve_line(_run("cli", status="failed")) == run_types.STATUS_LINES["failed"]


def test_an_unknown_kind_gets_no_invented_facts() -> None:
    """`type_for` never raises and the runs table outlives any release's idea of
    what a run is. What it must not do is borrow another kind's shape -- which
    is the failure the whole declaration replaced."""
    assert (
        run_facts.resolve_facts(_run("who-knows", outcome="delivered", counts={"stories": 3})) == []
    )


def test_counts_merge_across_stages_with_the_later_stage_winning() -> None:
    run = {
        "id": "run:x",
        "kind": "cli",
        "status": "succeeded",
        "outcome": "delivered",
        "params": {},
        "stages": [{"counts": {"blocks": 1}}, {"counts": {"blocks": 9, "spans": 4}}],
    }
    facts = {f["key"]: f["value"] for f in run_facts.resolve_facts(run)}
    assert facts["counts.blocks"] == 9
    assert facts["counts.spans"] == 4


def test_a_reader_is_never_rendered_as_a_user_id() -> None:
    """CLAUDE.md bans database values on a surface a person reads. With no
    connection to resolve the name, the fact is dropped rather than shown raw."""
    facts = run_facts.resolve_facts(
        _run(
            "personal_brief",
            outcome="delivered",
            counts={"stories": 2},
            params={"user_id": "user:1"},
        )
    )
    assert [f["key"] for f in facts] == ["counts.stories"]


def test_the_history_list_names_a_reader_by_address_and_never_by_id() -> None:
    """A history row is a surface a person reads, so it names the reader by the
    address every other actor column uses (C1) and never by `user:0193c8...`.
    The warm pass has no person behind it, and a row with nobody to name says
    nothing rather than falling back to the id."""
    run_type = run_types.type_for("personal_brief")
    lazy = {"kind": "personal_brief", "params": {"user_id": "user:1"}, "started_by_email": "a@b.c"}
    warmed = {"kind": "personal_brief", "params": {"user_id": "user:1"}, "started_by_email": None}
    assert run_facts.subject_text(lazy, run_type.subject) == "a@b.c"
    assert run_facts.subject_text(warmed, run_type.subject) is None
