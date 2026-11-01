-- The shapes the ontology refused at extraction time, aggregated per run.
--
-- One row per (run, asserted predicate, subject type, object type). The
-- individual rejections already exist as free-text run warnings; what dies
-- with that scroll is the AGGREGATE -- "the press asserted Company USES_TECH
-- Technology 4x this run" -- which is exactly the evidence an operator needs
-- where the ontology is edited, because a rewrite can never widen a contract
-- (its results must be well-typed) and only a human edit can. Read back by
-- the ontology editor's rejection panel, filtered against the CURRENT
-- ontology at read time so widening a contract dissolves its rows from view.
--
-- The example columns carry one verbatim instance (first seen in the run):
-- a count argues there is a problem, an example says what it is.
CREATE TABLE run_rejected_shapes (
    run_id             TEXT NOT NULL,
    predicate          TEXT NOT NULL,
    -- '' when the model omitted the type; kept non-null so the PK works.
    subject_type       TEXT NOT NULL,
    object_type        TEXT NOT NULL,
    n                  INTEGER NOT NULL,
    example_subject    TEXT NOT NULL,
    example_object     TEXT NOT NULL,
    example_article_id TEXT,
    example_evidence   TEXT,
    at                 TEXT NOT NULL,
    PRIMARY KEY (run_id, predicate, subject_type, object_type)
);
