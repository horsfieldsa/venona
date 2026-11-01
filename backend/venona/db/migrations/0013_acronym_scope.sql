-- Scope the acronyms stage to what it has not already read.
--
-- The stage loaded every analyzed document's full text on every run and
-- reprocessed all of it -- to detect surfaces, to count sightings, and to learn
-- which capitalised tokens are ordinary English. That is O(corpus) nightly
-- work whose answer for a document read last month cannot have changed, and it
-- grows for as long as the instance runs.
--
-- Scoping needs two things the run cannot recompute from the documents it
-- looks at, so both are persisted here: the counts, and the document
-- frequencies.

-- Which documents the stage has already read. On `clusters` rather than
-- `documents` because the stage reads canonical documents, selected by their
-- cluster being analyzed -- the same place `analyzed_at` lives, for the same
-- reason.
ALTER TABLE clusters ADD COLUMN IF NOT EXISTS acronyms_scanned_at TEXT;

-- How often each detected surface has been seen, across the whole corpus.
--
-- This was an in-memory accumulator rebuilt from every document each night,
-- and the tier gates read it: how many sightings a surface has and how many
-- articles carry it are what decide whether it is worth a web lookup. A run
-- that reads only new documents cannot know either without them being written
-- down.
--
-- SEPARATE FROM `acronym_definitions`, which holds surfaces that have a
-- definition or a verdict. Most detected surfaces have neither, and a surface
-- with no row there still has to be counted to become a candidate.
CREATE TABLE IF NOT EXISTS surface_sightings (
    normalized     TEXT PRIMARY KEY,
    surface        TEXT NOT NULL,
    sighting_count INTEGER NOT NULL DEFAULT 0,
    -- Distinct documents. Summed per scan rather than counted, which is exact
    -- only because a document is scanned once -- `acronyms_scanned_at` is what
    -- makes that true.
    article_count  INTEGER NOT NULL DEFAULT 0,
    first_seen     TEXT NOT NULL,
    last_seen      TEXT NOT NULL
);

-- How many documents use each capitalised token as an ordinary word.
--
-- DOCUMENT FREQUENCY, not membership, and cumulative rather than nightly. One
-- document using a token as an ordinary word is a coincidence -- a surname, a
-- product, a place -- and suppressing an acronym on a coincidence is how
-- VICTOR was lost. Recurrence is what distinguishes a shouted English word,
-- and recurrence cannot be measured from tonight's documents alone.
CREATE TABLE IF NOT EXISTS ordinary_word_df (
    word      TEXT PRIMARY KEY,
    doc_count INTEGER NOT NULL DEFAULT 0
);
