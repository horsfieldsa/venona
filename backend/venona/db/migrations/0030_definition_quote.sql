-- The sentence in an article that says what an entity IS, as distinct from the
-- sentence that merely mentions it.
--
-- `evidence_quote` is the context sentence the extractor happened to pick, one
-- per entity per document. It answers "where was this named", which is what C1
-- needs, and it is the only text `pipeline/define.py` has ever been able to
-- read. The two questions are not the same one: an article can name a platform
-- in a contract-award sentence and describe it three paragraphs later, and
-- every definition in that gap was unreachable.
--
-- Nullable, and null is the normal answer. Most mentions define nothing, and a
-- column that had to be filled would be filled with paraphrase.
--
-- Written only after `pipeline.define.verify_definition_sentence` has proved
-- the text is a substring of `documents.text_nfc` and names the surface, so a
-- row here is quotable evidence and not a model's summary of one.
ALTER TABLE assertions ADD COLUMN IF NOT EXISTS definition_quote TEXT;

-- The backfill and the free tier-1 pass both ask the same question -- which
-- rows carry one of these and have no definition yet -- over a table where the
-- overwhelming majority of rows do not. Partial, so it indexes the answer
-- rather than the corpus.
CREATE INDEX IF NOT EXISTS ix_assertions_definition_quote
    ON assertions(entity_id_raw)
    WHERE definition_quote IS NOT NULL;
