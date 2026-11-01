"""The set-based evidence aggregate agrees with the fold it replaced.

`promote_provisionals` and `refresh_mention_counts` both decide on three
numbers per entity: how many articles carry it, how many independent
PUBLISHERS, and when it was last asserted. Those were read as one row per
(entity, assertion) pair and folded in Python, twice per run.

Counting in SQL is safe only if it reproduces three things exactly, and each
one is a SILENTLY wrong answer when it does not -- all three decide whether an
entity appears in the graph at all:

  1. merge chains resolved TRANSITIVELY. A one-hop join credits A->B->C to B.
  2. `source_count` as distinct publisher DOMAINS, not feeds. Two feeds from
     one outlet are one source of corroboration.
  3. dormancy from the newest ASSERTION, not `entities.last_seen` -- which
     `promote_provisionals` rewrites, so reading it resets the clock every run
     and nothing ever goes quiet.

The tests below are a golden comparison against the Python fold, on a corpus
built to exercise all three at once.
"""

from __future__ import annotations

from venona.db import core as db
from venona.db.repositories import pipeline_resolve as repo
from venona.graph.projector import resolve_mappings

NOW = "2026-08-26T00:00:00.000Z"


def _entity(conn: db.Conn, entity_id: str, status: str = "provisional") -> None:
    db.upsert(
        conn,
        "entities",
        {
            "id": entity_id,
            "type": "Company",
            "name": entity_id,
            "canonical_key": entity_id,
            "status": status,
            "relevance": 0.5,
            "first_seen": NOW,
            "last_seen": NOW,
            "created_at": NOW,
        },
        key=["id"],
    )


def _document(conn: db.Conn, doc_id: str, domain: str, source_id: str) -> None:
    db.upsert(
        conn,
        "documents",
        {
            "id": doc_id,
            "url": f"https://{domain}/{doc_id}",
            "url_key": f"https://{domain}/{doc_id}",
            "title": doc_id,
            "text_nfc": "body",
            "text_chars": 4,
            "content_hash": doc_id,
            "cluster_id": f"cluster:{doc_id}",
            "site_domain": domain,
            "source_id": source_id,
            "published_at": NOW,
            "fetched_at": NOW,
            "created_at": NOW,
        },
        key=["id"],
    )


def _assertion(
    conn: db.Conn,
    assertion_id: str,
    *,
    entity_id: str,
    doc_id: str,
    created_at: str,
    method: str = "extraction",
    retracted: bool = False,
) -> None:
    db.insert(
        conn,
        "assertions",
        {
            "id": assertion_id,
            "run_id": "run:evidence",
            "article_id": doc_id,
            "cluster_id": f"cluster:{doc_id}",
            "kind": "entity",
            "surface": entity_id,
            "entity_id_raw": entity_id,
            "evidence_quote": "it was named.",
            "confidence": 0.9,
            "method": method,
            "created_at": created_at,
            "retracted_at": NOW if retracted else None,
        },
    )


def _merge(conn: db.Conn, mapping_id: str, from_id: str, to_id: str) -> None:
    db.insert(
        conn,
        "entity_mappings",
        {
            "merge_id": mapping_id,
            "from_id": from_id,
            "to_id": to_id,
            "active": 1,
            "created_at": NOW,
        },
    )


def _python_fold(conn: db.Conn, mappings: dict[str, str]) -> dict[str, tuple[int, int, str]]:
    """The fold this replaced, kept here as the golden implementation.

    Deliberately a copy rather than an import: the point of a golden comparison
    is that the two are written independently, and calling the production
    helper from both sides would compare it with itself.
    """
    articles: dict[str, set[str]] = {}
    publishers: dict[str, set[str]] = {}
    newest: dict[str, str] = {}
    rows = conn.execute(
        "SELECT e.id, a.article_id, a.created_at, d.site_domain "
        "  FROM entities e "
        "  LEFT JOIN assertions a ON a.entity_id_raw = e.id AND a.retracted_at IS NULL "
        "       AND a.method != 'seed' "
        "  LEFT JOIN documents d ON d.id = a.article_id"
    )
    for r in rows:
        owner = mappings.get(r["id"], r["id"])
        if r["article_id"]:
            articles.setdefault(owner, set()).add(r["article_id"])
        if r["site_domain"]:
            publishers.setdefault(owner, set()).add(r["site_domain"])
        if r["created_at"] and r["created_at"] > newest.get(owner, ""):
            newest[owner] = r["created_at"]
    return {
        entity_id: (
            len(articles.get(entity_id, ())),
            len(publishers.get(entity_id, ())),
            newest.get(entity_id, ""),
        )
        for entity_id in sorted(set(articles) | set(publishers))
    }


def _sql_fold(conn: db.Conn, mappings: dict[str, str]) -> dict[str, tuple[int, int, str]]:
    return {
        row["id"]: (row["articles"], row["sources"], row["newest"] or "")
        for row in repo.entity_evidence(conn, mappings)
    }


def _seed_hazards(conn: db.Conn) -> None:
    """One corpus exercising all three hazards at once."""
    # A -> B -> C, a chain. Every article A and B carry belongs to C.
    for entity_id in ("company:ev-a", "company:ev-b", "company:ev-c"):
        _entity(conn, entity_id)
    _merge(conn, "map:ev-ab", "company:ev-a", "company:ev-b")
    _merge(conn, "map:ev-bc", "company:ev-b", "company:ev-c")

    # Two FEEDS, one publisher domain: one source of corroboration, not two.
    _document(conn, "doc:ev-1", "example.test", "feed_one")
    _document(conn, "doc:ev-2", "example.test", "feed_two")
    # A genuinely independent outlet.
    _document(conn, "doc:ev-3", "other.test", "feed_three")

    _assertion(conn, "as:ev-1", entity_id="company:ev-a", doc_id="doc:ev-1", created_at="t1")
    _assertion(conn, "as:ev-2", entity_id="company:ev-b", doc_id="doc:ev-2", created_at="t9")
    _assertion(conn, "as:ev-3", entity_id="company:ev-c", doc_id="doc:ev-3", created_at="t5")
    # Neither of these is evidence: seeded identity is not, and a retraction
    # removes it.
    _assertion(
        conn, "as:ev-4", entity_id="company:ev-c", doc_id="doc:ev-3", created_at="t7", method="seed"
    )
    _assertion(
        conn,
        "as:ev-5",
        entity_id="company:ev-c",
        doc_id="doc:ev-1",
        created_at="t8",
        retracted=True,
    )


def test_the_aggregate_matches_the_python_fold(conn: db.Conn) -> None:
    """⭐ The golden comparison, on the whole seeded corpus plus the hazards."""
    _seed_hazards(conn)
    mappings = resolve_mappings(conn)

    assert _sql_fold(conn, mappings) == _python_fold(conn, mappings)


def test_a_merge_chain_credits_the_final_survivor(conn: db.Conn) -> None:
    """⭐ Hazard 1. A one-hop join credits A->B->C to B, and B is not in the
    graph any more -- so the survivor looks uncorroborated and the entity that
    no longer exists looks strong."""
    _seed_hazards(conn)
    folded = _sql_fold(conn, resolve_mappings(conn))

    assert folded["company:ev-c"][0] == 3, "all three articles belong to the survivor"
    assert "company:ev-a" not in folded and "company:ev-b" not in folded, (
        "a merged-away id must carry no evidence of its own"
    )


def test_sources_are_publishers_not_feeds(conn: db.Conn) -> None:
    """⭐ Hazard 2. Two feeds from one outlet read as two independent sources,
    which promotes on corroboration that is not there."""
    _seed_hazards(conn)
    folded = _sql_fold(conn, resolve_mappings(conn))

    assert folded["company:ev-c"][1] == 2, (
        "three articles across two DOMAINS is two sources, not three"
    )


def test_the_newest_assertion_decides_dormancy(conn: db.Conn) -> None:
    """⭐ Hazard 3. `entities.last_seen` is rewritten by the very function that
    reads it, so the dormancy clock would reset every run."""
    _seed_hazards(conn)
    folded = _sql_fold(conn, resolve_mappings(conn))

    assert folded["company:ev-c"][2] == "t9", (
        "the survivor's newest assertion is the loser's, and a retracted or "
        "seeded one is not evidence at all"
    )


def test_an_entity_with_only_retracted_evidence_disappears(conn: db.Conn) -> None:
    """A retraction removes evidence; it does not leave a zero row that would
    keep a stale count alive."""
    _entity(conn, "company:ev-gone")
    _document(conn, "doc:ev-gone", "gone.test", "feed_gone")
    _assertion(
        conn,
        "as:ev-gone",
        entity_id="company:ev-gone",
        doc_id="doc:ev-gone",
        created_at="t1",
        retracted=True,
    )

    folded = _sql_fold(conn, resolve_mappings(conn))
    assert "company:ev-gone" not in folded
