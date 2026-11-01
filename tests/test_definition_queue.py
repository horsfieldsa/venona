"""The definitions queue: one surface per decision, ranked, with a suggestion.

Converged on the merge queue, because it is the same job. Three properties that
list did not have, and that these pin:

  * the unit is a SURFACE. `FCC` appeared twice in the old list and `CPE` five
    times, so "how much is left" was a count of rows (604) rather than of
    questions;
  * a decided surface is GONE. `decided_at` existed and the row stayed;
  * the queue PROPOSES, from evidence that already existed and never reached
    the screen.
"""

from __future__ import annotations

import pytest
from helpers import ANON, NOW
from venona.api.routers import glossary
from venona.db import core as db
from venona.pipeline.acronyms import store_definition
from venona.services import definition_queue
from venona.services.definition_queue import looks_like_one_meaning


def _reading(conn, surface, expansion, url, *, method="web_grounded", confidence=0.7) -> str:
    return store_definition(
        conn,
        surface=surface,
        expansion=expansion,
        method=method,
        confidence=confidence,
        article_id=None,
        quote=f"The {expansion} ({surface}) is a thing.",
        url=url,
        now=NOW,
    )


def _undefined(conn, surface, sightings=1, attempted=False) -> None:
    # `db.upsert` rather than the `INSERT OR REPLACE` this was: that is SQLite
    # syntax, and `upsert` already emits the portable
    # `ON CONFLICT (...) DO UPDATE` form.
    db.upsert(
        conn,
        "acronym_definitions",
        {
            "id": f"gloss:{surface.lower()}",
            "surface": surface,
            "normalized": surface.lower(),
            "method": "undefined",
            "confidence": 0.0,
            "status": "undefined",
            "sighting_count": sightings,
            "article_count": 1,
            "first_seen": NOW,
            "last_seen": NOW,
            "last_attempt_at": NOW if attempted else None,
        },
        key=["id"],
    )


@pytest.fixture
def empty(conn: db.Conn) -> db.Conn:
    conn.execute("DELETE FROM acronym_definitions")
    conn.execute("DELETE FROM ambiguous_surfaces")
    conn.execute("DELETE FROM documents")
    conn.commit()
    return conn


def _by_surface(result: dict) -> dict[str, dict]:
    return {i["surface"]: i for i in result["items"]}


# ── one surface, not one row ─────────────────────────────────────────


def test_a_surface_with_five_readings_is_one_decision(empty: db.Conn) -> None:
    for expansion in ("Capability Program Executive", "customer premise equipment"):
        _reading(empty, "CPE", expansion, f"https://x.test/{expansion[:4]}")

    result = definition_queue.build(empty)
    assert result["total_pending"] == 1, "one question, not two rows"
    assert len(_by_surface(result)["CPE"]["readings"]) == 2


def test_a_decided_surface_leaves_the_queue(empty: db.Conn) -> None:
    """The whole difference between a queue and a list."""
    _reading(empty, "CPE", "Capability Program Executive", "https://a.test/1")
    _reading(empty, "CPE", "customer premise equipment", "https://b.test/2")
    assert definition_queue.build(empty)["total_pending"] == 1

    glossary.keep_both("CPE", ANON, conn=empty)

    result = definition_queue.build(empty)
    assert result["total_pending"] == 0
    assert result["decided"] == 1


# ── ranking: what a decision is worth ────────────────────────────────


def test_the_order_is_by_consequence_not_frequency(empty: db.Conn) -> None:
    """Sorting by sightings alone is what buried the twenty-five decisions worth
    making under five hundred that were not."""
    _undefined(empty, "COMMON", sightings=99)
    _reading(empty, "SETTLED", "Single Grounded Reading", "https://a.test/1")
    _reading(empty, "SPLIT", "Alpha Bravo Charlie", "https://b.test/2")
    _reading(empty, "SPLIT", "Delta Echo Foxtrot", "https://c.test/3")

    order = [i["surface"] for i in definition_queue.build(empty)["items"]]
    assert order.index("SPLIT") < order.index("SETTLED") < order.index("COMMON")


# ── the suggestion ───────────────────────────────────────────────────


def test_spelling_variants_are_proposed_as_one_meaning(empty: db.Conn) -> None:
    _reading(empty, "CRADA", "Cooperative Research and Development Agreement", "https://a.test/1")
    _reading(empty, "CRADA", "Cooperative Research and Development Agreements", "https://b.test/2")

    assert _by_surface(definition_queue.build(empty))["CRADA"]["suggestion"]["verdict"] == (
        "same_thing"
    )


def test_readings_on_comparable_evidence_are_proposed_as_both(
    empty: db.Conn,
) -> None:
    _reading(empty, "ISV", "Infantry Squad Vehicle", "https://a.test/1")
    _reading(empty, "ISV", "independent software vendor", "https://b.test/2")

    assert _by_surface(definition_queue.build(empty))["ISV"]["suggestion"]["verdict"] == "keep_both"


def test_a_much_stronger_reading_is_proposed_as_the_winner(empty: db.Conn) -> None:
    """`VENOM` is the case: a tier-1 parenthetical from a document we hold
    against a web page about a 2015 security vulnerability. Proposing "keep
    both" there asks the curator to bless a reading the evidence outranks."""
    weak = _reading(empty, "VENOM", "Virtualized Environment Neglected Ops", "https://a.test/1")
    strong = _reading(
        empty,
        "VENOM",
        "Viper Experimentation and Next-generation Operations Model",
        None,
        method="in_article",
        confidence=1.0,
    )

    suggestion = _by_surface(definition_queue.build(empty))["VENOM"]["suggestion"]
    assert suggestion["verdict"] == "choose"
    assert suggestion["entry_id"] == strong
    assert suggestion["entry_id"] != weak


def test_an_untried_surface_is_proposed_for_lookup(empty: db.Conn) -> None:
    _undefined(empty, "NGCM", sightings=21)
    assert _by_surface(definition_queue.build(empty))["NGCM"]["suggestion"]["verdict"] == "lookup"


@pytest.mark.negative
def test_a_tried_and_failed_surface_is_proposed_nothing(empty: db.Conn) -> None:
    """Suggesting a second lookup would propose spending money on a question
    already asked and answered."""
    _undefined(empty, "DFR", sightings=12, attempted=True)
    assert _by_surface(definition_queue.build(empty))["DFR"]["suggestion"] is None


# ── the same-meaning check, on the real pairs ────────────────────────


@pytest.mark.parametrize(
    ("expansions", "one_meaning"),
    [
        (["Fiscal Year", "Fiscal Years", "In fiscal year"], True),
        (["Artificial Intelligence", "of artificial intelligence"], True),
        # Plurals are the commonest shape of the thing this exists for, and the
        # check missed them until `_singular` was added.
        (
            [
                "Cooperative Research and Development Agreement",
                "Cooperative Research and Development Agreements",
            ],
            True,
        ),
        (["Capability Program Executive", "customer premise equipment"], False),
        (["Agile Combat Employment", "Air Combat Evolution"], False),
        (["Infantry Squad Vehicle", "independent software vendor"], False),
    ],
)
def test_one_meaning_or_several(expansions: list[str], one_meaning: bool) -> None:
    assert looks_like_one_meaning(expansions) is one_meaning


@pytest.mark.negative
def test_access_does_not_become_acces() -> None:
    """`ss` is left alone by the plural strip."""
    assert not looks_like_one_meaning(["Access Control Point", "Acme Control Point"])


# ── the ranked queue is not re-ranked for every page ─────────────────


def test_paging_the_queue_ranks_it_once(conn: db.Conn, monkeypatch) -> None:
    """⭐ `build` read every definition and ranked the whole table in memory to
    return one page, and the curator pages through it.

    The heavy input -- the corpus audit -- was already memoised; the residual
    was the full read and the sort, re-paid per page.
    """
    from venona.db.repositories import definition_queue as queue_repo
    from venona.services import definition_queue

    reads = {"n": 0}
    real = queue_repo.select_acronym_definitions

    def counted(*args, **kwargs):
        reads["n"] += 1
        return real(*args, **kwargs)

    monkeypatch.setattr(queue_repo, "select_acronym_definitions", counted)

    first = definition_queue.build(conn, limit=1, offset=0)
    second = definition_queue.build(conn, limit=1, offset=1)

    assert reads["n"] == 1, f"page two must come from the ranked list, not read {reads['n']} times"
    assert first["total_pending"] == second["total_pending"]


def test_a_verdict_re_ranks_the_queue(conn: db.Conn) -> None:
    """A decision is exactly what takes a surface OUT of the queue, so the
    cache must not survive one."""
    from venona.services import definition_queue

    before = definition_queue.build(conn, limit=50)
    if not before["items"]:
        pytest.skip("the seeded corpus offers nothing to decide")

    target = before["items"][0]["normalized"]
    conn.execute(
        "UPDATE acronym_definitions SET decided_at = 't9' WHERE normalized = ?",
        (target,),
    )

    after = definition_queue.build(conn, limit=50)
    assert after["total_pending"] == before["total_pending"] - 1, (
        "a ruled surface must leave the queue rather than be served from a stale rank"
    )
