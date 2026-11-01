"""The curation API: transport over `services/curate.py`, nothing more.

The merge mechanism was tested from S8; what is new is exposing it over HTTP.
So these cover the seams that HTTP adds — refusals becoming status codes, and
undo actually reaching the database — rather than re-testing merge semantics.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException
from helpers import ANON, NOW, SIGNED, curator, entity
from pydantic import ValidationError
from venona.api.deps import Window
from venona.api.routers import curation
from venona.db import core as db


def _conflict(conn: db.Conn, cid: str, a: str, b: str, kind: str = "duplicate") -> None:
    db.upsert(
        conn,
        "alias_conflicts",
        {
            "id": cid,
            "surface": "ACME",
            "normalized": "acme",
            "existing_entity_id": a,
            "proposed_entity_id": b,
            "entity_type": "Company",
            "kind": kind,
            "created_at": NOW,
        },
        key=["id"],
    )
    conn.commit()


# `ANON` and `SIGNED` are `helpers`' now: the glossary verdicts needed the same
# two fakes, and a second copy is what that module exists to prevent. The unsigned
# case is the default here on purpose -- `authz.actor_id` must answer None rather
# than raise, or a CLI verdict would fail on attribution instead of recording the
# NULL that is the honest record of it. `TestTheVerdictRecordsWhoMadeIt` covers
# the signed path.


@pytest.fixture
def pair(conn: db.Conn) -> db.Conn:
    entity(conn, "company:acme", "ACME")
    entity(conn, "company:acme-corp", "ACME Corporation")
    _conflict(conn, "c1", "company:acme", "company:acme-corp")
    return conn


def test_merge_then_undo_round_trips(pair: db.Conn) -> None:
    """Undo is a server call, not a client illusion: `unmerge` reverses the
    mapping AND reopens the conflict it closed."""
    merge = curation.create_merge(
        curation.MergeRequest(from_id="company:acme", to_id="company:acme-corp"), ANON, conn=pair
    )
    assert merge.active
    row = pair.execute("SELECT resolved, resolution FROM alias_conflicts WHERE id='c1'").fetchone()
    assert (row["resolved"], row["resolution"]) == (1, "merged")

    curation.undo_merge(merge.merge_id, ANON, from_id="company:acme", conn=pair)

    row = pair.execute("SELECT resolved, resolution FROM alias_conflicts WHERE id='c1'").fetchone()
    assert (row["resolved"], row["resolution"]) == (0, None), "the conflict must come back"
    status = pair.execute("SELECT status FROM entities WHERE id='company:acme'").fetchone()[
        "status"
    ]
    assert status == "provisional", "the loser must stop being merged"


@pytest.mark.negative
def test_a_refused_merge_is_409_not_500(pair: db.Conn) -> None:
    """Every `MergeError` case is a legitimate refusal the curator should read —
    a cycle, a self-merge, a survivor already merged away — not a server fault."""
    with pytest.raises(HTTPException) as exc:
        curation.create_merge(
            curation.MergeRequest(from_id="company:acme", to_id="company:acme"), ANON, conn=pair
        )
    assert exc.value.status_code == 409
    assert "itself" in str(exc.value.detail)


def test_keep_apart_then_reopen(pair: db.Conn) -> None:
    """The merge side of undo already existed; keep-apart had no counterpart, so
    a misfired key was unrecoverable from the UI."""
    result = curation.keep_apart("c1", ANON, conn=pair)
    assert result.resolution == "kept_apart"

    reopened = curation.reopen("c1", pair)
    assert reopened.resolved is False
    assert reopened.resolution is None


@pytest.mark.negative
def test_reopen_refuses_a_merged_pair(pair: db.Conn) -> None:
    """Reopening a merged conflict without unmerging would leave the queue and
    the entities disagreeing."""
    curation.create_merge(
        curation.MergeRequest(from_id="company:acme", to_id="company:acme-corp"), ANON, conn=pair
    )
    with pytest.raises(HTTPException) as exc:
        curation.reopen("c1", pair)
    assert exc.value.status_code == 409


def test_conflicts_list_carries_both_names(pair: db.Conn) -> None:
    """A queue showing two ids and no names is unreadable."""
    listing = curation.get_conflicts(
        kind=None, include_resolved=False, window=Window(500, 0), conn=pair
    )
    assert listing.total_open == 1
    item = listing.items[0]
    assert item.existing_name == "ACME"
    assert item.proposed_name == "ACME Corporation"
    assert listing.counts == {"duplicate": 1}


# ── the third verdict: related ───────────────────────────────────────


def test_relate_writes_a_typed_assertion_and_settles_the_conflict(
    pair: db.Conn,
) -> None:
    """`General Dynamics` / `GDIT` is neither a merge nor an unrelated pair.

    Keeping them apart discarded the one fact the conflict surfaced. The
    assertion is written the same way every other one is, but marked `manual`
    with a quote that names the decision — the seed loader's precedent.
    """
    result = curation.relate(
        "c1",
        curation.RelateRequest(
            subject_id="company:acme", object_id="company:acme-corp", predicate="PART_OF"
        ),
        pair,
    )
    assert result.resolution == "related"

    row = pair.execute(
        "SELECT predicate, method, subject_id_raw, object_id_raw, evidence_quote "
        "FROM assertions WHERE kind = 'relation' ORDER BY created_at DESC, id DESC LIMIT 1"
    ).fetchone()
    assert row["predicate"] == "PART_OF"
    assert row["method"] == "manual", "a curated claim must stay distinguishable"
    assert (row["subject_id_raw"], row["object_id_raw"]) == ("company:acme", "company:acme-corp")
    assert "Curated" in row["evidence_quote"]


@pytest.mark.negative
def test_relate_rejects_an_inverted_or_ill_typed_predicate(pair: db.Conn) -> None:
    """Direction is the thing most easily got backwards, and `check_relation`
    is the same guard the pipeline uses — one implementation, not two."""
    entity(pair, "person:someone", "Someone", entity_type="Person")
    _conflict(pair, "c2", "person:someone", "company:acme")

    with pytest.raises(HTTPException) as exc:
        curation.relate(
            "c2",
            curation.RelateRequest(
                subject_id="company:acme", object_id="person:someone", predicate="PART_OF"
            ),
            pair,
        )
    assert exc.value.status_code == 422
    assert "PART_OF" in str(exc.value.detail)


def test_relating_is_undone_by_retracting_not_deleting(pair: db.Conn) -> None:
    """`assertions` is append-only (invariant 1). `retracted_at` is exactly the
    column for a claim made and then withdrawn — and nothing in the codebase
    ever set it before this."""
    curation.relate(
        "c1",
        curation.RelateRequest(
            subject_id="company:acme", object_id="company:acme-corp", predicate="PART_OF"
        ),
        pair,
    )
    curation.reopen("c1", pair)

    row = pair.execute(
        "SELECT retracted_at FROM assertions WHERE kind='relation' "
        "ORDER BY created_at DESC, id DESC LIMIT 1"
    ).fetchone()
    assert row["retracted_at"] is not None, "the claim must be withdrawn, not deleted"
    assert (
        pair.execute("SELECT COUNT(*) AS n FROM assertions WHERE run_id='curation:c1'").fetchone()[
            "n"
        ]
        == 1
    ), "the row itself stays as a record"


def test_options_are_generated_from_the_ontology(pair: db.Conn) -> None:
    """So the choices offered and the rules enforced cannot drift apart."""
    opts = curation.relate_options("c1", pair)
    assert opts.subject_type == "Company"
    names = [p.name for p in opts.forward]
    assert "PART_OF" in names
    assert "WORKS_FOR" not in names, "Person-only predicates must not be offered"


def test_options_type_the_pair_as_it_now_is_not_as_it_was_filed(
    pair: db.Conn,
) -> None:
    """The bug this endpoint shipped with, and the only reader that had it.

    A conflict keeps the ids it was filed with; every other reader resolves the
    merge chain before using them. This one did not, so after `company:canada` was
    merged into `country:canada` it typed the tombstone as an Organization,
    offered PART_OF, never offered BASED_IN -- and `relate`, which DOES resolve,
    answered `PART_OF object must be one of ['Company', 'Program'], got
    Country`, naming a type that had never been on screen.
    """
    entity(pair, "company:gov-of-canada", "Government of Canada")
    entity(pair, "company:canada", "Canada", status="merged")
    entity(pair, "country:canada", "Canada", entity_type="Country")
    _conflict(pair, "c-can", "company:gov-of-canada", "company:canada", kind="containment")
    db.insert(
        pair,
        "entity_mappings",
        {
            "from_id": "company:canada",
            "to_id": "country:canada",
            "merge_id": "merge:canada",
            "active": 1,
            "reason": "test",
            "created_at": NOW,
        },
    )
    pair.commit()

    opts = curation.relate_options("c-can", pair)

    assert opts.object_type == "Country", "the pair is typed as it now IS"
    names = [p.name for p in opts.forward]
    assert "BASED_IN" in names, "the predicate that actually expresses this"
    assert "PART_OF" not in names, "the predicate the API would refuse"


@pytest.mark.negative
def test_relate_requires_a_predicate(pair: db.Conn) -> None:
    """It used to default to PART_OF -- the server guessing the one thing only
    the curator knows, and guessing the predicate that is wrong whenever the
    parent is a Country or a Person."""
    with pytest.raises(ValidationError):
        curation.RelateRequest(subject_id="company:acme", object_id="company:acme-corp")


# ── the two projection numbers ───────────────────────────────────────


async def test_a_merge_of_uncorroborated_entities_is_not_pending(
    pair: db.Conn,
) -> None:
    """Reported as "the rebuild count doesn't increment as much as I merge".

    It should not. Only `active` entities are projected, so merging two
    uncorroborated ones removes nothing from the graph — neither was ever in it.
    Counting merges would promise a rebuild with nothing to do.
    """

    class _NoGraph:
        @property
        def driver(self):
            raise RuntimeError("neo4j down")

    pair.execute(
        "UPDATE entities SET status='provisional' WHERE id IN ('company:acme','company:acme-corp')"
    )
    pair.commit()
    curation.create_merge(
        curation.MergeRequest(from_id="company:acme", to_id="company:acme-corp"), ANON, conn=pair
    )

    pending, blocked = await curation._projection_state(_NoGraph(), pair)
    assert pending == 0, "an unreachable graph is not evidence of pending work"
    assert blocked == 0


async def test_a_curated_relation_with_a_provisional_endpoint_is_blocked(
    pair: db.Conn,
) -> None:
    """Counted separately, and deliberately.

    All ten curated relationships on the live database had an uncorroborated
    endpoint. Folding them into `pending` would leave the banner permanently
    non-zero behind a button that cannot clear it — which trains the operator to
    ignore the banner.
    """

    class _NoGraph:
        @property
        def driver(self):
            raise RuntimeError("neo4j down")

    pair.execute("UPDATE entities SET status='provisional' WHERE id = 'company:acme'")
    pair.commit()
    curation.relate(
        "c1",
        curation.RelateRequest(
            subject_id="company:acme", object_id="company:acme-corp", predicate="PART_OF"
        ),
        pair,
    )

    pending, blocked = await curation._projection_state(_NoGraph(), pair)
    assert blocked == 1, "an uncorroborated endpoint blocks projection"
    assert pending == 0, "and it must not be offered as something a rebuild fixes"


# ── conflicts that outlive a merge ───────────────────────────────────
#
# The failure this class of test exists for: merging what the screen called
# "U.S. Army" answered `survivor 'company:army' is merged`. A conflict filed before
# a merge keeps naming the loser, and the queue and the cards disagreed about
# which entity that was -- the cards resolved, the queue did not.


def test_the_queue_names_the_entity_a_merged_side_became(pair: db.Conn) -> None:
    """The queue resolves merge chains, exactly as the cards beside it do."""
    entity(pair, "company:acme-holdings", "ACME Holdings")
    _conflict(pair, "c2", "company:acme", "company:acme-holdings", kind="containment")
    curation.create_merge(
        curation.MergeRequest(from_id="company:acme", to_id="company:acme-corp"), ANON, conn=pair
    )

    listed = curation.get_conflicts(
        kind=None, include_resolved=False, window=Window(500, 0), conn=pair
    )
    row = next(c for c in listed.items if c.id == "c2")
    assert row.existing_entity_id == "company:acme-corp"
    assert row.existing_name == "ACME Corporation"


def test_a_pair_that_has_become_one_entity_leaves_the_queue(pair: db.Conn) -> None:
    """Nothing left to decide, so it must not be offered as a decision."""
    _conflict(pair, "c2", "company:acme", "company:acme-corp", kind="containment")
    curation.create_merge(
        curation.MergeRequest(from_id="company:acme", to_id="company:acme-corp"), ANON, conn=pair
    )

    listed = curation.get_conflicts(
        kind=None, include_resolved=False, window=Window(500, 0), conn=pair
    )
    assert [c.id for c in listed.items] == []


def test_merging_a_stale_conflict_succeeds(pair: db.Conn) -> None:
    """The reported bug, end to end.

    The queue offers the pair against the survivor; posting those ids must
    merge rather than answer 409 about an id the curator never saw.
    """
    entity(pair, "company:acme-holdings", "ACME Holdings")
    _conflict(pair, "c2", "company:acme", "company:acme-holdings", kind="containment")
    curation.create_merge(
        curation.MergeRequest(from_id="company:acme", to_id="company:acme-corp"), ANON, conn=pair
    )

    listed = curation.get_conflicts(
        kind=None, include_resolved=False, window=Window(500, 0), conn=pair
    )
    row = next(c for c in listed.items if c.id == "c2")

    merge = curation.create_merge(
        curation.MergeRequest(from_id=row.proposed_entity_id, to_id=row.existing_entity_id),
        ANON,
        conn=pair,
    )
    assert merge.to_id == "company:acme-corp"
    assert merge.from_id == "company:acme-holdings"


def test_a_merge_settles_a_conflict_filed_under_pre_merge_ids(pair: db.Conn) -> None:
    """`close_conflicts_for_pair` matches on merge closures.

    Matched on the two ids as given, a merge of (survivor, other) would miss
    the row filed under (loser, other) and leave answered work in the queue --
    the same staleness one layer up.
    """
    entity(pair, "company:acme-holdings", "ACME Holdings")
    _conflict(pair, "c2", "company:acme", "company:acme-holdings", kind="containment")
    curation.create_merge(
        curation.MergeRequest(from_id="company:acme", to_id="company:acme-corp"), ANON, conn=pair
    )
    # c2 stores `company:acme`; the merge below names its survivor.
    curation.create_merge(
        curation.MergeRequest(from_id="company:acme-holdings", to_id="company:acme-corp"),
        ANON,
        conn=pair,
    )

    row = pair.execute("SELECT resolved, resolution FROM alias_conflicts WHERE id='c2'").fetchone()
    assert (row["resolved"], row["resolution"]) == (1, "merged")


def test_an_unmerge_restores_the_original_pair(pair: db.Conn) -> None:
    """Resolving on READ rather than rewriting the row is what buys this: the
    stored ids never changed, so reversing the merge reverses the display."""
    entity(pair, "company:acme-holdings", "ACME Holdings")
    _conflict(pair, "c2", "company:acme", "company:acme-holdings", kind="containment")
    merge = curation.create_merge(
        curation.MergeRequest(from_id="company:acme", to_id="company:acme-corp"), ANON, conn=pair
    )

    listed = curation.get_conflicts(
        kind=None, include_resolved=False, window=Window(500, 0), conn=pair
    )
    assert next(c for c in listed.items if c.id == "c2").existing_entity_id == "company:acme-corp"

    curation.undo_merge(merge.merge_id, ANON, from_id="company:acme", conn=pair)

    listed = curation.get_conflicts(
        kind=None, include_resolved=False, window=Window(500, 0), conn=pair
    )
    assert next(c for c in listed.items if c.id == "c2").existing_entity_id == "company:acme"


# ── who decided ──────────────────────────────────────────────────────
#
# Migration 0002 added `resolved_by`, `merged_by`, `decided_by` and
# `status_changed_by` — "every one of these tables already recorded THAT a verdict
# was reached and WHEN; none recorded WHO, because there was only ever one who."
# The columns landed and nothing wrote them and nothing read them, which made the
# audit trail write-only in the worst sense: a verdict nobody can attribute is
# worth about as much as one nobody recorded, and on a shared corpus "somebody
# decided this" is not an answer a reader can act on.
#
# The actor arrives as a plain `actor: str | None` from the router. Identity does
# not go into `services/` (a service that can reach a `Request` is a service two
# callers cannot share), and `api/authz.py::actor_id` is the single place a router
# reaches for it.


def _curator(conn: db.Conn) -> None:
    curator(conn)


class TestTheVerdictRecordsWhoMadeIt:
    def test_a_merge_records_its_actor_and_returns_the_email(self, pair: db.Conn) -> None:
        """The id is stored; the EMAIL is what comes back. A `users.id` on screen
        tells a curator nothing."""
        _curator(pair)
        merge = curation.create_merge(
            curation.MergeRequest(from_id="company:acme", to_id="company:acme-corp"),
            SIGNED,
            conn=pair,
        )
        assert merge.merged_by == "curator@example.com"

        stored = pair.execute(
            "SELECT merged_by FROM entity_mappings WHERE merge_id = ?", (merge.merge_id,)
        ).fetchone()
        assert stored["merged_by"] == "user:curator"

    def test_the_merge_list_joins_the_email_back(self, pair: db.Conn) -> None:
        """A separate read path, and the one a curator actually browses."""
        _curator(pair)
        curation.create_merge(
            curation.MergeRequest(from_id="company:acme", to_id="company:acme-corp"),
            SIGNED,
            conn=pair,
        )
        listed = curation.get_merges(active_only=True, window=Window(50, 0), conn=pair)
        assert [m.merged_by for m in listed.items] == ["curator@example.com"]

    def test_a_merge_also_signs_the_conflict_it_settled(self, pair: db.Conn) -> None:
        """A merge answers the queue row as a side effect, and that verdict needs
        the same name on it -- otherwise the queue shows a decision with no author
        while the merge beside it has one."""
        _curator(pair)
        curation.create_merge(
            curation.MergeRequest(from_id="company:acme", to_id="company:acme-corp"),
            SIGNED,
            conn=pair,
        )
        row = pair.execute("SELECT resolved_by FROM alias_conflicts WHERE id='c1'").fetchone()
        assert row["resolved_by"] == "user:curator"

    def test_keep_apart_records_its_actor(self, pair: db.Conn) -> None:
        """The other verdict, and the one 40% of the containment queue deserves."""
        _curator(pair)
        result = curation.keep_apart("c1", SIGNED, conn=pair)
        assert result.resolved_by == "curator@example.com"

    def test_an_unsigned_verdict_records_null_rather_than_failing(self, pair: db.Conn) -> None:
        """The CLI and the pipeline have no principal, and that is not an error.

        Migration 0002: "NULL is honest rather than lazy -- it means decided before
        this migration, or by the pipeline rather than a person." Refusing the
        verdict would trade a working feature for a column.
        """
        result = curation.keep_apart("c1", ANON, conn=pair)
        assert result.resolved_by is None
        row = pair.execute(
            "SELECT resolved, resolved_by FROM alias_conflicts WHERE id='c1'"
        ).fetchone()
        assert (row["resolved"], row["resolved_by"]) == (1, None)

    def test_the_queue_shows_the_actor_on_a_resolved_row(self, pair: db.Conn) -> None:
        """End to end through the read path the UI uses, including `include_resolved`
        -- which is the only view where an actor is ever visible."""
        _curator(pair)
        curation.keep_apart("c1", SIGNED, conn=pair)
        listed = curation.get_conflicts(
            kind=None, include_resolved=True, window=Window(500, 0), conn=pair
        )
        assert [c.resolved_by for c in listed.items] == ["curator@example.com"]


def _relation(conn: db.Conn, assertion_id: str, *, quote: str) -> None:
    db.upsert(
        conn,
        "assertions",
        {
            "id": assertion_id,
            "run_id": "run:daily",
            "article_id": "doc-1",
            "cluster_id": "cluster:doc-1",
            "kind": "relation",
            "predicate": "PART_OF",
            "subject_id_raw": "company:acme",
            "object_id_raw": "company:acme-corp",
            "evidence_quote": quote,
            "confidence": 0.55,
            "method": "extraction",
            "created_at": NOW,
        },
        key=["id"],
    )
    conn.commit()


def test_an_unsupported_relation_can_be_withdrawn(pair: db.Conn) -> None:
    """The error this router could not act on before.

    Every other verdict here is about identity: are these two names one thing.
    This is the other kind, where the names are right and the SENTENCE behind
    the edge says something else. On the live corpus a WORKS_FOR edge rested on
    a quote about hormone levels that named neither end of it, and there was
    nowhere for a reader who had spotted that to put the finding.
    """
    _relation(pair, "a-1", quote="a sentence about something else entirely.")

    result = curation.retract("a-1", pair)

    assert result["retracted_at"]
    assert not result["already_retracted"]
    row = pair.execute("SELECT retracted_at FROM assertions WHERE id = 'a-1'").fetchone()
    assert row["retracted_at"], "the claim must be withdrawn, not deleted"


def test_withdrawing_twice_keeps_the_first_timestamp(pair: db.Conn) -> None:
    """When it was withdrawn is the fact worth keeping. A second call that
    moved it would quietly rewrite the history of a decision somebody made."""
    _relation(pair, "a-2", quote="another sentence.")
    first = curation.retract("a-2", pair)

    again = curation.retract("a-2", pair)

    assert again["already_retracted"]
    assert again["retracted_at"] == first["retracted_at"]


def test_withdrawing_something_absent_is_a_404(pair: db.Conn) -> None:
    """A silent success on an id nobody holds would let a curator believe they
    had withdrawn a claim that is still on a reader's screen."""
    with pytest.raises(HTTPException) as caught:
        curation.retract("a-nope", pair)

    assert caught.value.status_code == 404
