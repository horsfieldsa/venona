"""Every statement the operator scripts under `scripts/` run.

WHY THEY ARE HERE AND NOT THERE. `scripts/` is the half of the tree nobody
reviews as application code, so a query written there is a query nobody looks
at -- and `lint_repositories` did not walk the directory at all, which made
that invisible rather than merely unfortunate. The rule these serve is the
read-scope seam: a query assembled by `str.format` in a script is a query
nothing can scope, and the fact that a script runs locally today is a fact
about today.

These are DIAGNOSTIC reads. They answer "what did that run cost", "what is in
the corpus", "did the envelope experiment work" -- questions asked out of band
by a person at a terminal, never on a request path. That is why they sit in
their own module rather than being scattered through the per-service ones: a
reader of `db/repositories/glossary.py` should find what the glossary serves,
not what a report script once needed.
"""

from __future__ import annotations

from venona.db import core as db
from venona.db.models import DECLARING_METHODS

# ── envelope_experiment ──────────────────────────────────────────────


def sample_documents(conn: db.Conn, limit: int) -> list[db.Row]:
    """Pinned by id order, not by recency: the arms must see the same prompt on
    every invocation, or the comparison is against noise."""
    return list(
        conn.execute(
            """
            SELECT id, title, url, publisher, published_at, text_nfc
              FROM documents
             WHERE text_chars BETWEEN 4000 AND 20000
          ORDER BY id
             LIMIT ?
            """,
            (limit,),
        ).fetchall()
    )


# ── envelope_report ──────────────────────────────────────────────────


def tool_use_shapes(conn: db.Conn, run_id: str | None) -> list[db.Row]:
    """How the model wrapped its structured output, per task.

    `run_id` is bound rather than interpolated; only the presence of the clause
    varies, which is the shape `lint_repositories` documents as acceptable.
    """
    where = "WHERE json_extract(data,'$.kind') = 'tool_use'"
    params: tuple = ()
    if run_id:
        where += " AND run_id = ?"
        params = (run_id,)
    return list(
        conn.execute(
            f"""
            SELECT json_extract(data,'$.task')                          AS task,
                   SUM(json_extract(data,'$.input') LIKE '%$PARAMETER%')   AS param,
                   SUM(json_extract(data,'$.input') LIKE '{{''response''%') AS wrapped,
                   SUM(json_extract(data,'$.tool') = 'StructuredOutput')    AS calls
              FROM run_events
              {where}
          GROUP BY task
            HAVING calls > 0
          ORDER BY calls DESC
            """,  # noqa: S608 - the only variation is a bound clause
            params,
        ).fetchall()
    )


# ── export_primer ────────────────────────────────────────────────────


def active_definitions(conn: db.Conn) -> list[db.Row]:
    return list(
        conn.execute(
            "SELECT entity_id, definition, method FROM entity_definitions "
            "WHERE status = 'active' AND definition IS NOT NULL ORDER BY entity_id"
        ).fetchall()
    )


def entity_expansions(conn: db.Conn) -> list[db.Row]:
    return list(
        conn.execute(
            "SELECT id, expansion FROM entities WHERE expansion IS NOT NULL ORDER BY id"
        ).fetchall()
    )


# ── lookup_report ────────────────────────────────────────────────────


def latest_lookup_all_run(conn: db.Conn) -> db.Row | None:
    return conn.execute(
        "SELECT id FROM runs WHERE kind = 'lookup_all' ORDER BY started_at DESC LIMIT 1"
    ).fetchone()


def run_row(conn: db.Conn, run_id: str) -> db.Row | None:
    return conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()


def stage_progress_events(conn: db.Conn, run_id: str) -> list[db.Row]:
    return list(
        conn.execute(
            "SELECT data FROM run_events WHERE run_id = ? AND type = 'stage_progress' ORDER BY seq",
            (run_id,),
        ).fetchall()
    )


def run_spend(conn: db.Conn, run_id: str) -> db.Row | None:
    return conn.execute(
        "SELECT COUNT(*) AS n, COALESCE(SUM(cost_usd), 0) AS c FROM llm_calls WHERE run_id = ?",
        (run_id,),
    ).fetchone()


def lookup_stage_counts(conn: db.Conn, run_id: str) -> db.Row | None:
    return conn.execute(
        "SELECT counts FROM run_stages WHERE run_id = ? AND stage = 'lookup'", (run_id,)
    ).fetchone()


def definitions_written_since(conn: db.Conn, since: str) -> list[db.Row]:
    """What a sweep actually wrote. Read these -- counters cannot check them."""
    return list(
        conn.execute(
            "SELECT surface, expansion, confidence, corroboration_count, grounding_url, "
            "       grounding_quote "
            "  FROM acronym_definitions "
            " WHERE expansion IS NOT NULL AND method = 'web_grounded' "
            "   AND last_seen >= ? ORDER BY surface",
            (since,),
        ).fetchall()
    )


# ── ui_smoke ─────────────────────────────────────────────────────────


def decided_verdicts(conn: db.Conn) -> list[db.Row]:
    """The curation verdicts a smoke run must restore.

    `make ui-smoke` records real verdicts on a working corpus, so it snapshots
    them first and puts them back afterwards. That backstop is why the target
    refuses to start without `VENONA_UI_SMOKE=1`.
    """
    return list(
        conn.execute(
            "SELECT normalized, decided_at FROM acronym_definitions "
            "WHERE decided_at IS NOT NULL ORDER BY normalized"
        ).fetchall()
    )


# ── graph_health ─────────────────────────────────────────────────────
#
# The corpus auditing itself. Each answers a question whose consequence a reader
# meets before anyone else does: an entity split in two halves its own evidence,
# an assertion whose endpoint is absent is counted and never shown, and a merge
# nobody can audit is a rule nobody can revise.


def live_entities(conn: db.Conn) -> list[db.Row]:
    """Every entity that still names something, with its mention count."""
    return list(
        conn.execute(
            "SELECT id, type, name, status, canonical_key, mention_count "
            "FROM entities WHERE status NOT IN ('merged', 'rejected') "
            "ORDER BY id"
        ).fetchall()
    )


def merged_but_live(conn: db.Conn) -> list[db.Row]:
    """Entities an active mapping folds away that `entities.status` still calls
    live -- the merge invariant, asked as a query.

    `merge_id` comes too, because the row is useless without it: the repair is
    an unmerge, an unmerge is keyed on the merge, and a reader who has to go
    and look it up will read the finding and do nothing.

    Two surfaces state the same fact and a reader gets whichever one their path
    consulted: anything that follows `entity_mappings` shows the survivor, and
    anything filtering on status shows the entity that was merged away. Neither
    is wrong on its own terms, which is why nothing raised for the five weeks
    three of these sat in the live corpus.
    """
    return list(
        conn.execute(
            "SELECT e.id, e.name, e.status, e.mention_count, m.to_id, m.reason, m.merge_id, "
            "       m.created_at, s.name AS to_name, "
            "       (SELECT COUNT(*) FROM assertions a "
            "        WHERE a.entity_id_raw = e.id OR a.subject_id_raw = e.id "
            "           OR a.object_id_raw = e.id) AS assertions "
            "FROM entity_mappings m "
            "JOIN entities e ON e.id = m.from_id "
            "JOIN entities s ON s.id = m.to_id "
            "WHERE m.active = 1 AND e.status <> 'merged' "
            "ORDER BY assertions DESC"
        ).fetchall()
    )


def declared_aliases(conn: db.Conn) -> list[db.Row]:
    """Alias surfaces a PERSON attached, with their owner and its type."""
    return list(
        conn.execute(
            "SELECT a.normalized, a.surface, a.entity_id, e.type "
            "FROM aliases a JOIN entities e ON e.id = a.entity_id "
            "WHERE a.added_by IN ('seed', 'manual') "
            "AND e.status NOT IN ('merged', 'rejected')"
        ).fetchall()
    )


def settled_pairs(conn: db.Conn) -> list[db.Row]:
    """Pairs a person has already ruled on, and what they said.

    A report that ignores these argues with the operator: `General Atomics` and
    its aeronautical arm read as a duplicate by name, and a curator had already
    recorded them as `part_of`. Re-raising a settled pair every audit is how a
    report teaches people to skim it.
    """
    return list(
        conn.execute(
            "SELECT existing_entity_id, proposed_entity_id, resolution "
            "FROM alias_conflicts WHERE resolved = 1 AND resolution IS NOT NULL"
        ).fetchall()
    )


def conflict_queue(conn: db.Conn) -> list[db.Row]:
    """The open queue, oldest first, with both sides' names.

    `suggestion_child` comes too, because it IS the `part_of` verdict: which of
    the two is inside the other. A report that prints the pair without it prints
    half an answer, and on the live queue three rows read the opposite way round
    from the order the names happen to fall in.
    """
    return list(
        conn.execute(
            "SELECT c.id, c.kind, c.surface, c.created_at, c.suggestion, "
            "c.suggestion_confidence, c.suggestion_child, "
            "c.existing_entity_id, c.proposed_entity_id, "
            "e.name AS a_name, e.type AS a_type, p.name AS b_name, p.type AS b_type "
            "FROM alias_conflicts c "
            "LEFT JOIN entities e ON e.id = c.existing_entity_id "
            "LEFT JOIN entities p ON p.id = c.proposed_entity_id "
            "WHERE c.resolved = 0 ORDER BY c.created_at"
        ).fetchall()
    )


def merge_cohorts(conn: db.Conn) -> list[db.Row]:
    """Merges grouped by the reason recorded on them, with how many were undone.

    The undo count is the only check on an auto-merge rule that exists: a
    curator reversing a cohort is the one signal that the rule is wrong, and an
    undone merge keeps its row with `active = 0` so the signal survives.
    """
    return list(
        conn.execute(
            "SELECT reason, count(*) AS total, "
            "sum(CASE WHEN active = 0 THEN 1 ELSE 0 END) AS undone, "
            "min(created_at) AS first_at, max(created_at) AS last_at "
            "FROM entity_mappings GROUP BY reason ORDER BY count(*) DESC"
        ).fetchall()
    )


def edge_corroboration(conn: db.Conn) -> list[db.Row]:
    """Every projected edge with its distinct publisher count.

    Counted on `documents.site_domain`, which is the definition
    `graph_service.edge_provenance` and `entity_service.neighborhood` both use.
    A report that counted articles here would disagree with the product about
    what corroboration means, which is the bug this exists to catch.
    """
    return list(
        conn.execute(
            "SELECT a.subject_id_raw, a.predicate, a.object_id_raw, "
            "count(DISTINCT d.site_domain) AS domains, count(*) AS assertions, "
            # A declared edge names no article and so counts zero domains. It is
            # not a thinly sourced edge, it is not an edge about publishers at
            # all, and leaving it in the denominator makes the single-source
            # share a measurement of how much trunk has been declared.
            "bool_or(a.method = ANY(?) AND d.site_domain IS NULL) AS declared "
            "FROM assertions a LEFT JOIN documents d ON d.id = a.article_id "
            "WHERE a.kind = 'relation' AND a.retracted_at IS NULL "
            "AND a.subject_id_raw IS NOT NULL AND a.object_id_raw IS NOT NULL "
            "GROUP BY a.subject_id_raw, a.predicate, a.object_id_raw",
            (list(DECLARING_METHODS),),
        ).fetchall()
    )


def anchor_candidates(conn: db.Conn, run_ids: tuple[str, ...]) -> list[db.Row]:
    """Active entities several publishers name, that nothing is attached to and
    nobody has declared.

    The corpus's own answer to "what should the trunk have said". An entity here
    has cleared the corroboration gate on its own, so it is not a mint anyone is
    guessing about; it is a name the reading keeps producing with no place to put
    it. Whether it BELONGS in the trunk is a judgement -- a place, an event and a
    one-off person all reach this list -- which is why this reports rows and
    declares nothing.
    """
    return list(
        conn.execute(
            """
            SELECT e.id, e.name, e.type, e.mention_count, e.source_count
              FROM entities e
             WHERE e.status = 'active'
               AND e.source_count >= 2
               AND e.id NOT IN (
                     SELECT DISTINCT entity_id_raw FROM assertions
                      WHERE run_id = ANY(?) AND entity_id_raw IS NOT NULL)
               AND NOT EXISTS (
                     SELECT 1 FROM assertions a
                      WHERE a.kind = 'relation' AND a.retracted_at IS NULL
                        AND (a.subject_id_raw = e.id OR a.object_id_raw = e.id))
          ORDER BY e.source_count DESC, e.mention_count DESC, e.id
            """,
            (list(run_ids),),
        ).fetchall()
    )


def dangling_assertion_endpoints(conn: db.Conn) -> list[db.Row]:
    """Relation assertions naming an id that is not in `entities`.

    These are the invisible half of a count: the aggregate sees them, the
    projection cannot draw them, and no `limit` reveals them. ADR 0124 recorded
    two of these surviving after grounding moved terms out of `gloss:`.
    """
    return list(
        conn.execute(
            "SELECT a.id, a.predicate, a.subject_id_raw, a.object_id_raw "
            "FROM assertions a "
            "WHERE a.kind = 'relation' AND a.retracted_at IS NULL "
            "AND a.subject_id_raw IS NOT NULL AND a.object_id_raw IS NOT NULL "
            "AND (a.subject_id_raw NOT IN (SELECT id FROM entities) "
            "  OR a.object_id_raw NOT IN (SELECT id FROM entities)) "
            "LIMIT 200"
        ).fetchall()
    )


def recent_run_health(conn: db.Conn, limit: int) -> list[db.Row]:
    """The last N runs: how each ended and what it cost."""
    return list(
        conn.execute(
            "SELECT id, kind, status, started_at, finished_at, error, cost_usd "
            "FROM runs ORDER BY started_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    )


def curate_metrics(conn: db.Conn, limit: int) -> list[db.Row]:
    """What the curate stage reported, run by run, newest first."""
    return list(
        conn.execute(
            "SELECT run_id, key, value, at FROM run_metrics WHERE stage = 'curate' "
            "ORDER BY at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    )


# ── graph_health: effectiveness ──────────────────────────────────────
#
# Whether a run found the RIGHT things, which no counter can answer. These
# assemble a sample with its evidence attached so a person can rule on it; none
# of them rules on anything. Same argument `lookup_report.py` makes: a count
# cannot tell a right answer from a wrong one.


def brief_writing_runs(conn: db.Conn, limit: int) -> list[db.Row]:
    """The last few runs that produced a brief, newest first.

    DRIVEN FROM `briefs`, not from `runs`: every row returned then has something
    to be judged on, where a join the other way would list brief-writing kinds
    that wrote no brief and leave the columns empty, which reads as a bad brief
    rather than as no brief. The join is LEFT because the seeded brief carries
    `SEED_RUN_ID`, for which no `runs` row exists.
    """
    return list(
        conn.execute(
            "SELECT b.id AS brief_id, b.date, b.title, b.generated_at, "
            "       b.run_id, r.kind, r.status, r.outcome, r.started_at, r.finished_at "
            "  FROM briefs b LEFT JOIN runs r ON r.id = b.run_id "
            " ORDER BY b.generated_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    )


def consumed_clusters(conn: db.Conn, brief_ids: list[str]) -> list[db.Row]:
    """What each brief RETRIEVED, and whether it went on to say anything about it.

    THE TWO TABLES ARE NOT THE SAME SET, and that gap is the finding.
    `brief_clusters` holds the retrieval set, written before the model has
    chosen anything (`pipeline/brief.py`), and every LATER brief excludes those
    clusters -- that is what makes it a daily brief rather than a greatest-hits
    list. `brief_items` holds what the brief actually published.

    So a cluster retrieved and not written about is SPENT: permanently excluded
    from every future brief and never shown to anybody. Nothing reports it, and
    the two counts are close enough that a widening gap looks like an ordinary
    day. `venona briefs delete` is the only thing that gives one back.
    """
    return list(
        conn.execute(
            """
            SELECT bc.brief_id, bc.rank, c.id, c.title, c.relevance_overall,
                   c.tier, c.so_what, c.profile_hooks, d.publisher, d.url,
                   (SELECT COUNT(*) FROM brief_items bi
                     WHERE bi.brief_id = bc.brief_id AND bi.cluster_id = c.id) AS items
              FROM brief_clusters bc
              JOIN clusters c ON c.id = bc.cluster_id
         LEFT JOIN documents d ON d.id = c.canonical_doc_id
             WHERE bc.brief_id = ANY(?)
          ORDER BY bc.brief_id, bc.rank
            """,
            (brief_ids,),
        ).fetchall()
    )


def hook_usage(conn: db.Conn, since: str) -> list[db.Row]:
    """Every profile phrase a score has cited, most cited first.

    Read the other way round it is the more useful list: a passage of the
    profile that appears nowhere here has never moved a single score, which
    makes it a sentence the operator believes is steering their brief and that
    is not.

    `since` IS LOAD-BEARING. `analyze.verified_hooks` did not exist before
    2026-08-27, so a cluster analysed earlier holds whatever the model returned,
    checked against nothing. Counting those would report fabricated phrases as
    verified, and -- the direction that does real damage -- let one coincidental
    hallucination cover a profile passage that has genuinely never been cited.
    """
    return list(
        conn.execute(
            """
            SELECT hook, COUNT(*) AS n, MAX(c.analyzed_at) AS last_at
              FROM clusters c,
                   LATERAL jsonb_array_elements_text(c.profile_hooks::jsonb) AS hook
             WHERE c.profile_hooks IS NOT NULL AND c.analyzed_at >= ?
          GROUP BY hook
          ORDER BY n DESC, hook
            """,
            (since,),
        ).fetchall()
    )


def minted_entities(conn: db.Conn, run_ids: list[str], limit: int) -> list[db.Row]:
    """Entities a run brought into existence, with the sentence that named them.

    ATTRIBUTED BY FIRST ASSERTION, not by `assertions.run_id` alone. Every run
    writes an entity assertion for every entity it resolves, new or not, so
    filtering assertions by run returns everything the run MENTIONED -- on a
    mature corpus almost entirely names that have existed for months. `entities`
    carries no `run_id`, and an id is minted once and never changes (C4), so the
    earliest assertion naming it is what says which run made it.

    That earliest row also supplies the surface and the quote, which is the
    question being put to the reader: is that name, in that sentence, a thing.

    A name an active mapping folds away is EXCLUDED rather than annotated.
    Somebody has already ruled on it, and a merged id keeps neither its mentions
    nor its publishers -- so every one of them sorts to the top of a sample
    ordered by fewest sightings, and the rows a reader most needs to see are
    pushed out by rows that are settled. Same reasoning as `settled_pairs`.

    Ordered by the fewest sightings first. Those are both the likeliest junk and
    the ones `curate` holds provisional indefinitely, so they are the sample
    worth a person's attention.
    """
    return list(
        conn.execute(
            """
            SELECT * FROM (
                SELECT DISTINCT ON (a.entity_id_raw)
                       a.entity_id_raw AS id, a.run_id, a.surface, a.evidence_quote,
                       e.name, e.type, e.status, e.mention_count, e.source_count,
                       e.created_at, d.publisher
                  FROM assertions a
                  JOIN entities e ON e.id = a.entity_id_raw
             LEFT JOIN documents d ON d.id = a.article_id
                 WHERE a.kind = 'entity'
                   AND e.status NOT IN ('merged', 'rejected')
                   AND NOT EXISTS (
                         SELECT 1 FROM entity_mappings m
                          WHERE m.from_id = a.entity_id_raw AND m.active = 1)
              ORDER BY a.entity_id_raw, a.created_at
            ) t
             WHERE t.run_id = ANY(?)
          ORDER BY t.source_count, t.mention_count, t.id
             LIMIT ?
            """,
            (run_ids, limit),
        ).fetchall()
    )


def relation_samples(conn: db.Conn, run_ids: list[str], limit: int) -> list[db.Row]:
    """Relations a run wrote, with both names and the quote behind them.

    Ordered by `confidence` ascending rather than drawn evenly: the model's own
    least-certain readings are where an unsupported relation is likeliest, so
    ten of those say more about the stage than ten at random. The caller groups
    what comes back by predicate, because one bad row is bad luck and four under
    the same predicate is a rule to change. Retracted rows are excluded --
    somebody has already ruled on those.
    """
    return list(
        conn.execute(
            "SELECT a.id, a.predicate, a.confidence, a.evidence_quote, a.run_id, "
            "       s.name AS subject_name, s.type AS subject_type, "
            "       o.name AS object_name, o.type AS object_type, d.publisher "
            "  FROM assertions a "
            "  JOIN entities s ON s.id = a.subject_id_raw "
            "  JOIN entities o ON o.id = a.object_id_raw "
            "  LEFT JOIN documents d ON d.id = a.article_id "
            " WHERE a.kind = 'relation' AND a.retracted_at IS NULL "
            "   AND a.run_id = ANY(?) "
            " ORDER BY a.confidence, a.id LIMIT ?",
            (run_ids, limit),
        ).fetchall()
    )


def source_stats(conn: db.Conn, run_ids: list[str]) -> list[db.Row]:
    """What each source gave each run, worst yield first.

    Rows exist only for runs that ran the FETCH stage, which is not every
    brief-writing run: a rewrite reads the corpus it already has. The caller
    says so rather than printing an empty block, because a source section with
    nothing in it reads as a broken report instead of as a run that did not go
    looking.
    """
    return list(
        conn.execute(
            "SELECT run_id, source_id, offered, capped, kept, duplicate, skipped, "
            "       fresh, stale, undated, status, error "
            "  FROM run_source_stats WHERE run_id = ANY(?) "
            " ORDER BY run_id, kept, source_id",
            (run_ids,),
        ).fetchall()
    )
