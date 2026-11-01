-- Which model service did this, and what paid for it.
--
-- NOTE FOR ANYONE EDITING THIS FILE: no comment here may contain a colon
-- followed by word characters. SQLAlchemy's `text()` reads a leading-colon
-- token as a bind parameter INSIDE comments as well as in statements, and
-- the pattern includes digits -- so a file reference carrying a line number
-- detonates the migration, and so does spelling the hazard out literally.
-- The baseline carries the same warning for the same reason.

-- A connection record, not only a secret store.
--
-- `secret` is NULLABLE because the two kinds of credential live in different
-- places. A subscription is written by the CLI into an instance-scoped home on
-- disk and venona never holds a copy; the row still exists so the account it
-- signed in as, and when that was last confirmed, survive a restart. The
-- in-process health cache cannot answer either question after one.
--
-- PLAINTEXT where a secret is present, and deliberately. Unlike a password
-- nothing ever compares it, and unlike a hash venona has to REPLAY it, so
-- encrypting here would move the problem to where the key lives, which is the
-- environment, which is where this started. The notifications table records
-- the same decision about the same class of value.
CREATE TABLE IF NOT EXISTS harness_credentials (
    id            TEXT PRIMARY KEY,
    harness       TEXT NOT NULL,
    billing_mode  TEXT NOT NULL,
    -- NO ROUTE RETURNS THIS. Responses carry a hint derived at read time.
    secret        TEXT,
    -- What the service says it is signed in as. Admin surfaces only; the
    -- health route is reader-reachable and must not carry it.
    account_label TEXT,
    verified_at   TEXT,
    -- Attribution, never ownership. ON DELETE SET NULL rather than CASCADE:
    -- deleting the administrator who connected an account must not disconnect
    -- the model for the whole instance.
    updated_by    TEXT REFERENCES users(id) ON DELETE SET NULL,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);

-- One credential per service per way of paying for it, so connecting an API
-- key does not silently discard a subscription an operator may switch back to.
CREATE UNIQUE INDEX IF NOT EXISTS ux_harness_credentials
    ON harness_credentials (harness, billing_mode);

-- Provenance on the ledger and on the run.
--
-- NULLABLE, NO DEFAULT, NEVER BACKFILLED. Every row written before this
-- migration was Claude Code on a subscription, and stamping that is provably
-- true -- but a DEFAULT would also silently stamp every FUTURE row that missed
-- the write path, which is the failure the outcome column was introduced to
-- remove. A row with no service named renders without one.
ALTER TABLE llm_calls ADD COLUMN IF NOT EXISTS harness TEXT;
ALTER TABLE llm_calls ADD COLUMN IF NOT EXISTS billing_mode TEXT;
-- How `cost_usd` was arrived at. One column now holds four kinds of number --
-- a subscription's shadow price at list rates, what a metered provider
-- actually billed, tokens multiplied by a price table this repo maintains, and
-- a plain zero -- and summing across them means nothing. An aggregate that
-- cannot tell them apart is how a chart claims a bill nobody received.
ALTER TABLE llm_calls ADD COLUMN IF NOT EXISTS cost_basis TEXT;

ALTER TABLE runs ADD COLUMN IF NOT EXISTS harness TEXT;
ALTER TABLE runs ADD COLUMN IF NOT EXISTS billing_mode TEXT;

-- Stamped when the executor is CHOSEN, not when the row is written: a queued
-- light-lane run is admitted minutes later, and the service could change in
-- between. A run cancelled before admission keeps NULL, correctly.
CREATE INDEX IF NOT EXISTS ix_runs_harness ON runs (harness);

-- Windows belong to the service that reported them.
--
-- Without this a switch leaves both services' windows in the latest-per-window
-- read forever, two of them frozen at the moment the old service stopped being
-- used, and two rows claiming to be representative at once. NULLS NOT DISTINCT
-- keeps the re-observation guarantee for every row written before the column
-- existed; without it each of those becomes distinct from itself.
ALTER TABLE quota_samples ADD COLUMN IF NOT EXISTS harness TEXT;
DROP INDEX IF EXISTS ux_quota_samples_reading;
CREATE UNIQUE INDEX IF NOT EXISTS ux_quota_samples_reading
    ON quota_samples (harness, window_name, resets_at, utilization) NULLS NOT DISTINCT;
