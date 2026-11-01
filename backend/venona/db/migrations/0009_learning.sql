-- Learning: per-user spaced-repetition state, the review log, the generated
-- question bank, and per-user knobs (docs/adr/0157, epic O).
--
-- Cards themselves are NOT stored. A card is a rendering of a row that already
-- carries invariant 3's provenance -- an entity definition, a gloss, a curated
-- assertion, an approved generated question -- and materialising it would copy
-- a sentence-traceable fact into a row that goes stale when the definition is
-- edited, rejected, or its entity merged. Deriving at read time makes "card
-- exists <=> sourced fact exists" structural. What persists is only what
-- cannot be derived: a learner's memory state and the log of their graded
-- reviews.
--
-- PRIVACY (the operator's stance, ADR 0157): rows in the two per-user tables
-- are created only by the review endpoint, whose subject is always the acting
-- user, and read only by learner-scoped routes. There is no admin surface over
-- them. These are the first genuinely per-user data tables in this schema; the
-- deferred `workspace_id` note in 0001 still holds for the corpus tables.

-- Per-user FSRS memory state, one row per (user, card). A row exists only
-- after the user's FIRST explicit grade: "new" is the absence of a row, so an
-- untouched deck writes nothing about anybody. When the underlying fact stops
-- deriving (definition rejected, entity merged away, question retired) the row
-- goes dormant -- never served, never deleted -- because memory state is
-- history, and a fact that returns wakes it with that history intact.
CREATE TABLE learning_cards (
    user_id     TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    -- card:def:<entity_id> | card:gloss:<gloss_id> | card:rel:<assertion_id>
    -- | card:q:<question_id>. Opaque to SQL; the service parses the first two
    -- segments. One row per FACT -- formats (flashcard/choice/cloze) are
    -- renderings rotated per review, not separate schedules.
    card_key    TEXT NOT NULL,
    state       TEXT NOT NULL,              -- learning | review | relearning
    stability   DOUBLE PRECISION NOT NULL,  -- FSRS S, days
    difficulty  DOUBLE PRECISION NOT NULL,  -- FSRS D, 1..10
    due         TEXT NOT NULL,              -- ISO-8601 Z, compared lexically
    last_review TEXT NOT NULL,
    reps        INTEGER NOT NULL DEFAULT 0,
    lapses      INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    PRIMARY KEY (user_id, card_key)
);

-- The queue read is "this user's cards due by now", so the index leads with
-- the user and orders by the due text.
CREATE INDEX ix_learning_cards_due ON learning_cards (user_id, due);

-- Append-only. The daily series, retention, streak and goal all DERIVE from
-- this log; there is no session table because a session is not an act of
-- study -- a streak is "distinct days with at least one review", and a row
-- per review records strictly less than a row per sitting would.
CREATE TABLE learning_reviews (
    id           TEXT PRIMARY KEY,           -- 'review:' + uuid7
    user_id      TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    card_key     TEXT NOT NULL,
    rating       INTEGER NOT NULL CHECK (rating BETWEEN 1 AND 4),  -- Again..Easy
    -- The card's state BEFORE the grade. Retention is computed over non-new
    -- reviews only: a first meeting is not a memory test, and counting it
    -- would flatter or damn the number depending on the deck's age.
    review_kind  TEXT NOT NULL,              -- new | review | relearning
    format       TEXT NOT NULL,              -- flashcard | choice | cloze | relation
    -- Days since the previous review of this card, 0.0 on the first. Stored
    -- rather than recomputed so the log stays interpretable if the state row
    -- is ever reset.
    elapsed_days DOUBLE PRECISION NOT NULL,
    -- What was scheduled when the grade landed. Audit only: it lets "was this
    -- review early or late" be answered from the log alone.
    due_at       TEXT,
    created_at   TEXT NOT NULL
);

-- Both the day-bucketed series and "reviews today" scan one user's recent
-- rows; created_at is ISO text, so the range and the substr() bucketing both
-- ride this index.
CREATE INDEX ix_learning_reviews_user_day ON learning_reviews (user_id, created_at);

-- The generated question bank. Proposed by a run, ruled on by a person: a row
-- is born 'proposed' and enters nobody's deck until an admin makes it
-- 'active' -- the same posture as merges and definitions, where model output
-- is never self-admitting.
CREATE TABLE llm_questions (
    id                   TEXT PRIMARY KEY,  -- 'question:' + uuid7
    run_id               TEXT NOT NULL,
    kind                 TEXT NOT NULL,     -- choice | cloze | qa
    -- Resolved by CODE from surfaces via the alias index; the model never
    -- emits an id. NULL when no surface resolved -- the question still works,
    -- it just carries no entity colour.
    entity_id            TEXT,
    prompt               TEXT NOT NULL,
    choices              TEXT,              -- json array; NULL unless kind = 'choice'
    answer               TEXT NOT NULL,
    -- Bare TEXT like every other grounding_* column in this schema: the
    -- article may be pruned later and the quote must still say where it came
    -- from.
    grounding_article_id TEXT NOT NULL,
    -- Verified by code as an exact substring of that article's text_nfc
    -- before the row is written; a paraphrase is rejected in the run, so a
    -- stored quote is always a real quotation (invariant 3).
    grounding_quote      TEXT NOT NULL,
    grounding_span       TEXT,              -- json [start, end], computed by the verifier
    status               TEXT NOT NULL DEFAULT 'proposed',  -- proposed | active | rejected | retired
    decided_by           TEXT,              -- users.id; NULL = never reviewed. Never backfilled.
    decided_at           TEXT,
    created_at           TEXT NOT NULL
);

-- The review queue lists by status; the run detail page lists by run.
CREATE INDEX ix_llm_questions_status ON llm_questions (status);
CREATE INDEX ix_llm_questions_run ON llm_questions (run_id);

-- Per-user knobs. An absent row means the code defaults, and the row is
-- created on first PATCH -- so, like learning_cards, an untouched feature
-- stores nothing.
CREATE TABLE learning_settings (
    user_id          TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    new_per_day      INTEGER NOT NULL DEFAULT 10,
    daily_goal       INTEGER NOT NULL DEFAULT 30,   -- reviews/day the streak ring fills against
    -- FSRS schedules for this recall probability. A learner preference, not
    -- an operator decision, which is why it lives here and not in profile/.
    retention_target DOUBLE PRECISION NOT NULL DEFAULT 0.9,
    updated_at       TEXT NOT NULL
);
