"""Entity merges.

The read side is tested harder than the write side on purpose. Writing a
mapping row is trivial; the failure that mattered was that a merge would have
been **invisible in the UI and a regression in annotation** — visible in Neo4j,
absent from every hover card, and it would have deleted the loser's aliases from
the alias index entirely.
"""

from __future__ import annotations

import threading

import psycopg
import pytest
from helpers import NOW, entity
from venona.db import core as db
from venona.services import entity_service
from venona.services.curate import MergeError, list_merges, merge_entities, resolve_entity_id
from venona.services.curate import unmerge as do_unmerge


def _alias(conn: db.Conn, entity_id: str, surface: str) -> None:
    db.upsert(
        conn,
        "aliases",
        {
            "id": f"{entity_id}|{surface}",
            "entity_id": entity_id,
            "surface": surface,
            "normalized": surface.lower(),
            "strictness": 2,
            "is_acronym": int(surface.isupper()),
            "added_by": "seed",
            "created_at": NOW,
        },
        key=["id"],
    )


@pytest.fixture
def pair(conn: db.Conn) -> db.Conn:
    """The real duplicate from the live corpus."""
    entity(conn, "company:mda", "MDA")
    entity(conn, "company:missile-defense-agency", "Missile Defense Agency")
    _alias(conn, "company:mda", "MDA")
    _alias(conn, "company:missile-defense-agency", "Missile Defense Agency")
    conn.commit()
    return conn


# ── the write side ───────────────────────────────────────────────────


def test_a_merge_is_a_row_not_an_edit(pair: db.Conn) -> None:
    """Invariant 1: assertions are append-only. A merge that rewrote them would
    make `venona graph rebuild` lossy and unmerge impossible."""
    before = pair.execute("SELECT COUNT(*) AS n FROM assertions").fetchone()["n"]

    merge_entities(
        pair,
        from_id="company:mda",
        to_id="company:missile-defense-agency",
        merge_id="merge:1",
        reason="same agency",
        now=NOW,
    )

    assert pair.execute("SELECT COUNT(*) AS n FROM assertions").fetchone()["n"] == before
    assert resolve_entity_id(pair, "company:mda") == "company:missile-defense-agency"


def test_the_loser_becomes_merged_not_rejected(pair: db.Conn) -> None:
    """`rejected` means the model hallucinated it. `merged` means it is a real
    duplicate. Conflating them loses the distinction in every later query — and
    `rejected` would still let `promote_provisionals` see it."""
    merge_entities(
        pair,
        from_id="company:mda",
        to_id="company:missile-defense-agency",
        merge_id="merge:1",
        reason="same agency",
        now=NOW,
    )
    status = pair.execute("SELECT status FROM entities WHERE id = 'company:mda'").fetchone()[
        "status"
    ]
    assert status == "merged"


def test_two_provisionals_can_be_merged(pair: db.Conn) -> None:
    """The COMMON case, and an earlier version of this refused it.

    196 of 197 provisionals sit at (1 article, 1 source), so nearly every real
    duplicate is two uncorroborated entities. Requiring an active survivor made
    merging impossible exactly where it was most needed.
    """
    pair.execute("UPDATE entities SET status='provisional'")
    pair.commit()

    merge_entities(
        pair,
        from_id="company:mda",
        to_id="company:missile-defense-agency",
        merge_id="merge:1",
        reason="same agency",
        now=NOW,
    )
    assert resolve_entity_id(pair, "company:mda") == "company:missile-defense-agency"


@pytest.mark.negative
def test_merging_into_a_merged_entity_follows_the_chain(pair: db.Conn) -> None:
    """A tombstone is a signpost, not a wall.

    This used to be refused, and the refusal is what made the merge queue
    offer decisions it would not carry out: a conflict filed before a merge
    still names the loser, every read path resolves that id, and so the card
    on screen showed the SURVIVOR while the button posted the tombstone. The
    curator merged what they could see and got
    `survivor 'company:army' is merged` -- naming an id that had never been on
    screen.

    A merged id has no independent existence, so the request can only mean the
    survivor.
    """
    entity(pair, "company:third", "Third Agency")
    pair.commit()
    merge_entities(
        pair,
        from_id="company:missile-defense-agency",
        to_id="company:third",
        merge_id="merge:1",
        reason="x",
        now=NOW,
    )

    # `company:missile-defense-agency` is now a tombstone pointing at `company:third`.
    merge_entities(
        pair,
        from_id="company:mda",
        to_id="company:missile-defense-agency",
        merge_id="merge:2",
        reason="x",
        now=NOW,
    )

    assert resolve_entity_id(pair, "company:mda") == "company:third"
    assert resolve_entity_id(pair, "company:missile-defense-agency") == "company:third"


def test_merging_into_a_rejected_entity_is_still_refused(pair: db.Conn) -> None:
    """The case the old refusal was really protecting.

    `rejected` is a person's verdict that the model hallucinated an entity.
    Following a chain into one would quietly reinstate something someone threw
    away, which a tombstone never does.
    """
    pair.execute(
        "UPDATE entities SET status = 'rejected' WHERE id = 'company:missile-defense-agency'"
    )
    pair.commit()

    with pytest.raises(MergeError, match="rejected"):
        merge_entities(
            pair,
            from_id="company:mda",
            to_id="company:missile-defense-agency",
            merge_id="merge:1",
            reason="x",
            now=NOW,
        )


def test_a_refusal_names_the_entity_not_just_the_id(pair: db.Conn) -> None:
    """Every id in the refusal that prompted this work was one nobody had seen.

    A curator reads names off cards; an error that answers in ids is an error
    they cannot act on.
    """
    merge_entities(
        pair,
        from_id="company:mda",
        to_id="company:missile-defense-agency",
        merge_id="merge:1",
        reason="x",
        now=NOW,
    )

    with pytest.raises(MergeError) as caught:
        merge_entities(
            pair,
            from_id="company:mda",
            to_id="company:missile-defense-agency",
            merge_id="merge:2",
            reason="x",
            now=NOW,
        )
    assert "Missile Defense Agency" in str(caught.value)
    assert "already the same entity" in str(caught.value)


def test_a_merge_consolidates_evidence_across_the_closure(conn: db.Conn) -> None:
    """The point of merging two provisionals.

    `assertions` are never rewritten (invariant 1), so the survivor keeps
    carrying only its OWN assertions. Unless promotion aggregates over the merge
    closure, merging two single-article entities leaves both below the gate and
    the merge achieves nothing at all.
    """
    from venona.pipeline.resolve import promote_provisionals

    entity(conn, "company:aaa", "Agency AAA", status="provisional")
    entity(conn, "company:bbb", "Agency BBB", status="provisional")

    for i, (entity_id, domain) in enumerate(
        [("company:aaa", "breakingdefense.com"), ("company:bbb", "defensenews.com")]
    ):
        db.upsert(
            conn,
            "documents",
            {
                "id": f"doc:m{i}",
                "source_id": "breaking_defense",
                "site_domain": domain,
                "url": f"https://{domain}/{i}",
                "url_key": f"{domain}/{i}",
                "title": "T",
                "text_nfc": "body",
                "text_chars": 4,
                "content_hash": f"mh{i}",
                "cluster_id": f"cluster:m{i}",
                "fetched_at": NOW,
                "created_at": NOW,
            },
            key=["id"],
        )
        db.insert(
            conn,
            "assertions",
            {
                "id": f"assert:m{i}",
                "run_id": "run:test",
                "article_id": f"doc:m{i}",
                "cluster_id": f"cluster:m{i}",
                "kind": "entity",
                "surface": "Agency",
                "entity_id_raw": entity_id,
                "evidence_quote": "Agency did a thing.",
                "confidence": 0.9,
                "method": "provisional",
                "created_at": NOW,
            },
        )
    conn.commit()

    # Apart, neither has corroboration: one article, one publisher each.
    promote_provisionals(conn, now=NOW)
    assert conn.execute("SELECT status FROM entities WHERE id='company:bbb'").fetchone()[0] == (
        "provisional"
    )

    merge_entities(
        conn, from_id="company:aaa", to_id="company:bbb", merge_id="merge:1", reason="dupe", now=NOW
    )
    conn.commit()
    promote_provisionals(conn, now=NOW)

    survivor = conn.execute("SELECT status FROM entities WHERE id='company:bbb'").fetchone()[0]
    assert survivor == "active", "the merged pair now has two articles from two publishers"


@pytest.mark.negative
def test_a_cycle_is_refused(pair: db.Conn) -> None:
    merge_entities(
        pair,
        from_id="company:mda",
        to_id="company:missile-defense-agency",
        merge_id="merge:1",
        reason="x",
        now=NOW,
    )
    pair.execute("UPDATE entities SET status='active' WHERE id='company:mda'")
    pair.commit()

    # Refused either way. Chain resolution now catches this one step earlier
    # and says something more useful than "cycle": both ids resolve to the same
    # entity, because the mapping written above is still active. The cycle
    # guard stays for data where it is not -- which only a hand edit produces.
    with pytest.raises(MergeError, match="already the same entity|cycle"):
        merge_entities(
            pair,
            from_id="company:missile-defense-agency",
            to_id="company:mda",
            merge_id="merge:2",
            reason="x",
            now=NOW,
        )


@pytest.mark.negative
def test_self_merge_is_refused(pair: db.Conn) -> None:
    with pytest.raises(MergeError, match="itself"):
        merge_entities(
            pair, from_id="company:mda", to_id="company:mda", merge_id="m", reason="x", now=NOW
        )


def test_unmerge_restores(pair: db.Conn) -> None:
    merge_entities(
        pair,
        from_id="company:mda",
        to_id="company:missile-defense-agency",
        merge_id="merge:1",
        reason="x",
        now=NOW,
    )
    do_unmerge(pair, from_id="company:mda", merge_id="merge:1", now=NOW)

    assert resolve_entity_id(pair, "company:mda") == "company:mda"
    # The row survives, deactivated, as a record of what was done and undone.
    assert len(list_merges(pair, active_only=False)) == 1
    assert list_merges(pair, active_only=True) == []


def test_a_failed_unmerge_leaves_nothing_half_applied(pair: db.Conn) -> None:
    """Undo is three UPDATEs, and it used to run them in AUTOCOMMIT.

    `db.connect` sets `isolation_level=None`, so each statement was its own
    implicit transaction and the trailing `conn.commit()` committed nothing. A
    failure partway therefore left the mapping deactivated with the entity still
    `merged` -- an entity that resolves to itself while carrying a merged status,
    which no read path expects and only a hand edit could produce otherwise.

    Undo is the recovery path for a misclick in a fast keyboard queue, so a
    half-applied undo is the one state nobody can reason about.
    """
    _conflict(pair, "mda|Organization|company:mda", "company:missile-defense-agency", "company:mda")
    merge_entities(
        pair,
        from_id="company:mda",
        to_id="company:missile-defense-agency",
        merge_id="merge:1",
        reason="x",
        now=NOW,
    )

    # Fail on the LAST of the three statements -- the conflict reopen -- which
    # is the one with two applied statements already behind it. A trigger rather
    # than a patched `execute`, because it fails the statement the way the
    # database really would.
    #
    # Postgres has no inline `RAISE(ABORT, ...)` as SQLite did, so the trigger
    # needs a plpgsql function behind it. The dollar-quoted body is safe through
    # `db.Conn.execute` because no parameters are passed, which is the one case
    # `_convert` returns verbatim.
    pair.execute(
        """
        CREATE FUNCTION boom() RETURNS trigger AS $boom$
        BEGIN RAISE EXCEPTION 'disk I/O error'; END
        $boom$ LANGUAGE plpgsql
        """
    )
    pair.execute(
        "CREATE TRIGGER boom BEFORE UPDATE ON alias_conflicts FOR EACH ROW EXECUTE FUNCTION boom()"
    )
    # `RAISE EXCEPTION` surfaces as `RaiseException`, an InternalError -- not the
    # IntegrityError SQLite's ABORT produced. The claim under test is that the
    # unmerge fails and leaves nothing half-applied, not which class it fails as.
    with pytest.raises(psycopg.Error):
        do_unmerge(pair, from_id="company:mda", merge_id="merge:1", now=NOW)
    pair.execute("DROP TRIGGER boom ON alias_conflicts")
    pair.execute("DROP FUNCTION boom()")

    # Nothing moved: the merge is still whole, so undo can be retried.
    assert resolve_entity_id(pair, "company:mda") == "company:missile-defense-agency"
    assert len(list_merges(pair, active_only=True)) == 1
    status = pair.execute("SELECT status FROM entities WHERE id = 'company:mda'").fetchone()[0]
    assert status == "merged"


# ── the read side: the part that would have been a regression ────────


def test_a_card_request_for_the_loser_returns_the_survivor(pair: db.Conn) -> None:
    """Only the projector resolved through mappings before this. A merge would
    have shown in Neo4j and nowhere a human looks — and historical
    `brief_blocks.spans` store raw ids, so they point at the loser forever."""
    from venona.services.entity_service import entity_card

    merge_entities(
        pair,
        from_id="company:mda",
        to_id="company:missile-defense-agency",
        merge_id="merge:1",
        reason="x",
        now=NOW,
    )

    card = entity_card(pair, "company:mda")
    assert card is not None, "the loser's id must still resolve to a card"
    assert card.id == "company:missile-defense-agency"
    assert card.name == "Missile Defense Agency"


def test_the_losers_aliases_keep_matching(pair: db.Conn) -> None:
    """The regression that would have made merging worse than useless.

    `alias_index` joined `entities WHERE status='active'`. Demote the loser and
    "MDA" stops matching entirely — so the merge would DELETE the very surface
    it was meant to unify.
    """
    from venona.services.alias_index import build_index

    merge_entities(
        pair,
        from_id="company:mda",
        to_id="company:missile-defense-agency",
        merge_id="merge:1",
        reason="x",
        now=NOW,
    )
    pair.commit()

    # Through the public matcher, because that is what annotation actually uses.
    index = build_index(pair)
    matches = index.find("The MDA awarded a contract.")
    refs = {m.ref for m in matches}

    assert refs, "'MDA' must still match after the merge"
    assert "company:missile-defense-agency" in refs, "and must point at the survivor"
    assert "company:mda" not in refs, "never at the merged-away id"


@pytest.mark.negative
def test_a_merged_entity_is_never_re_promoted(pair: db.Conn) -> None:
    """`promote_provisionals` re-evaluates every run. Without the status filter
    it would flip a merged entity back to `active` and resurrect it into the
    projection after a human had merged it away."""
    from venona.pipeline.resolve import promote_provisionals

    merge_entities(
        pair,
        from_id="company:mda",
        to_id="company:missile-defense-agency",
        merge_id="merge:1",
        reason="x",
        now=NOW,
    )
    pair.commit()

    promote_provisionals(pair, now=NOW)

    status = pair.execute("SELECT status FROM entities WHERE id = 'company:mda'").fetchone()[
        "status"
    ]
    assert status == "merged"


# ── the curate stage classifier ──────────────────────────────────────


@pytest.mark.parametrize(
    ("surface", "a", "b", "expected"),
    [
        # One thing, named two ways. The acronym generates from the long form.
        ("MDA", "MDA", "Missile Defense Agency", "duplicate"),
        ("STR", "STR", "Systems & Technology Research", "duplicate"),
        # One thing, SPELLED two ways -- both spellings generate the acronym.
        (
            "NGCSM",
            "Next Generation C-sUAS Missile",
            "Next Generation Counter-sUAS Missile",
            "duplicate",
        ),
        ("MGPE", "Maj. Gen. Partick Ellis", "Maj. Gen. Patrick Ellis", "duplicate"),
        # A hyphen and a trailing acronym gloss are SPELLING. Raw, this pair
        # scored 77.9 and was filed as a collision -- the same program against
        # itself, twice (NGC2 and NGCC), on the live corpus. Comparing
        # `_comparable()` forms scores it 100.
        (
            "NGC2",
            "Next Generation Command and Control",
            "Next-Generation Command and Control (NGC2)",
            "duplicate",
        ),
        (
            "CSIS",
            "Center for Strategic and International Studies (CSIS)",
            "Center for Strategic and International Studies",
            "duplicate",
        ),
        # One acronym, two GENUINELY different bodies. Merging these would be
        # an unrecoverable error, so the order of the checks matters.
        ("DIA", "Defense Intelligence Agency", "Defense Investment Agency", "collision"),
        ("ACS", "Alpine Combat Systems", "Allen Control Systems", "collision"),
        ("UAF", "Ukrainian Air Force", "the U.S. Air Force", "collision"),
    ],
)
def test_conflicts_are_classified(
    conn: db.Conn, surface: str, a: str, b: str, expected: str
) -> None:
    """These need DIFFERENT actions — merge, or record as permanently ambiguous
    — so a queue that cannot tell them apart makes a human re-diagnose every
    row. All seven cases are live rows from the real corpus.
    """
    from venona.pipeline.curate import _classify

    entity(conn, "company:aa", a)
    entity(conn, "company:bb", b)
    conn.commit()

    row = {
        "id": "x",
        "surface": surface,
        "existing_entity_id": "company:aa",
        "proposed_entity_id": "company:bb",
    }
    assert _classify(conn, row) == expected


def test_a_resolver_filed_containment_keeps_its_kind(conn: db.Conn) -> None:
    """The resolver files these itself when it declines a token-subset match, so
    they never need re-inferring."""
    from venona.pipeline.curate import _classify

    assert (
        _classify(conn, {"id": "contain|company:a|company:b", "surface": "Amazon"}) == "containment"
    )


# ── the review queue has an exit ─────────────────────────────────────


def _conflict(
    conn: db.Conn,
    conflict_id: str,
    existing: str,
    proposed: str,
    *,
    surface: str = "MDA",
) -> None:
    db.upsert(
        conn,
        "alias_conflicts",
        {
            "id": conflict_id,
            "surface": surface,
            "normalized": surface.lower(),
            "existing_entity_id": existing,
            "proposed_entity_id": proposed,
            "entity_type": "Company",
            "created_at": NOW,
        },
        key=["id"],
    )
    conn.commit()


def _conflict_row(conn: db.Conn, conflict_id: str) -> db.Row:
    return conn.execute("SELECT * FROM alias_conflicts WHERE id = ?", (conflict_id,)).fetchone()


def test_merging_closes_the_conflict_it_answers(pair: db.Conn) -> None:
    """`resolved` was never set to 1 anywhere, so the queue only ever grew.

    A merge IS the answer to the conflict that proposed it; making the user
    close it separately is how a queue with an exit still fills up.
    """
    _conflict(pair, "mda|Organization|company:mda", "company:missile-defense-agency", "company:mda")

    merge_entities(
        pair,
        from_id="company:mda",
        to_id="company:missile-defense-agency",
        merge_id="merge:1",
        reason="duplicate",
        now=NOW,
    )

    row = _conflict_row(pair, "mda|Organization|company:mda")
    assert row["resolved"] == 1
    assert row["resolution"] == "merged"
    assert row["resolved_at"] == NOW


def test_one_merge_reports_every_conflict_it_answered(pair: db.Conn) -> None:
    """The queue must not offer back a decision the curator just made.

    The resolver files a conflict per SURFACE, so one real pair routinely
    produces several rows. `prog:orca-xluuv` and its uncrewed spelling were
    filed twice on the live corpus -- once under the acronym-generated surface
    `OELUUV` and once under `OELU2V` -- and `prog:precision-strike-missile` /
    `...-prsm` the same way.

    The server always settled all of them. The CLIENT could not know which, so
    it dropped the row that was clicked and left the siblings on screen; the
    next click returned "already the same entity", which is accurate, useless,
    and reads as a bug in the merge rather than in the queue. Returning the ids
    is what lets the queue drop them.
    """
    _conflict(pair, "oeluuv", "company:missile-defense-agency", "company:mda", surface="OELUUV")
    _conflict(pair, "oelu2v", "company:missile-defense-agency", "company:mda", surface="OELU2V")

    settled = merge_entities(
        pair,
        from_id="company:mda",
        to_id="company:missile-defense-agency",
        merge_id="merge:1",
        reason="duplicate",
        now=NOW,
    )

    assert sorted(settled) == ["oelu2v", "oeluuv"], "both rows, not just the clicked one"
    assert all(_conflict_row(pair, cid)["resolved"] == 1 for cid in settled)


def test_a_merge_that_answered_nothing_reports_nothing(pair: db.Conn) -> None:
    """A merge made from the CLI, or from the entity page, has no queue behind
    it. An empty list is the honest answer and the client falls back to dropping
    the row it knows about."""
    settled = merge_entities(
        pair,
        from_id="company:mda",
        to_id="company:missile-defense-agency",
        merge_id="merge:1",
        reason="duplicate",
        now=NOW,
    )
    assert settled == []


def test_the_pair_is_matched_in_either_direction(pair: db.Conn) -> None:
    """`_add_alias` files (owner, newcomer) and `_contained` files (candidate,
    minted), so the same real pair reaches the queue both ways round."""
    _conflict(pair, "reversed", "company:mda", "company:missile-defense-agency")

    merge_entities(
        pair,
        from_id="company:mda",
        to_id="company:missile-defense-agency",
        merge_id="merge:1",
        reason="duplicate",
        now=NOW,
    )

    assert _conflict_row(pair, "reversed")["resolved"] == 1


def test_keeping_a_pair_apart_is_also_an_answer(pair: db.Conn) -> None:
    """Roughly 40% of live containment rows are correct distinctions -- Thales
    vs Thales Australia. With no way to say so they sat forever, indistinguishable
    from rows nobody had looked at."""
    from venona.services.curate import dismiss_conflict

    _conflict(pair, "contain|company:a|company:b", "company:mda", "company:missile-defense-agency")
    dismiss_conflict(pair, "contain|company:a|company:b", now=NOW)

    row = _conflict_row(pair, "contain|company:a|company:b")
    assert row["resolved"] == 1
    assert row["resolution"] == "kept_apart"
    # And the graph is untouched -- keeping apart is not a merge.
    assert not pair.execute("SELECT 1 FROM entity_mappings").fetchone()


@pytest.mark.negative
def test_dismissing_an_unknown_conflict_is_refused(pair: db.Conn) -> None:
    from venona.services.curate import dismiss_conflict

    with pytest.raises(MergeError):
        dismiss_conflict(pair, "no-such-conflict", now=NOW)


def test_unmerging_reopens_what_the_merge_closed(pair: db.Conn) -> None:
    """An unmerge says the pair was NOT one thing, which puts the original
    question back on the table. Leaving the row closed would hide a conflict
    nobody has actually adjudicated."""
    _conflict(pair, "mda|Organization|company:mda", "company:missile-defense-agency", "company:mda")
    merge_entities(
        pair,
        from_id="company:mda",
        to_id="company:missile-defense-agency",
        merge_id="merge:1",
        reason="duplicate",
        now=NOW,
    )
    do_unmerge(pair, from_id="company:mda", merge_id="merge:1", now=NOW)

    row = _conflict_row(pair, "mda|Organization|company:mda")
    assert row["resolved"] == 0
    assert row["resolution"] is None


def test_a_kept_apart_verdict_survives_an_unrelated_unmerge(pair: db.Conn) -> None:
    """`kept_apart` was never about the merge, so it must stand on its own."""
    from venona.services.curate import dismiss_conflict

    _conflict(pair, "kept", "company:mda", "company:missile-defense-agency")
    dismiss_conflict(pair, "kept", now=NOW)
    merge_entities(
        pair,
        from_id="company:mda",
        to_id="company:missile-defense-agency",
        merge_id="merge:1",
        reason="duplicate",
        now=NOW,
    )
    do_unmerge(pair, from_id="company:mda", merge_id="merge:1", now=NOW)

    assert _conflict_row(pair, "kept")["resolution"] == "kept_apart"


# ── auto-merge: provable identity only ───────────────────────────────


def _dup(conn: db.Conn, cid: str, a: str, b: str, surface: str) -> None:
    db.upsert(
        conn,
        "alias_conflicts",
        {
            "id": cid,
            "surface": surface,
            "normalized": surface.lower(),
            "existing_entity_id": a,
            "proposed_entity_id": b,
            "entity_type": "Company",
            "kind": "duplicate",
            "created_at": NOW,
        },
        key=["id"],
    )
    conn.commit()


def test_a_bare_acronym_merges_into_its_expansion(conn: db.Conn) -> None:
    """`DHS` <- `Department of Homeland Security`. Derivational, not fuzzy: the
    acronym is GENERATED from the expansion, which is why this is not the
    high-similarity merge CLAUDE.md forbids."""
    from venona.pipeline.curate import identical_pairs

    entity(conn, "company:dhs", "DHS")
    entity(conn, "company:department-of-homeland-security", "Department of Homeland Security")
    _dup(conn, "c1", "company:dhs", "company:department-of-homeland-security", "DHS")

    pairs = identical_pairs(conn)
    assert pairs == [("company:dhs", "company:department-of-homeland-security", "acronym of")], (
        "the expansion must survive so cards and graph nodes read properly"
    )


def test_a_parenthetical_gloss_is_not_a_different_entity(conn: db.Conn) -> None:
    """`Space Systems Command (SSC)` IS `Space Systems Command` — the same
    string once the gloss is normalised."""
    from venona.pipeline.curate import identical_pairs

    entity(conn, "company:ssc-gloss", "Space Systems Command (SSC)")
    entity(conn, "company:ssc", "Space Systems Command")
    _dup(conn, "c1", "company:ssc-gloss", "company:ssc", "SSC")

    loser, survivor, why = identical_pairs(conn)[0]
    assert (loser, survivor) == ("company:ssc-gloss", "company:ssc")
    assert why == "same name"


def test_a_gloss_with_a_lowercase_letter_in_it_is_still_a_gloss(conn: db.Conn) -> None:
    """`PrSM` is how the Army writes it, and an all-caps rule cannot see it.

    The cost is not one missed merge. `_comparable` is read by the duplicate
    detector, by `identical_pairs` and by `graph_health`'s own grouping, so a
    name that keeps its gloss takes a merge key nothing else can hold and the
    split goes unseen by all three at once.
    """
    from venona.pipeline.curate import identical_pairs

    entity(conn, "prog:prsm-glossed", "Precision Strike Missile (PrSM)")
    entity(conn, "prog:precision-strike-missile", "Precision Strike Missile")
    _dup(conn, "c1", "prog:prsm-glossed", "prog:precision-strike-missile", "PrSM")

    loser, survivor, why = identical_pairs(conn)[0]
    assert (loser, survivor) == ("prog:prsm-glossed", "prog:precision-strike-missile")
    assert why == "same name"


@pytest.mark.negative
@pytest.mark.parametrize(
    "name",
    [
        "Rafael (Israel)",
        "Tomahawk (Block IV)",
        "Patriot (Advanced Capability)",
        "Command and Control Fix (C2 Fix)",
    ],
)
def test_a_parenthetical_that_is_not_an_acronym_stays_part_of_the_name(name: str) -> None:
    """The half that protects against a false merge.

    Widening the gloss rule widens what compares EQUAL, and two names that
    should not be one entity is the expensive direction. A qualifier carrying
    one capital, or more than one token, is not an acronym gloss and is left
    where it is.
    """
    from venona.pipeline.curate import _comparable

    assert "(" in _comparable(name)


@pytest.mark.negative
def test_a_typo_is_not_provable_identity(conn: db.Conn) -> None:
    """`Maj. Gen. Partick Ellis` / `Patrick Ellis` is the one live duplicate that
    is similarity ONLY. It stays a human decision — that boundary is the whole
    argument for auto-merging the rest."""
    from venona.pipeline.curate import identical_pairs

    entity(conn, "person:a", "Maj. Gen. Partick Ellis")
    entity(conn, "person:b", "Maj. Gen. Patrick Ellis")
    _dup(conn, "c1", "person:a", "person:b", "MGPE")

    assert identical_pairs(conn) == []


@pytest.mark.negative
def test_a_collision_is_never_offered_for_merge(conn: db.Conn) -> None:
    """`DIA` = Defense **Intelligence** vs **Investment** Agency. `_classify`
    files it as a collision because BOTH names generate the acronym, so it never
    reaches this path — merging them would be unrecoverable."""
    from venona.pipeline.curate import identical_pairs

    entity(conn, "company:dia-intel", "Defense Intelligence Agency")
    entity(conn, "company:dia-invest", "Defense Investment Agency")
    _dup(conn, "c1", "company:dia-intel", "company:dia-invest", "DIA")
    conn.execute("UPDATE alias_conflicts SET kind='collision' WHERE id='c1'")
    conn.commit()

    assert identical_pairs(conn) == []


@pytest.mark.negative
def test_containment_is_never_auto_merged(conn: db.Conn) -> None:
    """`Thales` / `Thales Australia` is a parent/subsidiary distinction the
    resolver deliberately declined. Only `kind='duplicate'` is considered."""
    from venona.pipeline.curate import identical_pairs

    entity(conn, "company:thales", "Thales")
    entity(conn, "company:thales-au", "Thales Australia")
    _dup(
        conn,
        "contain|company:thales|company:thales-au",
        "company:thales",
        "company:thales-au",
        "Thales",
    )
    conn.execute("UPDATE alias_conflicts SET kind='containment'")
    conn.commit()

    assert identical_pairs(conn) == []


def test_a_settled_conflict_is_not_re_merged(conn: db.Conn) -> None:
    """Only open rows. A pair someone kept apart must not be merged behind them
    on the next run."""
    from venona.pipeline.curate import identical_pairs

    entity(conn, "company:dhs", "DHS")
    entity(conn, "company:department-of-homeland-security", "Department of Homeland Security")
    _dup(conn, "c1", "company:dhs", "company:department-of-homeland-security", "DHS")
    conn.execute("UPDATE alias_conflicts SET resolved=1, resolution='kept_apart'")
    conn.commit()

    assert identical_pairs(conn) == []


def test_a_chain_collapses_onto_one_survivor(conn: db.Conn) -> None:
    """Three CSIS entities existed live. Without resolving through
    `entity_mappings` mid-pass, the second hop would target an entity that had
    already been merged away and `merge_entities` would refuse it."""
    from venona.pipeline.curate import identical_pairs

    entity(conn, "company:csis", "CSIS")
    entity(conn, "company:csis-gloss", "Center for Strategic and International Studies (CSIS)")
    entity(conn, "company:csis-full", "Center for Strategic and International Studies")
    _dup(conn, "c1", "company:csis-gloss", "company:csis-full", "CSIS")
    conn.commit()

    loser, survivor, _ = identical_pairs(conn)[0]
    merge_entities(conn, from_id=loser, to_id=survivor, merge_id="merge:1", reason="auto", now=NOW)
    conn.commit()

    _dup(conn, "c2", "company:csis", "company:csis-gloss", "CSIS")
    pairs = identical_pairs(conn)
    assert pairs, "the second pair must still be mergeable after the first"
    assert pairs[0][1] == "company:csis-full", (
        "must target the surviving id, not the merged-away one"
    )


def test_a_merged_entitys_aliases_move_to_the_survivor(pair: db.Conn) -> None:
    """A regression the merge work introduced and nearly shipped.

    `projector.py` keyed aliases by raw `entity_id`, so a merged-away entity
    took its aliases with it: once `DHS` merged into `Department of Homeland
    Security`, the surviving node carried no "DHS" and `/graph/search?q=DHS`
    stopped finding a node that WAS findable before the merge.

    `alias_index.py` already handled this for annotation, so hover cards kept
    working while graph search regressed quietly — which is why it took a live
    search to notice.
    """
    from venona.graph.projector import _entity_rows, resolve_mappings

    merge_entities(
        pair,
        from_id="company:mda",
        to_id="company:missile-defense-agency",
        merge_id="merge:1",
        reason="acronym of",
        now=NOW,
    )
    pair.commit()

    rows = _entity_rows(pair, resolve_mappings(pair))
    survivor = next(
        r for group in rows.values() for r in group if r.id == "company:missile-defense-agency"
    )

    assert "MDA" in survivor.aliases, "the loser's acronym must stay searchable"
    assert not any(r.id == "company:mda" for group in rows.values() for r in group), (
        "the merged-away entity must not project as its own node"
    )


# ── the adjudicator's verdict is an ACTION, not a predicate ──────────


def test_part_of_between_an_org_and_a_country_means_based_in() -> None:
    """`Government of Canada` / `Canada` was suggested `part_of`, and the API
    then refused it:

        PART_OF object must be one of ['Company', 'Program'], got Country

    The model was not wrong -- one of them does belong to the other -- but
    "part of" is plain English and PART_OF is a predicate, and
    `config/ontology.yaml` says in as many words that a governing body is an
    Organization BASED_IN its country, never the country itself.
    """
    from venona.pipeline.curate import _relation_for

    assert _relation_for("Company", "Country") == "BASED_IN"


def test_part_of_wins_when_part_of_is_legal() -> None:
    """The literal reading is preferred wherever the ontology permits it, so
    `GDIT` / `General Dynamics` is unaffected by the translation."""
    from venona.pipeline.curate import _relation_for

    assert _relation_for("Company", "Company") == "PART_OF"


def _pair(a_type: str = "Platform", b_type: str = "Platform") -> dict:
    return {
        "existing_entity_id": "plat:f-16-block-70",
        "a_name": "F-16 Block 70",
        "a_type": a_type,
        "proposed_entity_id": "plat:f-16",
        "b_name": "F-16",
        "b_type": b_type,
    }


def test_a_variant_verdict_is_stored_as_variant_of() -> None:
    """ADR 0252: the run wrote `F-16 Block 70 COMPONENT_OF F-16` because a
    version of a design had nowhere else to go."""
    from venona.pipeline.curate import suggestion_for

    verdict = {"verdict": "variant", "child": "F-16 Block 70"}
    assert suggestion_for(verdict, _pair()) == ("part_of", "plat:f-16-block-70", "VARIANT_OF")


def test_part_of_between_platforms_still_means_a_component() -> None:
    from venona.pipeline.curate import _relation_for, suggestion_for

    assert _relation_for("Platform", "Platform") == "COMPONENT_OF"
    verdict = {"verdict": "part_of", "child": "F-16 Block 70"}
    assert suggestion_for(verdict, _pair())[2] == "COMPONENT_OF"


@pytest.mark.negative
def test_a_variant_no_predicate_can_express_is_kept_apart() -> None:
    """Between two Programs there is no VARIANT_OF, and a curator's only
    answer would be keep apart."""
    from venona.pipeline.curate import suggestion_for

    verdict = {"verdict": "variant", "child": "F-16 Block 70"}
    assert suggestion_for(verdict, _pair("Program", "Program")) == ("distinct", None, None)


def test_an_ambiguous_pair_is_left_for_the_curator() -> None:
    """More than one legal predicate and no PART_OF is a question, not an
    answer. NULL is what makes the UI show the choices with nothing
    preselected instead of guessing -- which is exactly what it used to do."""
    from venona.pipeline.curate import _relation_for

    # Person -> Organization admits both WORKS_FOR and LEADS.
    assert _relation_for("Person", "Company") is None


@pytest.mark.negative
def test_a_pair_no_predicate_can_connect_yields_none() -> None:
    from venona.pipeline.curate import _relation_for

    assert _relation_for("Country", "Person") is None
    assert _relation_for(None, "Company") is None


def test_every_suggested_predicate_is_one_the_api_would_accept() -> None:
    """The contract between the two halves: whatever `_relation_for` returns,
    `check_relation` must accept for that ordered pair of types. Otherwise the
    queue is once again proposing a decision the API refuses."""
    from venona.ontology import get_ontology
    from venona.pipeline.curate import _relation_for

    ontology = get_ontology()
    types = [t.name for t in ontology.entity_types.values()]
    for child in types:
        for parent in types:
            predicate = _relation_for(child, parent)
            if predicate is None:
                continue
            assert ontology.check_relation(predicate, child, parent) is None, (
                f"{child} --{predicate}--> {parent} would be refused"
            )


# ── two merges at once ───────────────────────────────────────────────


@pytest.mark.negative
def test_two_merges_of_a_pair_cannot_close_a_loop(conn: db.Conn, database_url: str) -> None:
    """⭐ A -> B and B -> A, both passing their cycle guard.

    Each transaction reads a mapping table in which the other has not
    committed, so both see no cycle, both pass, and both write. `resolve_mappings`
    then walks a loop and breaks it arbitrarily — which entity survives depends
    on iteration order, and nothing raises or reports it. The graph is simply
    wrong, in a way that reads as a model mistake rather than a race.
    """
    entity(conn, "org:alpha", "Alpha")
    entity(conn, "org:bravo", "Bravo")
    conn.commit()

    ready, attempted = threading.Barrier(2), threading.Barrier(2)
    outcomes: dict[str, str] = {}

    def merge(name: str, from_id: str, to_id: str) -> None:
        own = db.connect(database_url)
        try:
            ready.wait(timeout=10)
            try:
                merge_entities(
                    own,
                    from_id=from_id,
                    to_id=to_id,
                    merge_id=f"merge:{name}",
                    reason="duplicate",
                    now=NOW,
                )
                outcomes[name] = "merged"
            except MergeError:
                outcomes[name] = "refused"
            attempted.wait(timeout=10)
        finally:
            own.close()

    threads = [
        threading.Thread(target=merge, args=("ab", "org:alpha", "org:bravo")),
        threading.Thread(target=merge, args=("ba", "org:bravo", "org:alpha")),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=20)

    assert sorted(outcomes.values()) == ["merged", "refused"], (
        f"both merges committed and the mappings form a cycle: {outcomes}"
    )
    active = conn.execute("SELECT from_id, to_id FROM entity_mappings WHERE active = 1").fetchall()
    assert len(active) == 1
    # And the surviving mapping resolves to something that is not itself mapped.
    survivor = active[0]["to_id"]
    assert resolve_entity_id(conn, survivor) == survivor


@pytest.mark.negative
def test_one_entity_cannot_be_merged_into_two_survivors(conn: db.Conn, database_url: str) -> None:
    """The fork, which a per-PAIR lock would miss entirely — the two merges touch
    different pairs. One `from_id` with two active mappings leaves unmerge unable
    to say which to undo."""
    entity(conn, "org:loser", "Loser")
    entity(conn, "org:first", "First")
    entity(conn, "org:second", "Second")
    conn.commit()

    ready, attempted = threading.Barrier(2), threading.Barrier(2)
    outcomes: dict[str, str] = {}

    def merge(name: str, to_id: str) -> None:
        own = db.connect(database_url)
        try:
            ready.wait(timeout=10)
            try:
                merge_entities(
                    own,
                    from_id="org:loser",
                    to_id=to_id,
                    merge_id=f"merge:{name}",
                    reason="duplicate",
                    now=NOW,
                )
                outcomes[name] = "merged"
            except MergeError:
                outcomes[name] = "refused"
            attempted.wait(timeout=10)
        finally:
            own.close()

    threads = [
        threading.Thread(target=merge, args=("one", "org:first")),
        threading.Thread(target=merge, args=("two", "org:second")),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=20)

    active = conn.execute(
        "SELECT from_id, to_id FROM entity_mappings WHERE from_id = 'org:loser' AND active = 1"
    ).fetchall()
    assert len(active) == 1, f"one entity ended up merged into {len(active)} survivors: {outcomes}"


# ── surfaces two entities both claim ─────────────────────────────────


def _gen_alias(conn: db.Conn, entity_id: str, surface: str) -> None:
    """A generated acronym: the entity never wrote it down."""
    db.upsert(
        conn,
        "aliases",
        {
            "id": f"{entity_id}|{surface}",
            "entity_id": entity_id,
            "surface": surface,
            "normalized": surface.lower(),
            "strictness": 2,
            "is_acronym": 1,
            "added_by": "acronym_gen",
            "created_at": NOW,
        },
        key=["id"],
    )


def test_two_entities_claiming_one_surface_are_reported(conn: db.Conn) -> None:
    """`ACC` is Army Contracting Command and Air Combat Command. Nothing in the
    letters chooses, and until this existed heap order did."""
    from venona.services.alias_index import entity_collisions

    entity(conn, "govt:army-contracting-command", "Army Contracting Command")
    entity(conn, "unit:air-combat-command", "Air Combat Command")
    _alias(conn, "govt:army-contracting-command", "ACC")
    _alias(conn, "unit:air-combat-command", "ACC")
    conn.commit()

    assert entity_collisions(conn).get("acc") == {
        "govt:army-contracting-command",
        "unit:air-combat-command",
    }


def test_a_generated_acronym_cannot_make_a_surface_contested(conn: db.Conn) -> None:
    """`BAH` is Booz Allen Hamilton, and separately the initials the generator
    derived from a person's name. One of those is an entity a publication
    writes; the other is an artefact, and an artefact must not be able to take
    a real company's underline away."""
    from venona.services.alias_index import entity_collisions

    entity(conn, "company:bowen-allen-holdings", "Bowen Allen Holdings")
    entity(conn, "person:basil-a-hart", "Basil A. Hart")
    _alias(conn, "company:bowen-allen-holdings", "BAH")
    _gen_alias(conn, "person:basil-a-hart", "BAH")
    conn.commit()

    assert "bah" not in entity_collisions(conn)


def test_a_merged_pair_stops_being_contested(conn: db.Conn) -> None:
    """The reason this is counted on the RESOLVED ref. Two ids for Palantir both
    claim the bare name; once they are one entity the surface has one owner and
    the underline has to come back."""
    from venona.services.alias_index import entity_collisions

    entity(conn, "company:vantage", "Vantage")
    entity(conn, "company:vantage-technologies", "Vantage Technologies")
    _alias(conn, "company:vantage", "Vantage")
    _alias(conn, "company:vantage-technologies", "Vantage")
    conn.commit()
    assert "vantage" in entity_collisions(conn)

    merge_entities(
        conn,
        from_id="company:vantage",
        to_id="company:vantage-technologies",
        merge_id="merge:test",
        reason="auto: corporate form",
        now=NOW,
    )
    conn.commit()

    assert "vantage" not in entity_collisions(conn)


def test_a_surface_already_suppressed_stays_contested(conn: db.Conn) -> None:
    """Counted on the RAW strictness. A filed surface reads back as strictness 0
    -- that is what suppression IS -- so counting the effective value would
    release it, re-file it next run, and flap forever."""
    from venona.services.alias_index import entity_collisions

    entity(conn, "govt:army-contracting-command", "Army Contracting Command")
    entity(conn, "unit:air-combat-command", "Air Combat Command")
    _alias(conn, "govt:army-contracting-command", "ACC")
    _alias(conn, "unit:air-combat-command", "ACC")
    db.insert(
        conn,
        "ambiguous_surfaces",
        {"normalized": "acc", "reason": "2 entities claim this surface", "created_at": NOW},
    )
    conn.commit()

    assert "acc" in entity_collisions(conn)


def test_a_variant_letter_is_not_a_plural() -> None:
    """Why `_comparable` does not fold plurals, measured rather than assumed.

    Stripping a trailing `s` was proposed to catch `High-energy lasers` against
    `high-energy laser`. Run against the whole live queue it collapsed exactly
    two pairs, and the other was `MH-60 Seahawk` against `MH-60S Seahawk` -- a
    distinct aircraft whose variant letter reads as a plural. The rule turns a
    designator into its own family, invisibly, which is the failure the
    auto-merge ban exists for.

    In this domain designators routinely end in a letter that looks like one:
    MH-60S, F/A-18E, PAC-3. One correct merge does not pay for that class, so
    the lasers pair stays a human decision and this assertion says why.
    """
    from venona.db.models import normalize
    from venona.pipeline.curate import _comparable

    assert normalize(_comparable("MH-60S Seahawk")) != normalize(_comparable("MH-60 Seahawk"))
    assert normalize(_comparable("F/A-18E Super Hornet")) != normalize(
        _comparable("F/A-18 Super Hornet")
    )


# ── what a merged name goes by, and where it came from ────────────────


def test_a_card_lists_the_surfaces_the_absorbed_name_went_by(pair: db.Conn) -> None:
    """The card and graph search have to agree about what a name is called.

    `merge_entities` does not re-point `aliases.entity_id` -- the merge is a row
    in `entity_mappings` -- and `graph/projector.py` folds the loser's surfaces
    onto the survivor when it projects, with a comment recording that
    `/graph/search?q=DHS` otherwise stops finding a node it used to find. The
    card read the survivor's own row alone, so the two disagreed: the graph knew
    the merged-away spelling and the reader's card did not list it.
    """
    merge_entities(
        pair,
        from_id="company:mda",
        to_id="company:missile-defense-agency",
        merge_id="merge:1",
        reason="same agency",
        now=NOW,
    )
    pair.commit()

    card = entity_service.entity_card(pair, "company:missile-defense-agency")

    assert card is not None
    assert "MDA" in card.aliases, card.aliases


def test_the_record_accounts_for_a_whole_merge_CHAIN(conn: db.Conn) -> None:
    """A -> B -> C. Asking C `WHERE to_id = C` returns B and loses A.

    `resolve_mappings` collapses chains to a fixed point, so by the time a
    second merge lands, A's mapping points at C directly or at B depending on
    the order it was written -- and the survivor carries A's aliases, articles
    and connections either way. A screen whose whole job is to say where the
    name came from cannot be the one place A is missing.
    """
    entity(conn, "company:a", "Alpha")
    entity(conn, "company:b", "Beta")
    entity(conn, "company:c", "Gamma")
    conn.commit()

    merge_entities(
        conn, from_id="company:a", to_id="company:b", merge_id="m1", reason="one", now=NOW
    )
    merge_entities(
        conn, from_id="company:b", to_id="company:c", merge_id="m2", reason="two", now=NOW
    )
    conn.commit()

    record = entity_service.record(conn, "company:c")

    assert record is not None
    assert {m.id for m in record.merged_from} == {"company:a", "company:b"}


def test_the_record_reaches_a_merged_away_id_by_its_survivor(pair: db.Conn) -> None:
    """Asking for the loser answers for the winner, as every other read does."""
    merge_entities(
        pair,
        from_id="company:mda",
        to_id="company:missile-defense-agency",
        merge_id="merge:1",
        reason="same agency",
        now=NOW,
    )
    pair.commit()

    record = entity_service.record(pair, "company:mda")

    assert record is not None
    assert record.id == "company:missile-defense-agency"
    assert [m.id for m in record.merged_from] == ["company:mda"]
    # Every surface either name is matched on, with where each came from.
    assert {a.surface for a in record.aliases} == {"MDA", "Missile Defense Agency"}


# ── derivational classes the live queue was waiting on ───────────────


def _lexicons():
    import yaml
    from venona.settings import get_settings

    raw = yaml.safe_load((get_settings().profile_dir / "lexicon.yaml").read_text()) or {}
    return raw


def test_a_possessive_is_spelling_rather_than_identity():
    """Extraction takes a name from the sentence it sits in, so a name written
    possessively arrives that way. `RAND’s` is RAND."""
    from venona.pipeline.curate import _comparable

    assert _comparable("RAND’s") == _comparable("RAND")
    assert _comparable("RAND's") == _comparable("RAND")


def test_a_leading_possessive_is_a_different_entity_and_stays_that_way():
    """`Firefly’s Alpha` is Alpha, not a compound name. A trailing possessive is
    the same name wearing an apostrophe; a leading one belongs to somebody else,
    which is why `_PREFIX` handles that and `_comparable` does not."""
    from venona.pipeline.curate import _comparable

    assert _comparable("Firefly’s Alpha") != _comparable("Firefly Alpha")


def test_a_plural_is_still_not_a_possessive():
    """Only `’s`, never a bare trailing `s` — the difference between a possessive
    and a plural nothing can tell apart. `MH-60S` is not `MH-60`."""
    from venona.pipeline.curate import _comparable

    assert _comparable("Boeings") != _comparable("Boeing")
    assert _comparable("Patriots") != _comparable("Patriot")


def test_a_generational_suffix_still_keeps_two_people_apart():
    """`fixtures/curation/decisions.yaml::ellis-generational-suffix` decides this,
    and it is the opposite of what it looks like from the queue.

    `Jr.` is the one post-nominal that DISTINGUISHES two people rather than
    decorating one: a father and a son share every other token, so `jr`, `sr`
    and `iii` in `person_suffixes` merge them silently. They were in that list
    until that row was written. A branch (`USA`) or a qualification (`PhD`) names
    no person and stays.
    """
    from venona.pipeline.curate import _person_variant

    raw = _lexicons()
    titles, suffixes = set(raw["person_titles"]), set(raw["person_suffixes"])
    assert not (set(raw["person_suffixes"]) & {"jr", "sr", "ii", "iii", "iv"})
    assert (
        _person_variant("Patrick Ellis Jr.", "Patrick Ellis", titles=titles, suffixes=suffixes)
        is None
    )


def test_a_surname_alone_is_still_a_human_decision():
    """Two people share a surname; the given name is what tells them apart."""
    from venona.pipeline.curate import _person_variant

    raw = _lexicons()
    assert (
        _person_variant(
            "Eric Schmidt",
            "Schmidt",
            titles=set(raw["person_titles"]),
            suffixes=set(raw["person_suffixes"]),
        )
        is None
    )


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("JJ Cummings", "JJ “Yank” Cummings"),
        ('Jason "Ox" Rueschhoff', "Jason Rueschhoff"),
        ("Matt Ross", "Brigadier General Matt Ross"),
        ("Adam Telle", "Acting Army Secretary Adam Telle"),
        ("Air Force Lt. Gen. David Sanford", "David Sanford"),
    ],
)
def test_a_nickname_an_office_and_a_spelled_out_rank_are_not_a_name(a, b):
    """Each pair was merged by hand on production; replayed over every person
    pair the queue has held, the widened rule merged ten more of those and none
    of the eight a curator kept apart (2026-10-25)."""
    from venona.pipeline.curate import _person_variant

    raw = _lexicons()
    kw = {"titles": set(raw["person_titles"]), "suffixes": set(raw["person_suffixes"])}
    assert _person_variant(a, b, **kw) is not None


@pytest.mark.negative
def test_an_apostrophe_in_a_surname_is_not_a_nickname():
    from venona.pipeline.curate import _person_variant

    raw = _lexicons()
    kw = {"titles": set(raw["person_titles"]), "suffixes": set(raw["person_suffixes"])}
    assert _person_variant("Sean O'Brien", "Sean Brien", **kw) is None


def test_the_tail_list_does_not_swallow_a_name_that_ends_in_one():
    """`7th Fleet` and `Carlyle Group` are entities. A tail list that reached
    those would make a WRONG merge, which is the direction these lists are
    deliberately biased against."""
    from venona.pipeline.curate import _name_variant

    raw = _lexicons()
    kw = {
        "corporate_form": set(raw["corporate_form"]),
        "generic_tails": set(raw["generic_tails"]),
    }
    assert _name_variant("Carlyle", "Carlyle Group", **kw) is None
    assert _name_variant("7th", "7th Fleet", **kw) is None


def test_the_tails_the_live_queue_turned_up_now_fire():
    from venona.pipeline.curate import _name_variant

    raw = _lexicons()
    kw = {
        "corporate_form": set(raw["corporate_form"]),
        "generic_tails": set(raw["generic_tails"]),
    }
    for short, full in [
        ("PAC-2", "PAC-2 missiles"),
        ("Alpha", "Alpha rocket"),
        ("Sentinel", "Sentinel launchers"),
    ]:
        assert _name_variant(short, full, **kw), f"{short} / {full}"


def test_an_org_form_suffix_keeps_the_fuller_name():
    """`corporate form` survives as the FULLER name: it is the one a reader
    recognises and the one carrying the edges."""
    from venona.pipeline.curate import _name_variant

    raw = _lexicons()
    kw = {
        "corporate_form": set(raw["corporate_form"]),
        "generic_tails": set(raw["generic_tails"]),
    }
    result = _name_variant(
        "Naval Undersea Warfare Center Keyport",
        "Naval Undersea Warfare Center Keyport Division",
        **kw,
    )
    assert result is not None
    survivor, why = result
    assert why == "corporate form"
    assert survivor.endswith("Division")


# ── a gloss written in place of its expansion ─────────────────────────


def test_a_short_form_two_expansions_both_write_is_never_merged(conn: db.Conn) -> None:
    """`PAE Fires` is what a Program AND a Portfolio Acquisition Executive both
    abbreviate to, so folding it into one of them picks a reading nobody
    stated -- the `DIA` shape, reached through a gloss instead of a whole name.
    The third entity does not even need a gloss of its own to count."""
    from venona.pipeline.curate import identical_pairs

    entity(conn, "govt:pae-fires", "PAE Fires", entity_type="GovernmentBody")
    entity(
        conn,
        "govt:program-pae-fires",
        "Program Acquisition Executive (PAE) Fires",
        entity_type="GovernmentBody",
    )
    entity(
        conn,
        "govt:portfolio-fires",
        "Portfolio Acquisition Executive Fires",
        entity_type="GovernmentBody",
    )
    _dup(conn, "c1", "govt:pae-fires", "govt:program-pae-fires", "PAE Fires")

    assert identical_pairs(conn) == []


def test_without_a_namesake_the_expansion_survives(conn: db.Conn) -> None:
    from venona.pipeline.curate import identical_pairs

    entity(conn, "govt:pae-fires", "PAE Fires", entity_type="GovernmentBody")
    entity(
        conn,
        "govt:program-pae-fires",
        "Program Acquisition Executive (PAE) Fires",
        entity_type="GovernmentBody",
    )
    _dup(conn, "c1", "govt:pae-fires", "govt:program-pae-fires", "PAE Fires")

    assert identical_pairs(conn) == [("govt:pae-fires", "govt:program-pae-fires", "gloss form")]


@pytest.mark.parametrize(
    ("name", "short"),
    [
        ("PAC-3 Missile Segment Enhancement (MSE)", "PAC-3 MSE"),
        ("Indirect Fire Protection Capability (IFPC) Increment 2", "IFPC Increment 2"),
        # The gloss would be the whole result: `acronym of` owns that question,
        # with the collision guard a bare acronym needs.
        ("Defense Intelligence Agency (DIA)", None),
        # Nothing trailing the head generates it, so it is not a gloss of it.
        ("Maven Smart System (Palantir)", None),
        ("Maven Smart System", None),
    ],
)
def test_a_gloss_form_is_read_off_the_name_itself(name: str, short: str | None) -> None:
    from venona.pipeline.curate import _gloss_form

    assert _gloss_form(name) == short


def test_a_surface_that_is_a_name_is_a_question_not_our_bug(conn: db.Conn) -> None:
    """LIVE QUEUE, 2026-10-25: all ten open `resolver_error` rows had a surface
    equal to one side's own name, so no acronym generator was involved. Filed
    as our bug they were excluded from auto-merge AND adjudication, and shown to
    nobody for six weeks."""
    from venona.pipeline.curate import _classify

    entity(conn, "tech:counter-drone-system", "counter-drone system", entity_type="Technology")
    entity(
        conn, "tech:counter-drone-technology", "counter-drone technology", entity_type="Technology"
    )
    conn.commit()
    row = {
        "id": "dup|tech:counter-drone-system|tech:counter-drone-technology",
        "surface": "counter-drone system",
        "existing_entity_id": "tech:counter-drone-system",
        "proposed_entity_id": "tech:counter-drone-technology",
    }
    assert _classify(conn, row) == "duplicate"


def test_a_surface_neither_name_supports_is_still_our_bug(conn: db.Conn) -> None:
    from venona.pipeline.curate import _classify

    entity(
        conn, "govt:us-strategic-command", "U.S. Strategic Command", entity_type="GovernmentBody"
    )
    entity(conn, "govt:space-command", "Space Command", entity_type="GovernmentBody")
    conn.commit()
    row = {
        "id": "xyz|GovernmentBody|govt:space-command",
        "surface": "QZX",
        "existing_entity_id": "govt:us-strategic-command",
        "proposed_entity_id": "govt:space-command",
    }
    assert _classify(conn, row) == "resolver_error"
