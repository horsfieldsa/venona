-- Who changed what the model is told.
--
-- `0005_authority.sql` made the argument for `user_events` and it applies here
-- with more force, not less:
--
--     A change made over HTTP is attributable to nothing unless something
--     writes it down.
--
-- Editing `relevance_profile.md` or `research_prompt.md` changes what every
-- reader's brief is written from, for every run after it, and until now it was
-- the one write class in the system with no trail. `run_launcher` records
-- `started_by`, curation verdicts carry `actor_id`, authority changes write
-- `user_events` -- config writes recorded a log line and a `.bak` file named
-- after its own mtime. Twenty of those, in a ring, with no author (ADR 0166).
--
-- THE POINT IS THE JOIN. `runs.profile_hash` already records which profile a run
-- executed under and `runs.domain_hash` does the same for the descriptor, so a
-- row here turns two half-answers into one: *this* person wrote *this* version
-- at *this* time, and *those* runs ran under it.
CREATE TABLE config_events (
  -- 'cevent:' + uuid7. Sortable by creation, the only order this is read in.
  id         TEXT PRIMARY KEY,
  -- The `config_service.EDITABLE` key -- 'relevance_profile', 'ontology'. Not a
  -- foreign key and not a CHECK: the editable set is a product decision that
  -- changes, and a document removed from `EDITABLE` must not take the record of
  -- who last edited it along with it.
  key        TEXT NOT NULL,
  -- WHO DID IT, or NULL. NULL is honest and common, and means what it means on
  -- every other actor column in this schema (contract C1): the CLI decided, or a
  -- process did -- the setup wizard and the hub's provisioning call both write
  -- config with no person behind them. It is never backfilled; putting a name on
  -- a change nobody signed would invent provenance, which invariant 3 forbids
  -- everywhere else.
  actor_id   TEXT REFERENCES users(id),
  -- The first sixteen hex characters of sha256(content) -- the same value
  -- `write_doc` already computes for conflict detection, so the row names a
  -- version that can be recognised rather than merely a moment.
  --
  -- Spelled out in words rather than as a Python slice, and that is not style.
  -- `op.execute` hands this file to SQLAlchemy's `text()`, which scans the WHOLE
  -- file for bind-parameter markers and does not skip comments. A colon followed
  -- by word characters -- in a slice, or in prose explaining one -- is read as a
  -- parameter, and the migration dies with "a value is required for bind
  -- parameter". Nothing in a comment here may carry that shape.
  version    TEXT NOT NULL,
  -- What it replaced, or NULL for the first write of a document. Two versions is
  -- what makes a row answer "what changed" without reading the `.history/` ring,
  -- which prunes at 20 and this table does not.
  prev       TEXT,
  bytes      INTEGER NOT NULL,
  created_at TEXT NOT NULL
);
-- The only query this table has: the last N, newest first, exactly like
-- `user_events`. The admin rail reads it that way and nothing reads it another.
CREATE INDEX ix_config_events_recent ON config_events (created_at DESC);
-- And "what happened to this document", which is the second question anyone
-- asks once they have seen the first answer.
CREATE INDEX ix_config_events_key ON config_events (key, created_at DESC);
