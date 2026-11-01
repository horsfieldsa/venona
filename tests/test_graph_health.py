"""The effectiveness report, measured on the four things it is easy to get wrong.

Every case here is a defect the design actually had before it shipped, found by
reading the code rather than by running it -- which is the same argument
`graph_health.py` itself makes. Three of them produce a report that looks
perfectly reasonable and is wrong:

  * attributing a mint to the run that MENTIONED the name rather than the run
    that made it, which on a mature corpus returns months-old entities;
  * counting profile phrases from before anything verified them;
  * asking whether a block of the profile contains a hook instead of asking
    which block a hook falls in, which reports both halves of every wrapped
    bullet as never cited.

The fourth is the one the section exists for: a cluster a brief retrieved and
did not write about.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import editorial
import helpers
from helpers import NOW
from venona.db import core as db
from venona.db.repositories import diagnostics as diag

ROOT = Path(__file__).resolve().parents[1]


def _report():
    """`scripts/` is not a package, so the report is loaded by path.

    Imported rather than reimplemented: the block-splitting rule and the
    verdict file's reader are the things under test, and a copy of either here
    would pass while the report kept its own behaviour.
    """
    spec = importlib.util.spec_from_file_location("graph_health", ROOT / "scripts/graph_health.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _cluster(conn: db.Conn, cluster_id: str, *, hooks: str = "[]", analyzed: str = NOW) -> None:
    helpers.document(conn, f"doc-{cluster_id}")
    db.upsert(
        conn,
        "clusters",
        {
            "id": cluster_id,
            "canonical_doc_id": f"doc-{cluster_id}",
            "title": cluster_id,
            "relevance_overall": 70,
            "tier": "worth_knowing",
            "so_what": "it matters",
            "profile_hooks": hooks,
            "analyzed_at": analyzed,
            "first_seen": NOW,
            "last_seen": NOW,
        },
        key=["id"],
    )


def _brief(conn: db.Conn, brief_id: str, *, retrieved: list[str], told: list[str]) -> None:
    db.upsert(
        conn,
        "briefs",
        {
            "id": brief_id,
            "date": "2026-09-21",
            "title": brief_id,
            "generated_at": NOW,
            "created_at": NOW,
        },
        key=["id"],
    )
    for rank, cluster_id in enumerate(retrieved, start=1):
        db.upsert(
            conn,
            "brief_clusters",
            {
                "brief_id": brief_id,
                "cluster_id": cluster_id,
                "rank": rank,
                "created_at": NOW,
            },
            key=["brief_id", "cluster_id"],
        )
    for rank, cluster_id in enumerate(told, start=1):
        db.upsert(
            conn,
            "brief_items",
            {
                "id": f"item:{brief_id}:{rank}",
                "brief_id": brief_id,
                "section": "top",
                "rank": rank,
                "cluster_id": cluster_id,
                "headline": cluster_id,
                "state": "new",
                "created_at": NOW,
            },
            key=["id"],
        )


def _entity_assertion(
    conn: db.Conn, assertion_id: str, *, entity_id: str, run_id: str, at: str
) -> None:
    helpers.document(conn, f"doc-{assertion_id}")
    db.upsert(
        conn,
        "assertions",
        {
            "id": assertion_id,
            "run_id": run_id,
            "article_id": f"doc-{assertion_id}",
            "cluster_id": f"cluster:doc-{assertion_id}",
            "kind": "entity",
            "surface": entity_id,
            "entity_id_raw": entity_id,
            "evidence_quote": "named here.",
            "confidence": 0.8,
            "method": "extraction",
            "created_at": at,
        },
        key=["id"],
    )


def test_a_cluster_the_brief_retrieved_and_never_told_is_visible(conn: db.Conn) -> None:
    """The finding the section exists for.

    Retrieval SPENDS a cluster: every later brief excludes it. So one that was
    retrieved and not written about is not waiting for tomorrow, it is gone,
    and the two counts are close enough that a widening gap reads as an
    ordinary day.
    """
    _cluster(conn, "cluster:told")
    _cluster(conn, "cluster:untold")
    _brief(
        conn,
        "brief:2026-09-21",
        retrieved=["cluster:told", "cluster:untold"],
        told=["cluster:told"],
    )
    conn.commit()

    rows = {r["id"]: r for r in diag.consumed_clusters(conn, ["brief:2026-09-21"])}
    assert rows["cluster:told"]["items"] == 1
    assert rows["cluster:untold"]["items"] == 0


def test_a_mint_belongs_to_the_run_that_first_asserted_it(conn: db.Conn) -> None:
    """Every run re-asserts every entity it resolves, new or not.

    Filtering assertions by `run_id` therefore returns what a run MENTIONED. On
    a corpus with any history that is overwhelmingly names minted months ago,
    and a reader asked to judge whether they are real would be judging the
    wrong population entirely.
    """
    helpers.entity(conn, "company:old", "Old")
    _entity_assertion(
        conn, "a-first", entity_id="company:old", run_id="run:first", at="2026-01-01T00:00:00.000Z"
    )
    _entity_assertion(
        conn, "a-later", entity_id="company:old", run_id="run:later", at="2026-09-01T00:00:00.000Z"
    )
    conn.commit()

    assert [r["id"] for r in diag.minted_entities(conn, ["run:first"], 10)] == ["company:old"]
    assert diag.minted_entities(conn, ["run:later"], 10) == []


def test_a_name_already_merged_away_is_not_offered_for_judgement(conn: db.Conn) -> None:
    """A merged id keeps neither its mentions nor its publishers, so it sorts to
    the top of a sample ordered by fewest sightings -- pushing out the rows a
    reader has not seen with rows somebody has already ruled on."""
    helpers.entity(conn, "company:folded", "Folded")
    _entity_assertion(conn, "a-folded", entity_id="company:folded", run_id="run:one", at=NOW)
    db.upsert(
        conn,
        "entity_mappings",
        {
            "from_id": "company:folded",
            "to_id": "company:survivor",
            "merge_id": "merge:1",
            "active": 1,
            "reason": "auto: same name",
            "created_at": NOW,
        },
        key=["from_id", "merge_id"],
    )
    conn.commit()

    assert diag.minted_entities(conn, ["run:one"], 10) == []


def test_hooks_from_before_anything_verified_them_are_not_counted(conn: db.Conn) -> None:
    """`verified_hooks` landed on 2026-08-27. A cluster analysed earlier holds
    whatever the model returned, checked against nothing -- so counting it would
    present a fabricated phrase as verified, and could let one coincidence cover
    a passage of the profile nothing has really cited."""
    _cluster(conn, "cluster:old", hooks='["invented phrase"]', analyzed="2026-08-01T00:00:00.000Z")
    _cluster(conn, "cluster:new", hooks='["real phrase"]', analyzed="2026-09-01T00:00:00.000Z")
    conn.commit()

    assert [r["hook"] for r in diag.hook_usage(conn, "2026-08-27")] == ["real phrase"]


def test_a_hook_spanning_a_wrapped_bullet_cites_that_bullet() -> None:
    """The check is 'which block does this hook fall in', not 'does this block
    contain a hook'.

    `verified_hooks` collapses whitespace across the whole profile before
    matching, so a phrase copied out of a wrapped bullet exists in neither of
    the source lines it straddles. Asked the second way, both halves of every
    wrapped bullet report as never cited, and those are the substantive ones.
    """
    report = _report()
    profile = (
        "## Heading\n\n- Money and acquisition movement,\n  contracts and awards"
        "\n\n- Something else entirely"
    )
    hook = "Money and acquisition movement, contracts and awards"
    uncited = report.uncited_blocks(profile, [hook])

    assert uncited == ["- Something else entirely"]


def test_a_heading_is_never_reported_as_uncited() -> None:
    """Nothing can cite a heading, so its absence says nothing about the
    profile. Left in, headings are most of the list and the one passage that IS
    a finding reads as more of the same."""
    report = _report()
    uncited = report.uncited_blocks("# Title\n\n---\n\n- A real claim about the domain", [])

    assert uncited == ["- A real claim about the domain"]


def test_the_report_and_the_suite_read_the_same_verdicts() -> None:
    """Two readers of one file, which is the divergence the skill's own method
    is built on asking about.

    The suite's loader validates; the report's is four lines and cannot, because
    a report that needed the test package to start is a report nobody runs. They
    are allowed to differ in what they check and not in what they find.
    """
    report = _report()

    assert set(report.ruled_subjects()) == {case.subject for case in editorial.load()}
