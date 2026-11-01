-- Personal relevance: per-user emphasis, followed entities, and the reader's
-- own generated brief (ADR: per-user relevance is derived, never re-scored).
--
-- Nothing per-(user, cluster) is stored. A personal score is
-- compute_overall(clusters.axes, the user's derived weights) at read time, so
-- consistency with the corpus is structural -- the same derive-don't-copy
-- posture as learning cards (0009). What persists is only what cannot be
-- derived: the reader's stated emphasis, their explicit follows, and the one
-- prose document a model call produced for them.
--
-- PRIVACY (the operator's stance, ADR 0157 extended): rows here are created
-- only by routes whose subject is always the acting user, and read only by
-- reader-scoped routes. There is no admin surface over them. An absent row
-- means the instance defaults, so an untouched feature stores nothing about
-- anybody.

-- The reader's emphasis over the instance rubric's axes, as words rather
-- than weights: {axis_id: 'more' | 'normal' | 'less'}. Weights are DERIVED
-- server-side (instance default x factor, renormalized to sum 1.0) so no
-- stored row can put `overall` off the 0-100 scale, and the tier cuts scale
-- with the reader's reachable maximum -- the unreachable-cut bug (ADR 0047)
-- is prevented structurally rather than validated away.
CREATE TABLE relevance_settings (
    user_id    TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    emphasis   TEXT NOT NULL,   -- json {axis_id: level}
    updated_at TEXT NOT NULL
);

-- Names the reader follows. Explicit acts only -- a follow affordance or the
-- picker -- never inferred from reading (the no-passive-tracking stance).
-- Follows GROUP the personal reading list; they never enter the 0-100, which
-- is calibrated by the rubric weights alone. entity_id is bare TEXT like
-- every other entity reference outside the corpus tables: the entity may be
-- merged away, and the read path resolves through entity_mappings, so a
-- dormant follow wakes if its entity returns.
CREATE TABLE entity_follows (
    user_id    TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    entity_id  TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (user_id, entity_id)
);

-- The reader's own brief: one prose document per (user, day), written by one
-- light-lane model call over their personally ranked items. SEPARATE from
-- briefs/brief_blocks so the shared archive, the brief_clusters exclusion
-- ledger, and every existing brief query never see a personal document.
-- inputs_hash covers emphasis + follows + the run's profile hash: a revisit
-- with unchanged inputs is a free read, an emphasis edit regenerates at most
-- once.
CREATE TABLE personal_briefs (
    id           TEXT PRIMARY KEY,          -- 'pbrief:' + uuid7
    user_id      TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    date         TEXT NOT NULL,
    run_id       TEXT,
    inputs_hash  TEXT NOT NULL,
    title        TEXT NOT NULL,
    dek          TEXT,
    generated_at TEXT NOT NULL,
    created_at   TEXT NOT NULL
);

-- The read is "this user's brief for today"; regeneration replaces the row,
-- so the day holds at most one.
CREATE UNIQUE INDEX ix_personal_briefs_user_date ON personal_briefs (user_id, date);

-- Same shape as brief_blocks: the personal document goes through the same
-- markdown -> blocks -> spans annotation path, so the reader's brief gets
-- hoverable mentions exactly like the shared one.
CREATE TABLE personal_brief_blocks (
    id       TEXT PRIMARY KEY,
    brief_id TEXT NOT NULL REFERENCES personal_briefs(id) ON DELETE CASCADE,
    section  INTEGER NOT NULL,
    heading  TEXT,
    ordinal  INTEGER NOT NULL,
    type     TEXT NOT NULL,
    depth    INTEGER NOT NULL,
    text     TEXT NOT NULL,
    spans    TEXT NOT NULL,
    marks    TEXT NOT NULL,
    item_id  TEXT,
    role     TEXT
);

CREATE INDEX ix_personal_brief_blocks_brief ON personal_brief_blocks (brief_id);
