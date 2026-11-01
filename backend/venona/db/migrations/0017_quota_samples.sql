-- What the subscription had left, sampled from the model calls we already make.
--
-- A row is a point observation of ONE window, not a property of a call, which
-- is why it is not a column on `llm_calls`. A single `rate_limit_event` carries
-- every window at once, so one call contributes several rows, and the same
-- window is re-observed by every later call until it moves. Kept as a series
-- rather than a single current value because "how fast is this instance burning
-- its week" is the question a bare percentage cannot answer.
--
-- `utilization` is the 0-to-1 fraction the CLI reports, unscaled. Storing what
-- the source said means the one conversion to a percentage lives at the render
-- boundary and cannot disagree with a second copy of itself.
--
-- The unique index is what makes recording idempotent. Every call re-observes
-- an unmoved window, so without it a single run would write hundreds of
-- identical rows; with it the insert is ON CONFLICT DO NOTHING and never
-- becomes a read-then-write, which at READ COMMITTED is the race CLAUDE.md
-- warns about. A window only earns a row when its reading actually changes.
--
-- `window_name`, not `window`: WINDOW is a reserved word in Postgres (the
-- function frame clause), so the natural spelling would need quoting at every
-- call site forever.
CREATE TABLE quota_samples (
  id           TEXT PRIMARY KEY,
  observed_at  TEXT NOT NULL,
  window_name  TEXT NOT NULL,
  utilization  DOUBLE PRECISION NOT NULL,
  resets_at    TEXT NOT NULL,
  -- allowed | allowed_warning | rejected. Of the whole reading, not this
  -- window: the CLI reports one status across the set.
  status       TEXT NOT NULL,
  -- True when this is the window the CLI itself considers closest to binding.
  -- Not derivable from `utilization` -- the fullest window and the binding one
  -- are not always the same, and the CLI's own warning names this one.
  representative BOOLEAN NOT NULL DEFAULT FALSE,
  -- Nullable: a light-lane call outside any run still observes a window, and
  -- an unattributed reading is honest where a fabricated run id would not be.
  run_id       TEXT
);

CREATE UNIQUE INDEX ux_quota_samples_reading
  ON quota_samples(window_name, resets_at, utilization);

CREATE INDEX ix_quota_samples_window ON quota_samples(window_name, observed_at DESC);
