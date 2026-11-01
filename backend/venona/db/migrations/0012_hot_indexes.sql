-- Indexes on the three tables that only ever grow.
--
-- None of these changes an answer. They change what a read costs on an
-- instance that has been running for a year, which is the only kind that has
-- the problem -- so the cost arrives on the deployment with the most history
-- and the least appetite for a surprise.

-- The spend dashboard's `cost_by_day` scans the whole ledger to answer "the
-- last N days". `created_at` is ISO-8601 TEXT, which sorts lexicographically,
-- so a plain b-tree serves the range predicate exactly as a timestamp column
-- would.
CREATE INDEX IF NOT EXISTS ix_llm_calls_created_at ON llm_calls (created_at);

-- Active relations. Four readers walk `assertions` with this exact predicate
-- and no index behind it -- the learning deck per request, the curation
-- queue, the research stage and the graph projector -- and `assertions` is the
-- append-only table by invariant 1, so it is the fastest-growing one in the
-- schema.
--
-- PARTIAL, and the WHERE matches those readers' predicate exactly: a partial
-- index Postgres cannot prove covers the query is an index it will not use.
CREATE INDEX IF NOT EXISTS ix_assertions_active_relations
    ON assertions (subject_id_raw, object_id_raw)
    WHERE kind = 'relation' AND retracted_at IS NULL;

-- Unfinished runs. `lane_conflict` asks this on every launch and
-- `reap_interrupted` on every runs-list read, and both walk a table that keeps
-- one row per run ever executed -- while the answer is almost always zero rows.
CREATE INDEX IF NOT EXISTS ix_runs_active
    ON runs (status)
    WHERE status IN ('running', 'queued');
