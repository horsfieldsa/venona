"""Targeted corroboration: which entities to chase, and what the sweep asks for.

The premise is measured, not assumed. SQLite holds 468 relationship triples and
Neo4j shows 97, because `projector.py` projects only `active` entities and the
Cypher `MATCH` silently drops an edge whose endpoint has no node. 79% of what the
system knows is held back by the corroboration gate, and nothing seeks the second
article that would release it.
"""

from __future__ import annotations

import pytest
from helpers import NOW, entity
from venona.db import core as db
from venona.pipeline.research import StuckEntity, stuck_entities, sweep_prompt


def _relation(conn: db.Conn, rid: str, subject: str, obj: str) -> None:
    db.upsert(
        conn,
        "assertions",
        {
            "id": rid,
            "run_id": "run:test",
            "article_id": "doc:r",
            "cluster_id": "cluster:r",
            "kind": "relation",
            "predicate": "PARTNERS_WITH",
            "subject_id_raw": subject,
            "object_id_raw": obj,
            "evidence_quote": "A partnered with B.",
            "confidence": 0.9,
            "method": "extraction",
            "created_at": NOW,
        },
        key=["id"],
    )


@pytest.fixture
def blocked(conn: db.Conn) -> db.Conn:
    """One entity blocking three relationships, one blocking one, one active."""
    entity(conn, "company:hub", "Hub Corp", "provisional")
    entity(conn, "company:edge", "Edge Corp", "provisional")
    entity(conn, "company:live", "Live Corp", "active")
    entity(conn, "company:other-a", "Other A", "active")
    entity(conn, "company:other-b", "Other B", "active")

    # hub is an endpoint of three triples, edge of one.
    for i, other in enumerate(["company:live", "company:other-a", "company:other-b"]):
        _relation(conn, f"assert:h{i}", "company:hub", other)
    _relation(conn, "assert:e0", "company:edge", "company:live")
    conn.commit()
    return conn


def test_ranking_is_by_blocked_relationships_not_mentions(blocked: db.Conn) -> None:
    """The whole point of the targeting.

    An entity mentioned twice that sits on no relationship costs the graph
    nothing. Fincantieri — mentioned once — holds seven edges out of the
    projection. Ranking by mention count would chase the wrong one.
    """
    targets = stuck_entities(blocked, limit=10)
    names = [t.entity_id for t in targets]

    assert names[0] == "company:hub", f"hub blocks the most, got {names}"
    assert targets[0].blocked > targets[1].blocked


@pytest.mark.negative
def test_active_entities_are_never_targeted(blocked: db.Conn) -> None:
    """They are already projected; researching them buys nothing."""
    assert all(t.entity_id != "company:live" for t in stuck_entities(blocked, limit=10))


def test_dormant_entities_are_targeted(blocked: db.Conn) -> None:
    """A dormant entity blocking relationships is a research target, not a
    retired one — otherwise the 30-day dormancy rule quietly removes exactly the
    entities most worth chasing."""
    blocked.execute("UPDATE entities SET status='dormant' WHERE id='company:hub'")
    blocked.commit()

    assert any(t.entity_id == "company:hub" for t in stuck_entities(blocked, limit=10))


def test_explicit_entity_ids_narrow_the_set(blocked: db.Conn) -> None:
    targets = stuck_entities(blocked, limit=10, entity_ids=["company:edge"])
    assert [t.entity_id for t in targets] == ["company:edge"]


def test_nothing_to_do_returns_empty(conn: db.Conn) -> None:
    """A corpus with no uncorroborated relationship endpoints must not produce a
    sweep — that would spend money to research nothing."""
    assert stuck_entities(conn, limit=10) == []


# ── the prompt ───────────────────────────────────────────────────────


def test_the_sweep_asks_for_a_different_publisher() -> None:
    """Load-bearing. Promotion counts distinct `site_domain`, so three more
    articles from the outlet we already have corroborates nothing."""
    prompt = sweep_prompt(
        [StuckEntity("company:x", "Fincantieri", "Company", "provisional", 7, 1, 1)],
        dateline="",
    )
    assert "Fincantieri" in prompt
    assert "DIFFERENT publisher" in prompt


def test_the_sweep_asks_for_urls_not_summaries() -> None:
    """Same discipline as discovery and acronym Tier 3: the model is a search
    tool, and the pipeline reads the real page itself."""
    prompt = sweep_prompt(
        [StuckEntity("company:x", "Sikorsky", "Company", "provisional", 5, 1, 1)],
        dateline="",
    )
    assert "Do not summarise" in prompt
    assert "URL" in prompt


def test_a_name_whose_partner_is_also_unconfirmed_sorts_last(conn: db.Conn) -> None:
    """THE RANKING, and the measurement that changed it.

    An edge appears the moment BOTH its names are confirmed, so confirming a
    name whose partner is still unconfirmed reveals nothing. Ranking by total
    blocked edges ignores that: measured on the live corpus, of the 2,079
    blocked-edge slots it optimised for only 893 had a confirmed partner, so at
    most 43% of a $138 sweep's spend was on edges that could appear at all.

    `deep` below sits on three edges and none of them can appear -- every
    partner is provisional too. `shallow` sits on one that can. The sweep should
    buy the one, not the three.
    """
    entity(conn, "company:deep", "Deep Corp", "provisional")
    entity(conn, "company:shallow", "Shallow Corp", "provisional")
    entity(conn, "company:live", "Live Corp", "active")
    for i, other in enumerate(["company:p1", "company:p2", "company:p3"]):
        entity(conn, other, f"Partner {i}", "provisional")
        _relation(conn, f"assert:d{i}", "company:deep", other)
    _relation(conn, "assert:s0", "company:shallow", "company:live")
    conn.commit()

    targets = stuck_entities(conn, limit=10)
    order = [t.entity_id for t in targets]
    assert order.index("company:shallow") < order.index("company:deep"), (
        f"one edge that can appear beats three that cannot, got {order}"
    )
    ready = {t.entity_id: t.ready for t in targets}
    assert ready["company:shallow"] == 1
    assert ready["company:deep"] == 0


def test_a_name_with_no_confirmed_partner_is_still_reachable(conn: db.Conn) -> None:
    """Sorted last, never dropped. Confirming it makes its neighbours one-away
    in their turn, so a run that asks for enough names should still reach it --
    the ranking is an order, not a filter."""
    entity(conn, "company:deep", "Deep Corp", "provisional")
    entity(conn, "company:other", "Other Corp", "provisional")
    _relation(conn, "assert:d0", "company:deep", "company:other")
    conn.commit()

    assert {t.entity_id for t in stuck_entities(conn, limit=10)} == {
        "company:deep",
        "company:other",
    }


@pytest.mark.parametrize(
    ("promoted", "degraded", "expected"),
    [
        (2, False, "delivered"),
        (2, True, "delivered"),
        (0, False, "nothing_found"),
        # LIVE, 2026-10-22: a session limit cut the reading off after 54 of
        # 308 stories, and the run said the press held no second source.
        (0, True, None),
    ],
)
def test_a_sweep_cut_short_does_not_report_no_coverage(
    promoted: int, degraded: bool, expected: str | None
) -> None:
    from venona.pipeline.research import coverage_outcome

    assert coverage_outcome(promoted, degraded=degraded) == expected
