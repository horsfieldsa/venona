"""The acronyms stage reads each document once, and concludes the same things.

It used to load every analyzed document's full text on every run and reprocess
all of it -- to detect surfaces, to count sightings, and to learn which
capitalised tokens are ordinary English. What a document says about a surface
cannot change once it has been read, so that is O(corpus) nightly work whose
answer is already known, growing for as long as the instance runs.

Scoping is only safe because two things the run cannot recompute from the
documents it reads are written down: the sighting totals, and the ordinary-word
document frequencies. These tests are about those two, and about the one case
where a scoped scan could disagree with a whole-corpus one.
"""

from __future__ import annotations

import pytest
from venona.db import core as db
from venona.pipeline import acronyms
from venona.pipeline.types import FrozenClock, RunContext, SeededIdGen
from venona.settings import get_settings

NOW = "2026-08-26T00:00:00.000Z"


def _ctx(conn: db.Conn) -> RunContext:
    return RunContext(
        run_id="run:scope",
        db=conn,
        llm=None,
        settings=get_settings(),
        clock=FrozenClock(),
        ids=SeededIdGen(0),
    )


def _document(conn: db.Conn, doc_id: str, text: str) -> str:
    """An analyzed cluster the acronyms stage will pick up."""
    cluster_id = f"cluster:{doc_id}"
    db.upsert(
        conn,
        "documents",
        {
            "id": doc_id,
            "url": f"https://example.test/{doc_id}",
            "url_key": f"https://example.test/{doc_id}",
            "title": "A headline",
            "text_nfc": text,
            "text_chars": len(text),
            "content_hash": doc_id,
            "cluster_id": cluster_id,
            "published_at": NOW,
            "fetched_at": NOW,
            "created_at": NOW,
        },
        key=["id"],
    )
    db.upsert(
        conn,
        "clusters",
        {
            "id": cluster_id,
            "canonical_doc_id": doc_id,
            "title": "A headline",
            "published_at": NOW,
            "analyzed_at": NOW,
            "first_seen": NOW,
            "last_seen": NOW,
        },
        key=["id"],
    )
    return cluster_id


async def _run(conn: db.Conn) -> acronyms.AcronymReport:
    return await acronyms.run_acronyms(_ctx(conn), allow_web=False)


@pytest.mark.anyio
async def test_a_second_run_over_an_unchanged_corpus_reads_no_documents(conn: db.Conn) -> None:
    """⭐ The whole point. The first run reads the corpus; the second reads the
    documents that arrived since, which on an unchanged corpus is none."""
    _document(conn, "doc:scope-a", "The QWERTY programme was announced. QWERTY is central.")

    first = await _run(conn)
    assert first.documents_scanned > 0, "the first run must actually read the corpus"

    second = await _run(conn)
    assert second.documents_scanned == 0, "an unchanged corpus must not be re-read"


@pytest.mark.anyio
async def test_the_totals_survive_a_run_that_read_nothing(conn: db.Conn) -> None:
    """⭐ The counts are what the tier gates decide on, so a scoped run that
    reads no documents must still see every surface the corpus has ever shown.

    An in-memory accumulator rebuilt per run would report zero here, and every
    surface would silently stop being a candidate.
    """
    _document(conn, "doc:scope-b", "ZQXJ matters. ZQXJ recurs. ZQXJ again.")

    first = await _run(conn)
    second = await _run(conn)

    assert second.surfaces_seen == first.surfaces_seen, (
        "the corpus-wide totals must not shrink because tonight brought no news"
    )
    row = conn.execute(
        "SELECT sighting_count, article_count FROM surface_sightings WHERE normalized = 'zqxj'"
    ).fetchone()
    assert row is not None and row["sighting_count"] >= 3
    assert row["article_count"] == 1, "one document must count once, however often it is scanned"


@pytest.mark.anyio
async def test_a_new_document_adds_to_the_totals_rather_than_replacing_them(
    conn: db.Conn,
) -> None:
    """Sightings accumulate across scans; a run only ever knows about its own."""
    _document(conn, "doc:scope-c", "VBNM is discussed. VBNM continues.")
    await _run(conn)
    before = conn.execute(
        "SELECT sighting_count, article_count FROM surface_sightings WHERE normalized = 'vbnm'"
    ).fetchone()

    _document(conn, "doc:scope-d", "VBNM appears again in a second article.")
    await _run(conn)
    after = conn.execute(
        "SELECT sighting_count, article_count FROM surface_sightings WHERE normalized = 'vbnm'"
    ).fetchone()

    assert after["sighting_count"] > before["sighting_count"]
    assert after["article_count"] == before["article_count"] + 1, (
        "the second article must be counted once, added to the first"
    )


@pytest.mark.anyio
async def test_ordinary_word_frequency_is_cumulative(conn: db.Conn) -> None:
    """⭐ The VICTOR case, which is why the frequency is persisted at all.

    One document using a capitalised token as an ordinary word is a
    coincidence; recurrence is what marks a shouted English word. A scoped run
    can only see tonight's documents, so a nightly recount would decide from a
    sample of one -- and suppressing an acronym on a coincidence is exactly the
    failure this threshold exists to avoid.
    """
    # Title-case or lower-case is what counts as ordinary: mixed case is
    # deliberately not evidence, because `DoD` is a styling of an acronym.
    _document(conn, "doc:scope-e", "The Vista was described here.")
    await _run(conn)
    _document(conn, "doc:scope-f", "The Vista appeared again elsewhere.")
    await _run(conn)

    row = conn.execute("SELECT doc_count FROM ordinary_word_df WHERE word = 'VISTA'").fetchone()
    assert row is not None and row["doc_count"] == 2, (
        "the frequency must span both scans, not just the one that saw the word last"
    )


@pytest.mark.anyio
async def test_a_replaced_canonical_document_is_read_again(conn: db.Conn) -> None:
    """⭐ The one way a scoped scan could quietly go blind.

    A cluster's canonical document is the earliest published member, so a
    later-arriving earlier article replaces it -- and the text the stage read
    is then no longer the text the cluster stands for. `refresh_clusters`
    clears the marker for exactly that case.
    """
    from venona.pipeline import dedup

    cluster_id = _document(conn, "doc:scope-g", "PLKJ is mentioned.")
    await _run(conn)
    assert (await _run(conn)).documents_scanned == 0

    db.upsert(
        conn,
        "documents",
        {
            "id": "doc:scope-h",
            "url": "https://example.test/scope-h",
            "url_key": "https://example.test/scope-h",
            "title": "An earlier headline",
            "text_nfc": "PLKJ was mentioned much earlier, and so was WXYZ.",
            "text_chars": 48,
            "content_hash": "doc:scope-h",
            "cluster_id": cluster_id,
            "published_at": "2026-08-01T00:00:00.000Z",
            "fetched_at": NOW,
            "created_at": NOW,
        },
        key=["id"],
    )
    dedup.refresh_clusters(conn, now=NOW, cluster_ids=[cluster_id])

    assert (await _run(conn)).documents_scanned == 1, (
        "a cluster whose canonical text changed must come back into scope"
    )
    assert (
        conn.execute("SELECT 1 FROM surface_sightings WHERE normalized = 'wxyz'").fetchone()
        is not None
    ), "the replacement text's surfaces must be counted"


@pytest.mark.anyio
async def test_a_surface_that_becomes_an_ordinary_word_leaves_the_totals(conn: db.Conn) -> None:
    """⭐ Where a scoped scan would otherwise disagree with a whole-corpus one.

    The whole-corpus pass re-decided every surface each night against the
    current frequencies. A scoped scan cannot, so it would keep counting
    sightings recorded back when the token still looked like an acronym -- and
    a surface `detect` suppresses today would sit in the totals forever on
    evidence gathered before the rule applied to it.
    """
    settings = get_settings()
    threshold = settings.pipeline.acronym_ordinary_min_docs

    _document(conn, "doc:scope-i", "VISTA is named here. VISTA recurs. VISTA again.")
    await _run(conn)
    assert (
        conn.execute("SELECT 1 FROM surface_sightings WHERE normalized = 'vista'").fetchone()
        is not None
    ), "the surface must be counted first, or the retirement below proves nothing"

    # Enough documents using it as an ordinary word to cross the threshold.
    for i in range(threshold + 1):
        _document(conn, f"doc:scope-ordinary-{i}", f"The Vista was mentioned in report {i}.")
    await _run(conn)

    assert (
        conn.execute("SELECT 1 FROM surface_sightings WHERE normalized = 'vista'").fetchone()
        is None
    ), "a token that has become an ordinary word must leave the totals, not linger in them"
