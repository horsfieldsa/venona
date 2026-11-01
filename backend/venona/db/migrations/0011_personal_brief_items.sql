-- The reader's brief becomes items: the shared brief's machinery, minus the
-- section desks. The reader's ranking IS the order, so there is no section
-- column and `ord` alone carries the document's order (the supplied-ranking
-- position, assigned by code -- the model's own ordering is not trusted).
--
-- CASCADE, unlike brief_items' deliberate no-FK: regeneration replaces the
-- day's personal_briefs row in place and relies on the cascade to take the
-- old document's children with it, exactly as personal_brief_blocks already
-- does. Without it every regeneration would orphan the previous items.
CREATE TABLE personal_brief_items (
    -- The id is the brief id plus an iNN suffix. No colon in this comment:
    -- alembic runs this file through SQLAlchemy's textual SQL, where a colon
    -- followed by a word is a bind parameter -- even inside a comment.
    id         TEXT PRIMARY KEY,
    brief_id   TEXT NOT NULL REFERENCES personal_briefs(id) ON DELETE CASCADE,
    cluster_id TEXT,
    headline   TEXT NOT NULL,
    state      TEXT NOT NULL,         -- new | follow_up | ongoing
    state_note TEXT,
    top_rank   INTEGER,               -- 1..3 for the TOP block, else NULL
    top_line   TEXT,
    ord        INTEGER NOT NULL,      -- the render order
    created_at TEXT NOT NULL
);

CREATE INDEX ix_personal_brief_items_brief ON personal_brief_items (brief_id, ord);
