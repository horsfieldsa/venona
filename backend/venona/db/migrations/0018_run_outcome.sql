-- What a run FOUND, as opposed to whether it ran.
--
-- `runs.status` answered two questions with one word. `succeeded_partial` was
-- built for the first -- the run degraded, a feed 403'd, the budget ran out --
-- and then every site that needed to say "the machinery was fine, the answer
-- was no" reached for it too, because there was nowhere else. On this database
-- that made 44 of 68 lookups `succeeded_partial` with an empty `warnings` array
-- and a null `error`: rows mechanically indistinguishable from the 24 that
-- worked, wearing a word that says something degraded when nothing did.
--
-- Four values, and the pair (status, outcome) is what a reader reads:
--
--   delivered      the run produced its artifact
--   some           a batch landed part of what it tried
--   nothing_found  it did the work; the answer is no
--   nothing_to_do  there was nothing to look for
--
-- `some` rather than `partial`, deliberately. `outcome='partial'` beside
-- `status='succeeded_partial'` is two near-identical words meaning different
-- things in one row, which is the confusion this column exists to remove.
--
-- NULL is a real state and the default: a run that FAILED or was CANCELLED has
-- no outcome, and inventing one for it would be the same overload running the
-- other way. Old rows the backfill below cannot read stay NULL too.
--
-- No CHECK constraint, matching `status`: the vocabulary is validated in
-- `run_service.finish_run` against `OUTCOMES`. A `runs` table that raises on a
-- row written by an older release is worse than one that renders it plainly --
-- the same reasoning `run_types.type_for` is built on.
ALTER TABLE runs ADD COLUMN outcome TEXT;

-- Backfill from `run_stages.counts`, which is where `run_launcher` already
-- puts each work closure's returned `data` -- one row per run for the
-- single-stage kinds, which is every kind whose outcome was being smuggled
-- through `status`.
--
-- A DO block with a per-row exception handler rather than a set-based UPDATE,
-- because `counts` is TEXT: one malformed row would abort a cast in the WHERE
-- clause and take the whole migration with it. A row that will not parse keeps
-- its NULL and renders as it does today.
DO $$
DECLARE
  r RECORD;
  c JSONB;
  verdict TEXT;
BEGIN
  FOR r IN
    SELECT runs.id, runs.kind, runs.brief_id, runs.counters, run_stages.counts
      FROM runs
      LEFT JOIN run_stages ON run_stages.run_id = runs.id
                          AND run_stages.stage IN ('lookup', 'ingest', 'questions',
                                                   'personal_brief')
     WHERE runs.status LIKE 'succeeded%'
  LOOP
    verdict := NULL;
    BEGIN
      c := COALESCE(r.counts::jsonb, '{}'::jsonb);
    EXCEPTION WHEN others THEN
      CONTINUE;
    END;

    IF r.kind = 'lookup' THEN
      -- `grounded` is a boolean on the single lookup and a COUNT on the
      -- alternatives branch, where it is the number of new meanings found.
      IF jsonb_typeof(c->'grounded') = 'number' THEN
        verdict := CASE WHEN (c->>'grounded')::numeric > 0 THEN 'delivered'
                        ELSE 'nothing_found' END;
      ELSIF c->>'grounded' IS NOT NULL THEN
        verdict := CASE WHEN c->>'grounded' = 'true' THEN 'delivered'
                        ELSE 'nothing_found' END;
      END IF;

    ELSIF r.kind IN ('lookup_all', 'definitions') THEN
      IF c->>'considered' IS NOT NULL THEN
        verdict := CASE
          WHEN (c->>'considered')::numeric = 0 THEN 'nothing_to_do'
          WHEN COALESCE((c->>'grounded')::numeric, 0) = 0 THEN 'nothing_found'
          WHEN (c->>'grounded')::numeric < (c->>'considered')::numeric THEN 'some'
          ELSE 'delivered'
        END;
      END IF;

    ELSIF r.kind = 'ingest' THEN
      verdict := CASE WHEN c->>'is_new' = 'true' AND c->>'analyzed' = 'true'
                      THEN 'delivered' ELSE 'nothing_to_do' END;

    ELSIF r.kind = 'questions' THEN
      verdict := CASE
        WHEN c->>'reason' = 'no_articles' THEN 'nothing_to_do'
        WHEN COALESCE((c->>'generated')::numeric, 0) > 0 THEN 'delivered'
        ELSE 'nothing_found'
      END;

    ELSIF r.kind = 'personal_brief' THEN
      verdict := CASE
        WHEN c->>'brief_id' IS NOT NULL THEN 'delivered'
        ELSE 'nothing_to_do'
      END;

    ELSIF r.kind IN ('daily', 'on_demand', 'cli', 'rewrite') THEN
      -- The pipeline kinds. The brief IS the artifact, so the run row itself
      -- answers the question without reading a stage.
      verdict := CASE WHEN r.brief_id IS NOT NULL THEN 'delivered'
                      ELSE 'nothing_to_do' END;

    ELSIF r.kind = 'research' THEN
      -- A corroboration sweep writes no brief, so `brief_id` says nothing about
      -- it. What it set out to find is coverage, and `documents_new` is the
      -- count of it.
      BEGIN
        verdict := CASE
          WHEN COALESCE((r.counters::jsonb->>'documents_new')::numeric, 0) > 0
          THEN 'delivered' ELSE 'nothing_found' END;
      EXCEPTION WHEN others THEN
        verdict := NULL;
      END;
    END IF;

    -- Every other kind keeps its NULL. `domain_setup` in particular launches
    -- WITHOUT a stage, so the drafted/failed counts its work returns were never
    -- written anywhere this query could read -- and a guessed outcome on a
    -- historical row is exactly the dishonesty this column is replacing.

    IF verdict IS NOT NULL THEN
      UPDATE runs SET outcome = verdict WHERE id = r.id;
    END IF;
  END LOOP;
END $$;

-- The runs page filters on both, and the quiet-nights alarm in `scheduler.py`
-- asks for the last N dailies by outcome.
CREATE INDEX ix_runs_outcome ON runs (kind, outcome, started_at DESC);
