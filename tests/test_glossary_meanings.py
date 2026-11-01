"""Several meanings for one surface, and the person who settles it.

The disambiguation machinery has always existed -- `store_definition` marks a
surface `ambiguous` the moment a second expansion arrives, the alias index
refuses to annotate an ambiguous surface, and the hover card lists rather than
picks. Two things were missing, and they are the two things these cover:

  * nothing ever produced a SECOND meaning. `lookup_web` returned the first
    expansion that passed and stopped, so an ambiguous acronym looked exactly
    like an unambiguous one. Measured on this corpus: `FCC` grounded as "Fire
    Control Center" off a Kongsberg product page -- true of the letters, wrong
    for the articles using them -- and there was no way to ask what else it
    might mean.
  * nothing let a person END the ambiguity. The queue could accept one row or
    reject one row, so a three-way split had to be resolved by rejecting two of
    them individually, in a list that re-sorts under the cursor as you do it.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException
from helpers import ANON, NOW
from venona.api.routers import glossary
from venona.db import core as db
from venona.pipeline.acronyms import store_definition


def _meaning(conn: db.Conn, surface: str, expansion: str, url: str) -> str:
    return store_definition(
        conn,
        surface=surface,
        expansion=expansion,
        method="web_grounded",
        confidence=0.7,
        article_id=None,
        quote=f"The {expansion} ({surface}) is a thing.",
        url=url,
        now=NOW,
    )


@pytest.fixture
def empty(conn: db.Conn) -> db.Conn:
    conn.execute("DELETE FROM acronym_definitions")
    conn.execute("DELETE FROM ambiguous_surfaces")
    conn.commit()
    return conn


@pytest.fixture
def split(empty: db.Conn) -> tuple[db.Conn, str, str]:
    """FCC, with the two readings the live corpus actually produced."""
    fire = _meaning(empty, "FCC", "Fire Control Center", "https://kongsberg.com/fcc")
    futures = _meaning(empty, "FCC", "Futures and Concepts Command", "https://army.mil/fcc")
    return empty, fire, futures


# ── a second meaning makes the surface ambiguous ─────────────────────


def test_a_second_reading_splits_the_surface(split) -> None:
    conn, fire, futures = split
    rows = {
        r["id"]: r["status"]
        for r in conn.execute("SELECT id, status FROM acronym_definitions WHERE normalized='fcc'")
    }
    assert rows == {fire: "ambiguous", futures: "ambiguous"}
    # The annotator reads this table to decide NOT to mark the surface -- an
    # ambiguous acronym that still annotated would render one meaning's
    # definition over the other's mention.
    marked = conn.execute(
        "SELECT COUNT(*) FROM ambiguous_surfaces WHERE normalized='fcc'"
    ).fetchone()[0]
    assert marked == 1


# ── settling it ──────────────────────────────────────────────────────


def test_choosing_one_rejects_the_others_and_ends_the_ambiguity(split) -> None:
    conn, fire, futures = split
    result = glossary.choose_meaning(
        "FCC", glossary.ChoosePayload(entry_id=futures), ANON, conn=conn
    )

    assert result["chosen"] == futures
    assert result["rejected"] == [fire]
    # An explicit comprehension, not `dict(rows)`. A `sqlite3.Row` behaved like a
    # 2-tuple there, so `dict(rows)` built {id: status}; a row is a dict subclass
    # now and iterates its KEYS, so `dict(rows)` silently yields {"id": "status"}
    # -- one entry, no error, and every assertion below fails for the wrong reason.
    status = {
        r["id"]: r["status"]
        for r in conn.execute(
            "SELECT id, status FROM acronym_definitions WHERE normalized='fcc'"
        ).fetchall()
    }
    assert status[futures] == "active"
    assert status[fire] == "rejected"
    # Gone, so the annotator starts marking the surface again -- which is the
    # entire practical benefit of settling it.
    assert (
        conn.execute("SELECT COUNT(*) FROM ambiguous_surfaces WHERE normalized='fcc'").fetchone()[0]
        == 0
    )


def test_the_verdict_is_durable(split) -> None:
    """Every row is stamped `decided_at`, on both sides of the decision.

    `store_definition` defers to that stamp, so the next nightly run cannot
    quietly reinstate the reading a curator just discarded -- which is the
    failure `decided_at` was added for in the first place.
    """
    conn, fire, futures = split
    glossary.choose_meaning("FCC", glossary.ChoosePayload(entry_id=futures), ANON, conn=conn)

    stamps = {
        r["id"]: r["decided_at"]
        for r in conn.execute(
            "SELECT id, decided_at FROM acronym_definitions WHERE normalized='fcc'"
        )
    }
    assert all(stamps.values()), stamps

    # ...and re-grounding the rejected reading does not bring it back.
    _meaning(conn, "FCC", "Fire Control Center", "https://elsewhere.example/fcc")
    assert (
        conn.execute("SELECT status FROM acronym_definitions WHERE id=?", (fire,)).fetchone()[0]
        == "rejected"
    )


def test_the_losing_reading_keeps_its_evidence(split) -> None:
    """Rejected, not deleted. Each was grounded in a real sentence on a real
    page, so "why is FCC not a fire control center here" still has an answer --
    and a later re-lookup does not pay to rediscover a ruled-out reading."""
    conn, fire, futures = split
    glossary.choose_meaning("FCC", glossary.ChoosePayload(entry_id=futures), ANON, conn=conn)

    row = conn.execute(
        "SELECT expansion, grounding_quote, grounding_url FROM acronym_definitions WHERE id=?",
        (fire,),
    ).fetchone()
    assert row["expansion"] == "Fire Control Center"
    assert row["grounding_quote"] and row["grounding_url"]


@pytest.mark.negative
def test_choosing_a_meaning_of_a_different_acronym_is_refused(split) -> None:
    """Without the check a wrong id would reject every reading of this surface
    AND activate a row belonging to another acronym entirely."""
    conn, _fire, _futures = split
    other = _meaning(conn, "MOSA", "Modular Open Systems Approach", "https://example.mil/mosa")

    with pytest.raises(HTTPException) as raised:
        glossary.choose_meaning("FCC", glossary.ChoosePayload(entry_id=other), ANON, conn=conn)
    assert raised.value.status_code == 400

    # Nothing moved.
    assert (
        conn.execute("SELECT status FROM acronym_definitions WHERE id=?", (other,)).fetchone()[0]
        == "active"
    )
    assert {
        r["status"]
        for r in conn.execute("SELECT status FROM acronym_definitions WHERE normalized='fcc'")
    } == {"ambiguous"}


@pytest.mark.negative
def test_a_surface_with_nothing_grounded_cannot_be_settled(empty: db.Conn) -> None:
    with pytest.raises(HTTPException) as raised:
        glossary.choose_meaning(
            "NOPE", glossary.ChoosePayload(entry_id="gloss:nope"), ANON, conn=empty
        )
    assert raised.value.status_code == 404
