"""Deduplication and clustering.

Two layers, both pure Python, no model calls and no embeddings:

  1. **URL key** -- catches the same article arriving from two places, and the
     same article arriving again tomorrow. This is why a second consecutive run
     produces zero new documents.
  2. **Content hash** -- catches wire reprints published under different URLs by
     different outlets.

A third SimHash layer was designed and then dropped: at Hamming <= 3 on a 64-bit
hash it requires ~95% shingle overlap, which layer 2 already catches, and
catching genuinely re-led stories needs distance 8-16, which breaks the banding
scheme. The `documents.simhash` column is reserved so adding it later is not a
migration. See docs/adr/.

CLUSTERS, not documents, are the downstream unit. A story carried by five
outlets is one brief item whose salience signal is the pickup count -- counting
documents would just let wire reprints dominate the brief.
"""

from __future__ import annotations

from dataclasses import dataclass

from venona import timeutil
from venona.db import core as db
from venona.db.repositories import pipeline_dedup as repo
from venona.pipeline.normalize import Document, title_key


@dataclass
class DedupOutcome:
    document: Document
    cluster_id: str
    is_new: bool
    duplicate_of: str | None = None
    reason: str | None = None  # url_key | content_hash | title_date


def _existing_by_url(conn: db.Conn, url_key: str) -> db.Row | None:
    return repo.select_documents_by_url_key(conn, url_key).fetchone()


def _existing_by_content(conn: db.Conn, content_hash: str) -> db.Row | None:
    return repo.select_documents_by_content_hash(conn, content_hash).fetchone()


def _existing_by_title(conn: db.Conn, title: str, published_at: str | None) -> db.Row | None:
    """Last resort for syndicated copies that were lightly re-edited.

    Requires an exact normalised-title match AND both dates present within the
    same ~3 days. Without the date guard this would merge a program's annual
    milestone coverage across years.
    """
    if not published_at:
        return None
    key = title_key(title)
    if len(key) < 25:  # short titles collide too easily
        return None

    # The window is computed in PYTHON and compared as STRINGS, where this used
    # SQLite's `ABS(JULIANDAY(a) - JULIANDAY(b)) <= 3`. Three reasons, in order of
    # how badly the obvious translation fails:
    #
    #   1. `published_at` is TEXT. Casting it to timestamptz makes a malformed
    #      row an ERROR that takes the run down, where `JULIANDAY` returned NULL
    #      and the row simply failed the comparison. `timeutil.parse` is
    #      deliberately tolerant for the same reason, so the guard is here.
    #   2. Postgres has no `ABS()` over intervals, so the direct form does not
    #      even compile.
    #   3. `published_at` is ISO-8601 UTC with a `Z` and fixed width, so lexical
    #      order IS chronological order -- and a string BETWEEN can use
    #      `ix_documents_published`, which a per-row function call cannot.
    #
    # No wall clock is read: both bounds derive from the caller's timestamp, so
    # this stays deterministic under a FrozenClock.
    if timeutil.parse(published_at) is None:
        return None
    window_hours = 3 * 24
    earliest = timeutil.cutoff_seconds(published_at, window_hours)
    latest = timeutil.cutoff_seconds(published_at, -window_hours)

    for row in repo.select_documents(conn, earliest, latest):
        if title_key(row["title"]) == key:
            return row
    return None


def classify(conn: db.Conn, doc: Document) -> DedupOutcome:
    """Decide whether `doc` is new, and which cluster it belongs to."""
    hit = _existing_by_url(conn, doc.url_key)
    if hit:
        return DedupOutcome(
            document=doc,
            cluster_id=hit["cluster_id"],
            is_new=False,
            duplicate_of=hit["id"],
            reason="url_key",
        )

    hit = _existing_by_content(conn, doc.content_hash)
    if hit:
        # Same text, different URL: a syndicated reprint. New document row (it
        # is a real, separately-addressable article) but the SAME cluster.
        return DedupOutcome(
            document=doc,
            cluster_id=hit["cluster_id"],
            is_new=True,
            duplicate_of=hit["id"],
            reason="content_hash",
        )

    hit = _existing_by_title(conn, doc.title, doc.published_at)
    if hit:
        return DedupOutcome(
            document=doc,
            cluster_id=hit["cluster_id"],
            is_new=True,
            duplicate_of=hit["id"],
            reason="title_date",
        )

    return DedupOutcome(document=doc, cluster_id=f"cluster:{doc.id.split(':', 1)[1]}", is_new=True)


def persist(
    conn: db.Conn,
    outcome: DedupOutcome,
    *,
    run_id: str,
    now: str,
    sighting_id: str,
) -> None:
    """Write the document (if new) and always write a sighting.

    The sighting is recorded even for a duplicate: "picked up by five outlets"
    is the salience signal, and it is only observable if every arrival is
    logged.
    """
    doc = outcome.document

    if outcome.is_new:
        db.upsert(
            conn,
            "documents",
            {
                "id": doc.id,
                "run_id": run_id,
                "source_id": doc.source_id,
                "url": doc.url,
                "url_key": doc.url_key,
                "title": doc.title,
                "publisher": doc.publisher,
                "text_nfc": doc.text_nfc,
                "text_chars": doc.text_chars,
                "excerpt": doc.excerpt,
                "published_at": doc.published_at,
                "date_confidence": doc.date_confidence,
                "fetched_at": now,
                "content_hash": doc.content_hash,
                "site_domain": doc.site_domain,
                "cluster_id": outcome.cluster_id,
                "duplicate_of": outcome.duplicate_of,
                "paywalled": int(doc.paywalled),
                "created_at": now,
            },
            key=["id"],
        )

    db.upsert(
        conn,
        "sightings",
        {
            "id": sighting_id,
            "cluster_id": outcome.cluster_id,
            "document_id": doc.id,
            "source_id": doc.source_id,
            "run_id": run_id,
            "seen_at": now,
        },
        key=["id"],
    )


def take(ctx, doc: Document) -> DedupOutcome:
    """Classify `doc` and write it, in the one order that is ever correct.

    Three callers arrive here by different roads -- the fetch stage, the
    research lane and a URL a reader pasted -- and every one of them wants the
    same two statements with the same three run-scoped values behind them. Two
    of the three are the determinism knobs (`ctx.clock`, `ctx.ids`), which is
    what makes a fourth caller assembling this by hand a quiet way to lose
    offline replay rather than an obvious one.

    Everything ABOVE this line legitimately differs per caller: what counts as
    a candidate, which fields `build_document` gets, and what a `None` document
    means. That part is not one thing and is not treated as one.
    """
    outcome = classify(ctx.db, doc)
    persist(
        ctx.db,
        outcome,
        run_id=ctx.run_id,
        now=ctx.clock.now(),
        sighting_id=ctx.ids.uuid7(),
    )
    return outcome


def refresh_clusters(
    conn: db.Conn,
    *,
    now: str,
    source_order: dict[str, int] | None = None,
    cluster_ids: list[str] | None = None,
) -> int:
    """Recompute cluster rows from their member documents.

    The CANONICAL DOCUMENT is the earliest `published_at`, tie-broken by source
    declaration order. That rule is frozen (contract C7 notes) because analyze
    runs on the canonical document's text, and a wobbling choice would make runs
    non-reproducible.

    Documents with no date sort last: an undated stub should never become the
    canonical text for a cluster that has a properly dated article in it.

    `cluster_ids` SCOPES the recompute to the clusters a caller actually touched.
    A cluster's canonical document is decided by its own members and by nothing
    else, so recomputing one is independent of every other -- which makes the
    unscoped version pure waste rather than extra safety. Unscoped, adding ONE
    article by hand re-derived every cluster in the corpus, so the cost of the
    "add article" button grew by one cluster a day forever while the work it
    represents stayed the same size.

    `None` keeps the whole-corpus pass, which is what a deliberate rebuild wants.

    Returns how many clusters were recomputed. A caller reporting a corpus total
    must not read this when it passes `cluster_ids` -- see `fetch.py`, where the
    docs-per-cluster ratio needs the corpus figure and says so.
    """
    order = source_order or {}
    if cluster_ids is None:
        cluster_ids = [row["cluster_id"] for row in repo.select_documents_2(conn)]

    for cluster_id in cluster_ids:
        members = repo.select_documents_by_cluster_id(conn, cluster_id).fetchall()
        if not members:
            continue

        def sort_key(row: db.Row) -> tuple:
            has_date = 0 if row["published_at"] else 1
            return (
                has_date,
                row["published_at"] or "9999",
                order.get(row["source_id"], 999),
                row["id"],
            )

        canonical = sorted(members, key=sort_key)[0]
        dated = [m["published_at"] for m in members if m["published_at"]]

        existing = repo.select_clusters_by_id(conn, cluster_id).fetchone()

        row = {
            "id": cluster_id,
            "canonical_doc_id": canonical["id"],
            "title": canonical["title"],
            "published_at": min(dated) if dated else None,
            "first_seen": existing["first_seen"] if existing else now,
            "last_seen": now,
        }
        # A different canonical document means different TEXT, and the acronyms
        # stage has not read it. Clearing its scan marker puts the cluster back
        # in scope; leaving the marker set would exclude the replacement text
        # from every future scan, silently.
        if existing is not None and existing["canonical_doc_id"] != canonical["id"]:
            row["acronyms_scanned_at"] = None

        db.upsert(conn, "clusters", row, key=["id"])

    return len(cluster_ids)


def cluster_sighting_counts(conn: db.Conn) -> dict[str, int]:
    """Distinct sources per cluster -- the salience signal."""
    return {row["cluster_id"]: row["n"] for row in repo.select_sightings(conn)}
