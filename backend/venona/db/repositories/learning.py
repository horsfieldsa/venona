"""Every statement the learning feature runs (epic O, docs/adr/0157).

Two properties this module is the enforcement point for:

  * **Every per-user read and write is keyed on `user_id`.** The privacy
    stance -- a learner's rows are visible to that learner alone -- is only as
    strong as the queries, so no function here reads `learning_cards` or
    `learning_reviews` without a `user_id` parameter.
  * **Cards are derived, so the deck queries here select SOURCE rows.** The
    card-key namespaces (`card:def:` / `card:gloss:` / `card:rel:` /
    `card:q:`) are assembled in SQL by concatenation, which is what lets the
    memory listing join a key back to the row it renders.

See `db/repositories/__init__.py` for the rules.
"""

from __future__ import annotations

from venona.db import core as db

# ── FSRS state and the review log ────────────────────────────────────────


def card_state(conn: db.Conn, *, user_id: str, card_key: str) -> db.Row | None:
    return conn.execute(
        "SELECT * FROM learning_cards WHERE user_id = ? AND card_key = ?",
        (user_id, card_key),
    ).fetchone()


def card_state_for_update(conn: db.Conn, *, user_id: str, card_key: str) -> db.Row | None:
    """The row, locked, or None.

    Grading is a read-then-write -- load the FSRS state, advance it, write it
    back -- and at READ COMMITTED two rapid grades of the same card would both
    read the same state and one advance would be lost. Same primitive as
    `access.lock_for_decision`.
    """
    return conn.execute(
        "SELECT * FROM learning_cards WHERE user_id = ? AND card_key = ? FOR UPDATE",
        (user_id, card_key),
    ).fetchone()


def upsert_card_state(
    conn: db.Conn,
    *,
    user_id: str,
    card_key: str,
    state: str,
    stability: float,
    difficulty: float,
    due: str,
    last_review: str,
    reps: int,
    lapses: int,
    now: str,
) -> None:
    conn.execute(
        "INSERT INTO learning_cards (user_id, card_key, state, stability, difficulty,"
        "                            due, last_review, reps, lapses, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT (user_id, card_key) DO UPDATE SET "
        "  state = EXCLUDED.state, stability = EXCLUDED.stability, "
        "  difficulty = EXCLUDED.difficulty, due = EXCLUDED.due, "
        "  last_review = EXCLUDED.last_review, reps = EXCLUDED.reps, "
        "  lapses = EXCLUDED.lapses, updated_at = EXCLUDED.updated_at",
        (user_id, card_key, state, stability, difficulty, due, last_review, reps, lapses, now, now),
    )


def insert_review(
    conn: db.Conn,
    *,
    id: str,
    user_id: str,
    card_key: str,
    rating: int,
    review_kind: str,
    format: str,
    elapsed_days: float,
    due_at: str | None,
    now: str,
) -> None:
    conn.execute(
        "INSERT INTO learning_reviews (id, user_id, card_key, rating, review_kind,"
        "                              format, elapsed_days, due_at, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (id, user_id, card_key, rating, review_kind, format, elapsed_days, due_at, now),
    )


def due_cards(conn: db.Conn, *, user_id: str, now: str, limit: int) -> list[db.Row]:
    """Oldest-due first; `card_key` breaks ties so paging never straddles."""
    return list(
        conn.execute(
            "SELECT * FROM learning_cards WHERE user_id = ? AND due <= ? "
            "ORDER BY due, card_key LIMIT ?",
            (user_id, now, limit),
        ).fetchall()
    )


# ── the deck: what could become a card ───────────────────────────────────

#: Resolves an assertion endpoint through an active merge mapping, one hop.
#: Chained merges resolve fully in the graph projection; here one hop is the
#: deliberate approximation -- the ranking this feeds tolerates it and the
#: query stays a join instead of a recursion.
_RESOLVED_RELATIONS = """
    SELECT COALESCE(ms.to_id, a.subject_id_raw) AS s,
           COALESCE(mo.to_id, a.object_id_raw) AS o
      FROM assertions a
      LEFT JOIN entity_mappings ms
             ON ms.from_id = a.subject_id_raw AND ms.active = 1
      LEFT JOIN entity_mappings mo
             ON mo.from_id = a.object_id_raw AND mo.active = 1
     WHERE a.kind = 'relation' AND a.retracted_at IS NULL
"""


def new_def_candidates(conn: db.Conn, *, user_id: str, limit: int) -> list[db.Row]:
    """Defined, active entities the user has never graded, most central first.

    Degree is computed here over non-retracted assertions rather than asked of
    Neo4j: the graph is a rebuildable projection and the deck must not go dark
    with it. The tiebreak chain ends in `id` so the order is total.
    """
    return list(
        conn.execute(
            f"""
            WITH deg AS (
                SELECT e_id, COUNT(*) AS degree FROM (
                    SELECT s AS e_id FROM ({_RESOLVED_RELATIONS}) r1
                    UNION ALL
                    SELECT o AS e_id FROM ({_RESOLVED_RELATIONS}) r2
                ) endpoints GROUP BY e_id
            )
            SELECT 'card:def:' || e.id AS card_key, e.id AS entity_id, e.type,
                   COALESCE(deg.degree, 0) AS degree
              FROM entities e
              JOIN entity_definitions ed ON ed.entity_id = e.id
              LEFT JOIN deg ON deg.e_id = e.id
              LEFT JOIN learning_cards lc
                     ON lc.user_id = ? AND lc.card_key = 'card:def:' || e.id
             WHERE e.status = 'active' AND ed.status = 'active'
               AND ed.definition IS NOT NULL AND lc.card_key IS NULL
             ORDER BY degree DESC, e.mention_count DESC, e.relevance DESC, e.id
             LIMIT ?
            """,
            (user_id, limit),
        ).fetchall()
    )


def new_gloss_candidates(conn: db.Conn, *, user_id: str, limit: int) -> list[db.Row]:
    """Glosses the user has never graded -- only those that did NOT resolve to
    an entity, because a resolved acronym is already the entity's card and two
    schedules for one fact would double-drill it."""
    return list(
        conn.execute(
            "SELECT 'card:gloss:' || g.id AS card_key, g.id AS gloss_id "
            "  FROM acronym_definitions g "
            "  LEFT JOIN learning_cards lc "
            "         ON lc.user_id = ? AND lc.card_key = 'card:gloss:' || g.id "
            " WHERE g.status = 'active' AND g.expansion IS NOT NULL "
            "   AND g.entity_id IS NULL AND lc.card_key IS NULL "
            " ORDER BY g.article_count DESC, g.sighting_count DESC, g.id "
            " LIMIT ?",
            (user_id, limit),
        ).fetchall()
    )


def new_relation_candidates(conn: db.Conn, *, user_id: str, limit: int) -> list[db.Row]:
    """Curator-asserted relations the user has never graded. `run_id LIKE
    'curation:%'` selects the runs a person caused (the `curated_relations`
    rule); both endpoints must still be active or the question would name a
    ghost."""
    return list(
        conn.execute(
            "SELECT 'card:rel:' || a.id AS card_key, a.id AS assertion_id "
            "  FROM assertions a "
            "  LEFT JOIN learning_cards lc "
            "         ON lc.user_id = ? AND lc.card_key = 'card:rel:' || a.id "
            " WHERE a.run_id LIKE 'curation:%' AND a.retracted_at IS NULL "
            "   AND a.kind = 'relation' AND lc.card_key IS NULL "
            "   AND (SELECT status FROM entities WHERE id = a.subject_id_raw) = 'active' "
            "   AND (SELECT status FROM entities WHERE id = a.object_id_raw) = 'active' "
            " ORDER BY a.confidence DESC, a.id "
            " LIMIT ?",
            (user_id, limit),
        ).fetchall()
    )


def new_question_candidates(conn: db.Conn, *, user_id: str, limit: int) -> list[db.Row]:
    """Approved generated questions the user has never met, oldest first --
    the queue a reviewer feeds should drain in the order it was blessed."""
    return list(
        conn.execute(
            "SELECT 'card:q:' || q.id AS card_key, q.id AS question_id "
            "  FROM llm_questions q "
            "  LEFT JOIN learning_cards lc "
            "         ON lc.user_id = ? AND lc.card_key = 'card:q:' || q.id "
            " WHERE q.status = 'active' AND lc.card_key IS NULL "
            " ORDER BY q.created_at, q.id "
            " LIMIT ?",
            (user_id, limit),
        ).fetchall()
    )


# ── derivation sources ───────────────────────────────────────────────────


def relation_source(conn: db.Conn, *, assertion_id: str) -> db.Row | None:
    """One curated assertion with both endpoint names and types, servable only
    while both endpoints are active and the claim stands."""
    return conn.execute(
        "SELECT a.id, a.predicate, a.evidence_quote, "
        "       s.id AS subject_id, s.name AS subject_name, s.type AS subject_type, "
        "       o.id AS object_id, o.name AS object_name, o.type AS object_type "
        "  FROM assertions a "
        "  JOIN entities s ON s.id = a.subject_id_raw AND s.status = 'active' "
        "  JOIN entities o ON o.id = a.object_id_raw AND o.status = 'active' "
        " WHERE a.id = ? AND a.retracted_at IS NULL AND a.run_id LIKE 'curation:%'",
        (assertion_id,),
    ).fetchone()


def def_status(conn: db.Conn, *, entity_id: str) -> str | None:
    row = conn.execute(
        "SELECT status FROM entity_definitions WHERE entity_id = ?", (entity_id,)
    ).fetchone()
    return row["status"] if row else None


def question_source(conn: db.Conn, *, question_id: str) -> db.Row | None:
    """An approved question, servable; `proposed`/`rejected`/`retired` rows
    derive nothing, which is what keeps the review gate real."""
    return conn.execute(
        "SELECT * FROM llm_questions WHERE id = ? AND status = 'active'",
        (question_id,),
    ).fetchone()


def gloss_distractor_expansions(conn: db.Conn, *, exclude_id: str, limit: int) -> list[str]:
    """Other glosses' expansions, for multiple-choice distractors. Bounded and
    ordered here; the deterministic pick happens in the service."""
    return [
        r["expansion"]
        for r in conn.execute(
            "SELECT expansion FROM acronym_definitions "
            " WHERE status = 'active' AND expansion IS NOT NULL AND id != ? "
            " ORDER BY article_count DESC, id LIMIT ?",
            (exclude_id, limit),
        )
    ]


def distractor_names(conn: db.Conn, *, entity_type: str, exclude_id: str, limit: int) -> list[str]:
    """Same-type active entity names for multiple-choice distractors. The pool
    is bounded and ordered here; the deterministic pick happens in the service
    where the seed lives."""
    return [
        r["name"]
        for r in conn.execute(
            "SELECT name FROM entities "
            " WHERE type = ? AND status = 'active' AND id != ? "
            " ORDER BY mention_count DESC, id LIMIT ?",
            (entity_type, exclude_id, limit),
        )
    ]


# ── progress: everything derives from the review log ─────────────────────


def daily_series(conn: db.Conn, *, user_id: str, since: str) -> list[db.Row]:
    """Reviews per day with the counts retention is computed from, newest
    first. The day is the first ten characters of ISO text -- the
    `metrics.cost_by_day` argument: the column is not a timestamp and casting
    would blind the index. `since` is computed by the CALLER; this package may
    not ask what time it is."""
    return list(
        conn.execute(
            "SELECT substr(created_at, 1, 10) AS day, "
            "       COUNT(*) AS reviews, "
            "       COUNT(*) FILTER (WHERE review_kind != 'new') AS graded, "
            "       COUNT(*) FILTER (WHERE review_kind != 'new' AND rating = 1) AS lapsed "
            "  FROM learning_reviews "
            " WHERE user_id = ? AND created_at >= ? "
            " GROUP BY substr(created_at, 1, 10) "
            " ORDER BY day DESC",
            (user_id, since),
        ).fetchall()
    )


def review_days(conn: db.Conn, *, user_id: str, limit: int) -> list[str]:
    """Distinct study days, newest first -- the streak is walked from these."""
    return [
        r["day"]
        for r in conn.execute(
            "SELECT DISTINCT substr(created_at, 1, 10) AS day "
            "  FROM learning_reviews WHERE user_id = ? "
            " ORDER BY day DESC LIMIT ?",
            (user_id, limit),
        )
    ]


def reviews_since(conn: db.Conn, *, user_id: str, since: str) -> db.Row:
    """Today's totals (the caller passes the UTC midnight floor)."""
    return conn.execute(
        "SELECT COUNT(*) AS reviews, "
        "       COUNT(*) FILTER (WHERE review_kind = 'new') AS new_cards "
        "  FROM learning_reviews WHERE user_id = ? AND created_at >= ?",
        (user_id, since),
    ).fetchone()


def deck_totals_by_type(conn: db.Conn) -> list[db.Row]:
    """How many def-cards the corpus offers per entity type -- the coverage
    denominator. Whole-corpus, not per-user: the deck is shared; only progress
    against it is private."""
    return list(
        conn.execute(
            "SELECT e.type, COUNT(*) AS total "
            "  FROM entities e JOIN entity_definitions ed ON ed.entity_id = e.id "
            " WHERE e.status = 'active' AND ed.status = 'active' AND ed.definition IS NOT NULL "
            " GROUP BY e.type",
        ).fetchall()
    )


def started_by_type(conn: db.Conn, *, user_id: str, strong_stability: float) -> list[db.Row]:
    """The user's def-cards per entity type, with how many sit above the
    caller's stability bar ('strong' is a presentation threshold, so the
    service owns the number)."""
    return list(
        conn.execute(
            "SELECT e.type, COUNT(*) AS started, "
            "       COUNT(*) FILTER (WHERE lc.stability >= ?) AS strong "
            "  FROM learning_cards lc "
            "  JOIN entities e ON 'card:def:' || e.id = lc.card_key "
            " WHERE lc.user_id = ? AND e.status = 'active' "
            " GROUP BY e.type",
            (strong_stability, user_id),
        ).fetchall()
    )


def gloss_deck_total(conn: db.Conn) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM acronym_definitions "
        " WHERE status = 'active' AND expansion IS NOT NULL AND entity_id IS NULL",
    ).fetchone()
    return int(row["n"])


def gloss_started(conn: db.Conn, *, user_id: str, strong_stability: float) -> db.Row:
    return conn.execute(
        "SELECT COUNT(*) AS started, "
        "       COUNT(*) FILTER (WHERE stability >= ?) AS strong "
        "  FROM learning_cards "
        " WHERE user_id = ? AND card_key LIKE 'card:gloss:%'",
        (strong_stability, user_id),
    ).fetchone()


#: One row per held card, joined back to whatever it renders. COALESCE order
#: matches the namespaces; `detail` is the MEANING a reader wants beside a
#: term -- the definition, the expansion, the relationship read as words, or
#: the question's answer. A dormant card whose source vanished falls through
#: to its bare key, which the service labels rather than hiding -- the state
#: is real even when the fact is gone.
_HELD_CARDS = """
    SELECT lc.card_key, lc.state, lc.stability, lc.difficulty, lc.due,
           lc.last_review, lc.reps, lc.lapses,
           COALESCE(e.name, g.surface,
                    s.name || ' · ' || o.name,
                    substr(q.prompt, 1, 120)) AS term,
           COALESCE(e.type,
                    CASE WHEN g.id IS NOT NULL THEN 'gloss'
                         WHEN a.id IS NOT NULL THEN 'relation'
                         WHEN q.id IS NOT NULL THEN 'question' END) AS type,
           COALESCE(ed.definition, g.expansion,
                    s.name || ' ' || lower(replace(a.predicate, '_', ' ')) || ' ' || o.name,
                    q.answer) AS detail,
           e.id AS entity_id
      FROM learning_cards lc
      LEFT JOIN entities e ON lc.card_key = 'card:def:' || e.id
      LEFT JOIN entity_definitions ed ON ed.entity_id = e.id
      LEFT JOIN acronym_definitions g ON lc.card_key = 'card:gloss:' || g.id
      LEFT JOIN assertions a ON lc.card_key = 'card:rel:' || a.id
      LEFT JOIN entities s ON s.id = a.subject_id_raw
      LEFT JOIN entities o ON o.id = a.object_id_raw
      LEFT JOIN llm_questions q ON lc.card_key = 'card:q:' || q.id
     WHERE lc.user_id = ?
"""


def attention_cards(
    conn: db.Conn, *, user_id: str, weak_stability: float, strong_stability: float, limit: int
) -> list[db.Row]:
    """Worth another look: weak, or ever forgotten and not yet strong again.
    Soonest-due first, because these are the ones the schedule will press on
    next anyway."""
    return list(
        conn.execute(
            f"{_HELD_CARDS} AND lc.stability < ? AND (lc.stability < ? OR lc.lapses > 0) "
            " ORDER BY lc.due, lc.card_key LIMIT ?",
            (user_id, strong_stability, weak_stability, limit),
        ).fetchall()
    )


def upcoming_cards(
    conn: db.Conn, *, user_id: str, weak_stability: float, strong_stability: float, limit: int
) -> list[db.Row]:
    """What the schedule brings back next, minus everything the attention
    list already carries (the predicate is that list's negation)."""
    return list(
        conn.execute(
            f"{_HELD_CARDS} AND (lc.stability >= ? OR (lc.stability >= ? AND lc.lapses = 0)) "
            " ORDER BY lc.due, lc.card_key LIMIT ?",
            (user_id, strong_stability, weak_stability, limit),
        ).fetchall()
    )


def known_cards(
    conn: db.Conn, *, user_id: str, strong_stability: float, limit: int
) -> list[db.Row]:
    """The payoff list: what is held strongly, strongest first."""
    return list(
        conn.execute(
            f"{_HELD_CARDS} AND lc.stability >= ?  ORDER BY lc.stability DESC, lc.card_key LIMIT ?",
            (user_id, strong_stability, limit),
        ).fetchall()
    )


def deck_remaining(conn: db.Conn, *, user_id: str) -> int:
    """How many servable facts the user has never graded, across all four
    namespaces -- what "keep studying" can truthfully offer. COUNTs that
    mirror the candidate queries' WHERE clauses; if a namespace's eligibility
    changes there, change it here in the same commit."""
    row = conn.execute(
        """
        SELECT
          (SELECT COUNT(*) FROM entities e
             JOIN entity_definitions ed ON ed.entity_id = e.id
             LEFT JOIN learning_cards lc
                    ON lc.user_id = ? AND lc.card_key = 'card:def:' || e.id
            WHERE e.status = 'active' AND ed.status = 'active'
              AND ed.definition IS NOT NULL AND lc.card_key IS NULL)
        + (SELECT COUNT(*) FROM acronym_definitions g
             LEFT JOIN learning_cards lc
                    ON lc.user_id = ? AND lc.card_key = 'card:gloss:' || g.id
            WHERE g.status = 'active' AND g.expansion IS NOT NULL
              AND g.entity_id IS NULL AND lc.card_key IS NULL)
        + (SELECT COUNT(*) FROM assertions a
             LEFT JOIN learning_cards lc
                    ON lc.user_id = ? AND lc.card_key = 'card:rel:' || a.id
            WHERE a.run_id LIKE 'curation:%' AND a.retracted_at IS NULL
              AND a.kind = 'relation' AND lc.card_key IS NULL
              AND (SELECT status FROM entities WHERE id = a.subject_id_raw) = 'active'
              AND (SELECT status FROM entities WHERE id = a.object_id_raw) = 'active')
        + (SELECT COUNT(*) FROM llm_questions q
             LEFT JOIN learning_cards lc
                    ON lc.user_id = ? AND lc.card_key = 'card:q:' || q.id
            WHERE q.status = 'active' AND lc.card_key IS NULL) AS n
        """,
        (user_id, user_id, user_id, user_id),
    ).fetchone()["n"]
    return int(row)


def due_count(conn: db.Conn, *, user_id: str, now: str) -> int:
    """How many held cards are due, and nothing else -- the brief's nudge
    polls this, so it must stay a COUNT, never a derive."""
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM learning_cards WHERE user_id = ? AND due <= ?",
        (user_id, now),
    ).fetchone()
    return int(row["n"])


def held_total(conn: db.Conn, *, user_id: str) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM learning_cards WHERE user_id = ?", (user_id,)
    ).fetchone()
    return int(row["n"])


# ── settings ─────────────────────────────────────────────────────────────


def settings_row(conn: db.Conn, *, user_id: str) -> db.Row | None:
    return conn.execute("SELECT * FROM learning_settings WHERE user_id = ?", (user_id,)).fetchone()


def upsert_settings(
    conn: db.Conn,
    *,
    user_id: str,
    new_per_day: int,
    daily_goal: int,
    retention_target: float,
    now: str,
) -> None:
    conn.execute(
        "INSERT INTO learning_settings (user_id, new_per_day, daily_goal,"
        "                               retention_target, updated_at) "
        "VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT (user_id) DO UPDATE SET "
        "  new_per_day = EXCLUDED.new_per_day, daily_goal = EXCLUDED.daily_goal, "
        "  retention_target = EXCLUDED.retention_target, updated_at = EXCLUDED.updated_at",
        (user_id, new_per_day, daily_goal, retention_target, now),
    )


# ── the question bank ────────────────────────────────────────────────────


def questions_listing(conn: db.Conn, *, status: str, limit: int, offset: int) -> list[db.Row]:
    return list(
        conn.execute(
            "SELECT * FROM llm_questions WHERE status = ? "
            " ORDER BY created_at, id LIMIT ? OFFSET ?",
            (status, limit, offset),
        ).fetchall()
    )


def questions_total(conn: db.Conn, *, status: str) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM llm_questions WHERE status = ?", (status,)
    ).fetchone()
    return int(row["n"])


def insert_question(
    conn: db.Conn,
    *,
    id: str,
    run_id: str,
    kind: str,
    entity_id: str | None,
    prompt: str,
    choices: str | None,
    answer: str,
    grounding_article_id: str,
    grounding_quote: str,
    grounding_span: str | None,
    now: str,
) -> None:
    conn.execute(
        "INSERT INTO llm_questions (id, run_id, kind, entity_id, prompt, choices, answer,"
        "                           grounding_article_id, grounding_quote, grounding_span,"
        "                           status, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'proposed', ?)",
        (
            id,
            run_id,
            kind,
            entity_id,
            prompt,
            choices,
            answer,
            grounding_article_id,
            grounding_quote,
            grounding_span,
            now,
        ),
    )


def decide_question(
    conn: db.Conn,
    *,
    question_id: str,
    from_status: str,
    to_status: str,
    decided_by: str | None,
    now: str,
) -> bool:
    """One atomic status move; the `from_status` guard makes a double-click a
    no-op instead of a second decision (the `UPDATE ... RETURNING` shape from
    the read-then-write rule)."""
    row = conn.execute(
        "UPDATE llm_questions SET status = ?, decided_by = ?, decided_at = ? "
        " WHERE id = ? AND status = ? RETURNING id",
        (to_status, decided_by, now, question_id, from_status),
    ).fetchone()
    return row is not None


def question_prompt_exists(conn: db.Conn, *, normalized_prompt: str) -> bool:
    """Dedupe for the generation run: the same question re-proposed across
    runs would fill the review queue with echoes. Rejected rows count --
    re-proposing what a person refused is the loop this exists to stop."""
    row = conn.execute(
        "SELECT 1 AS one FROM llm_questions WHERE lower(prompt) = ? LIMIT 1",
        (normalized_prompt,),
    ).fetchone()
    return row is not None


def question_articles(conn: db.Conn, *, limit: int) -> list[db.Row]:
    """Input for the generation run: recent, substantial, non-duplicate
    articles. The 500-character floor drops link roundups and paywalled
    stubs, whose questions could only be about the headline."""
    return list(
        conn.execute(
            "SELECT id, title, text_nfc FROM documents "
            " WHERE text_nfc IS NOT NULL AND length(text_nfc) > 500 "
            "   AND duplicate_of IS NULL "
            " ORDER BY published_at DESC NULLS LAST, id LIMIT ?",
            (limit,),
        ).fetchall()
    )


def question_cost_sample(conn: db.Conn) -> db.Row:
    """What prior generation calls actually cost -- the preview's estimate
    rests on the ledger, never on a constant pretending to be one."""
    return conn.execute(
        "SELECT COUNT(*) AS n, COALESCE(AVG(cost_usd), 0) AS avg_cost "
        "  FROM llm_calls WHERE task = 'questions' AND status = 'ok'",
    ).fetchone()


def entity_id_for_name(conn: db.Conn, *, name: str) -> str | None:
    """A best-effort colour link: the model returns surfaces, and CODE decides
    whether one names an entity. No match is fine -- the question still works,
    it just carries no entity."""
    row = conn.execute(
        "SELECT e.id FROM entities e "
        " WHERE e.status = 'active' AND lower(e.name) = lower(?) "
        " ORDER BY e.mention_count DESC, e.id LIMIT 1",
        (name,),
    ).fetchone()
    return row["id"] if row else None
