"""Three situations, three verdicts — the queue could express only one.

`choose` says *these are different things and this one is right here*, so the
others are wrong. That is one of three real cases:

  * **different, and all real.** `CPE` is Capability Program Executive AND
    customer premise equipment AND Common Platform Enumeration. Keeping them is
    already the DEFAULT and already works -- the card lists them, `pick_sense`
    resolves per brief. What was missing was recording that a person had
    decided, so a reviewed surface never left the queue: `decided_at` was NULL
    on all 26 ambiguous surfaces in the corpus.
  * **one thing, spelled several ways.** `FY` is "Fiscal Year", "Fiscal Years"
    and "In fiscal year". Over half this corpus's ambiguity is this, and
    `choose` handles it badly: the duplicates are not WRONG, they are the same
    claim, and their evidence belongs to the survivor.
  * different, one right -- which `choose` already did.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException
from helpers import ANON, CURATOR_EMAIL, CURATOR_ID, NOW, SIGNED, curator
from venona.api.routers import glossary
from venona.db import core as db
from venona.pipeline.acronyms import WEB_BASE_CONFIDENCE, store_definition


def _reading(conn: db.Conn, surface: str, expansion: str, url: str) -> str:
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


def _rows(conn: db.Conn, normalized: str) -> dict[str, db.Row]:
    return {
        r["expansion"]: r
        for r in conn.execute(
            "SELECT * FROM acronym_definitions WHERE normalized = ?", (normalized,)
        )
    }


@pytest.fixture
def split(conn: db.Conn) -> db.Conn:
    conn.execute("DELETE FROM acronym_definitions")
    conn.execute("DELETE FROM ambiguous_surfaces")
    _reading(conn, "CPE", "Capability Program Executive", "https://army.example/cpe")
    _reading(conn, "CPE", "customer premise equipment", "https://telco.example/cpe")
    conn.commit()
    return conn


@pytest.fixture
def spellings(conn: db.Conn) -> db.Conn:
    """One meaning, three spellings — the majority case in this corpus."""
    conn.execute("DELETE FROM acronym_definitions")
    conn.execute("DELETE FROM ambiguous_surfaces")
    _reading(conn, "FY", "Fiscal Year", "https://a.example/fy")
    _reading(conn, "FY", "Fiscal Years", "https://b.example/fy")
    _reading(conn, "FY", "In fiscal year", "https://c.example/fy")
    conn.commit()
    return conn


# ── keep both ────────────────────────────────────────────────────────


def test_keeping_both_records_the_decision_without_changing_the_readings(
    split: db.Conn,
) -> None:
    glossary.keep_both("CPE", ANON, conn=split)

    rows = _rows(split, "cpe")
    assert len(rows) == 2
    for row in rows.values():
        assert row["status"] == "ambiguous", "both survive"
        assert row["decided_at"], "and the verdict is on the record"


def test_the_surface_stays_ambiguous_on_purpose(split: db.Conn) -> None:
    """That is what makes CPE render as one meaning in one brief and the other
    in another, instead of picking and being wrong half the time."""
    glossary.keep_both("CPE", ANON, conn=split)

    assert (
        split.execute("SELECT COUNT(*) FROM ambiguous_surfaces WHERE normalized='cpe'").fetchone()[
            0
        ]
        == 1
    )


def test_a_kept_surface_stops_counting_as_pending(split: db.Conn) -> None:
    """`GET /curation/status` counts PENDING work. Without `decided_at IS NULL`
    "keep both" would be a verdict that leaves the number it was meant to clear
    exactly where it was."""
    pending = (
        "SELECT COUNT(*) FROM acronym_definitions  WHERE status='ambiguous' AND decided_at IS NULL"
    )
    assert split.execute(pending).fetchone()[0] == 2

    glossary.keep_both("CPE", ANON, conn=split)
    assert split.execute(pending).fetchone()[0] == 0


@pytest.mark.negative
def test_keeping_both_needs_two_readings(conn: db.Conn) -> None:
    conn.execute("DELETE FROM acronym_definitions")
    _reading(conn, "MOSA", "Modular Open Systems Approach", "https://a.example/mosa")
    with pytest.raises(HTTPException) as raised:
        glossary.keep_both("MOSA", ANON, conn=conn)
    assert raised.value.status_code == 400


def test_keeping_both_reverses_an_earlier_pick(split: db.Conn) -> None:
    """Coming from a settled state, this is a REVERSAL. Half-applying it would
    leave the surface annotating with the reading the curator just said was not
    the only one."""
    winner = _rows(split, "cpe")["Capability Program Executive"]["id"]
    glossary.choose_meaning("CPE", glossary.ChoosePayload(entry_id=winner), ANON, conn=split)
    assert _rows(split, "cpe")["customer premise equipment"]["status"] == "rejected"

    glossary.keep_both("CPE", ANON, conn=split)
    assert {r["status"] for r in _rows(split, "cpe").values()} == {"ambiguous"}


# ── same thing ───────────────────────────────────────────────────────


def test_collapsing_folds_the_duplicates_evidence_into_the_survivor(
    spellings: db.Conn,
) -> None:
    """The difference from `choose`. The duplicates are not wrong -- they are the
    same claim from other documents, which is exactly what corroboration means.
    """
    survivor = _rows(spellings, "fy")["Fiscal Year"]["id"]
    assert _rows(spellings, "fy")["Fiscal Year"]["corroboration_count"] == 1

    result = glossary.same_thing(
        "FY", glossary.SameThingPayload(entry_id=survivor), ANON, conn=spellings
    )

    assert result["kept"] == "Fiscal Year"
    assert sorted(result["collapsed"]) == ["Fiscal Years", "In fiscal year"]
    row = _rows(spellings, "fy")["Fiscal Year"]
    assert row["status"] == "active"
    assert row["corroboration_count"] == 3, "three documents, one meaning"
    assert row["confidence"] > WEB_BASE_CONFIDENCE


def test_the_survivor_keeps_its_own_provenance(spellings: db.Conn) -> None:
    """Three of the four tier-3 checks are about the quote containing the
    expansion, so a sentence spelling "Fiscal Years" is not evidence for the
    string "Fiscal Year". Sources are shared; quotes are not."""
    survivor = _rows(spellings, "fy")["Fiscal Year"]["id"]
    glossary.same_thing("FY", glossary.SameThingPayload(entry_id=survivor), ANON, conn=spellings)

    row = _rows(spellings, "fy")["Fiscal Year"]
    assert "Fiscal Year (FY)" in row["grounding_quote"]
    assert row["grounding_url"] == "https://a.example/fy"


def test_collapsing_ends_the_ambiguity(spellings: db.Conn) -> None:
    """One meaning stands, so the annotator can mark the surface again without
    asking the context."""
    survivor = _rows(spellings, "fy")["Fiscal Year"]["id"]
    glossary.same_thing("FY", glossary.SameThingPayload(entry_id=survivor), ANON, conn=spellings)

    assert (
        spellings.execute(
            "SELECT COUNT(*) FROM ambiguous_surfaces WHERE normalized='fy'"
        ).fetchone()[0]
        == 0
    )
    losers = [r for e, r in _rows(spellings, "fy").items() if e != "Fiscal Year"]
    assert all(r["status"] == "rejected" and r["decided_at"] for r in losers)


@pytest.mark.negative
def test_collapsing_into_another_surfaces_reading_is_refused(
    spellings: db.Conn,
) -> None:
    other = _reading(spellings, "MOSA", "Modular Open Systems Approach", "https://x.example/m")
    with pytest.raises(HTTPException) as raised:
        glossary.same_thing("FY", glossary.SameThingPayload(entry_id=other), ANON, conn=spellings)
    assert raised.value.status_code == 400


@pytest.mark.negative
def test_the_same_document_twice_is_still_one_source(conn: db.Conn) -> None:
    """Two spellings grounded from the SAME page are one piece of evidence. The
    union is over source keys precisely so folding cannot manufacture
    confidence."""
    conn.execute("DELETE FROM acronym_definitions")
    conn.execute("DELETE FROM ambiguous_surfaces")
    _reading(conn, "FY", "Fiscal Year", "https://same.example/fy")
    _reading(conn, "FY", "Fiscal Years", "https://same.example/fy")
    conn.commit()

    survivor = _rows(conn, "fy")["Fiscal Year"]["id"]
    result = glossary.same_thing(
        "FY", glossary.SameThingPayload(entry_id=survivor), ANON, conn=conn
    )

    assert result["sources"] == 1
    assert _rows(conn, "fy")["Fiscal Year"]["confidence"] == WEB_BASE_CONFIDENCE


# ── reopen ───────────────────────────────────────────────────────────


def test_reopen_takes_back_a_keep_both(split: db.Conn) -> None:
    glossary.keep_both("CPE", ANON, conn=split)
    glossary.reopen("CPE", ANON, conn=split)

    rows = _rows(split, "cpe")
    assert all(r["decided_at"] is None for r in rows.values())
    assert all(r["status"] == "ambiguous" for r in rows.values())


def test_reopen_takes_back_a_choose(split: db.Conn) -> None:
    winner = _rows(split, "cpe")["Capability Program Executive"]["id"]
    glossary.choose_meaning("CPE", glossary.ChoosePayload(entry_id=winner), ANON, conn=split)
    glossary.reopen("CPE", ANON, conn=split)

    rows = _rows(split, "cpe")
    assert {r["status"] for r in rows.values()} == {"ambiguous"}
    assert all(r["decided_at"] is None for r in rows.values())
    # ...and the surface is disambiguating again.
    assert (
        split.execute("SELECT COUNT(*) FROM ambiguous_surfaces WHERE normalized='cpe'").fetchone()[
            0
        ]
        == 1
    )


@pytest.mark.negative
def test_reopen_refuses_a_surface_attached_to_an_entity(split: db.Conn) -> None:
    """That wrote an alias and promoted an entity. Unpicking it is a different
    operation with different consequences, so this refuses rather than
    half-undoing it."""
    split.execute("UPDATE acronym_definitions SET entity_id='company:x' WHERE normalized='cpe'")
    split.execute("UPDATE acronym_definitions SET decided_at=? WHERE normalized='cpe'", (NOW,))
    split.commit()

    with pytest.raises(HTTPException) as raised:
        glossary.reopen("CPE", ANON, conn=split)
    assert raised.value.status_code == 409


@pytest.mark.negative
def test_reopen_refuses_a_surface_called_not_an_acronym(split: db.Conn) -> None:
    """That verdict also wrote a stoplist line into `profile/lexicon.yaml`.

    Reopening clears the rejection and nothing else, so the readings would come
    back on screen while `detect` stayed blind to the token -- the half-undo the
    attached-entity branch refuses. Rejecting EVERY reading is what that verdict
    looks like: `choose` and `same_thing` each leave one standing.
    """
    split.execute(
        "UPDATE acronym_definitions SET status='rejected', decided_at=? WHERE normalized='cpe'",
        (NOW,),
    )
    split.commit()

    with pytest.raises(HTTPException) as raised:
        glossary.reopen("CPE", ANON, conn=split)
    assert raised.value.status_code == 409
    assert "stoplist" in raised.value.detail


def test_reopen_still_takes_back_a_verdict_that_left_one_reading(split: db.Conn) -> None:
    """The guard above keys on ALL readings being rejected, which is what keeps
    it from swallowing `choose` and `same_thing`."""
    split.execute(
        "UPDATE acronym_definitions SET status='rejected', decided_at=? WHERE normalized='cpe'",
        (NOW,),
    )
    split.execute(
        "UPDATE acronym_definitions SET status='active' WHERE id = "
        "(SELECT id FROM acronym_definitions WHERE normalized='cpe' LIMIT 1)"
    )
    split.commit()

    glossary.reopen("CPE", ANON, conn=split)


@pytest.mark.negative
def test_reopen_needs_something_to_reopen(split: db.Conn) -> None:
    with pytest.raises(HTTPException) as raised:
        glossary.reopen("CPE", ANON, conn=split)
    assert raised.value.status_code == 400


# ── who decided ──────────────────────────────────────────────────────


class TestTheVerdictRecordsWhoMadeIt:
    """`acronym_definitions.decided_by` has existed since migration 0002 and was
    written by nothing.

    `decided_at` said a person had ruled and there was no way to ask which one --
    on the queue where two curators reaching opposite verdicts about the same
    surface is an ordinary event, and where the reversal (`reopen`) leaves no
    trace of who had decided in the first place.

    Same shape as `test_curation_api.py::TestTheVerdictRecordsWhoMadeIt`, because
    it is the same path: `actor_id(request)` -> a plain `actor: str | None` -> the
    UPDATE -> a LEFT JOIN back to an email -> a schema field -> the screen.
    """

    def test_keep_both_records_the_actor_and_serves_the_email(self, split: db.Conn) -> None:
        curator(split)
        result = glossary.keep_both("CPE", SIGNED, conn=split)

        assert {r["decided_by"] for r in _rows(split, "cpe").values()} == {CURATOR_ID}
        assert {e["decided_by_email"] for e in result["entries"]} == {CURATOR_EMAIL}

    def test_choose_signs_the_winner_and_the_rejected_alike(self, split: db.Conn) -> None:
        """Both halves of one decision. Signing only the winner would leave the
        rejections looking like the pipeline's work."""
        curator(split)
        winner = _rows(split, "cpe")["Capability Program Executive"]["id"]
        glossary.choose_meaning("CPE", glossary.ChoosePayload(entry_id=winner), SIGNED, conn=split)

        rows = _rows(split, "cpe").values()
        assert {r["status"] for r in rows} == {"active", "rejected"}
        assert {r["decided_by"] for r in rows} == {CURATOR_ID}

    def test_same_thing_signs_the_survivor_and_the_folded_away(self, spellings: db.Conn) -> None:
        curator(spellings)
        survivor = _rows(spellings, "fy")["Fiscal Year"]["id"]
        glossary.same_thing(
            "FY", glossary.SameThingPayload(entry_id=survivor), SIGNED, conn=spellings
        )

        assert {r["decided_by"] for r in _rows(spellings, "fy").values()} == {CURATOR_ID}

    def test_reopening_erases_the_name_rather_than_recording_the_reopener(
        self, split: db.Conn
    ) -> None:
        """⭐ The one that is not a copy of the others.

        Reopening is the verdict whose product is the ABSENCE of one -- it clears
        `decided_at` so the next pipeline pass stops deferring to a ruling nobody
        stands behind. Writing the reopener into `decided_by` would leave a row
        that reads as decided by them, which is the opposite of what they did.
        """
        curator(split)
        glossary.keep_both("CPE", SIGNED, conn=split)
        glossary.reopen("CPE", SIGNED, conn=split)

        rows = _rows(split, "cpe").values()
        assert {r["decided_at"] for r in rows} == {None}
        assert {r["decided_by"] for r in rows} == {None}

    def test_an_unsigned_verdict_records_null_rather_than_failing(self, split: db.Conn) -> None:
        """The CLI and the pipeline have no principal. Refusing to record a
        verdict because attribution failed would trade a working feature for a
        column -- and NULL here is the honest record of "shell access decided"."""
        glossary.keep_both("CPE", ANON, conn=split)

        rows = _rows(split, "cpe").values()
        assert {r["decided_at"] for r in rows} != {None}, "the verdict itself must land"
        assert {r["decided_by"] for r in rows} == {None}
