-- The authored documents, and the archives, as rows.
--
-- NOTE FOR ANYONE EDITING THIS FILE -- no comment here may contain a colon
-- followed by word characters. SQLAlchemy's `text()` reads a leading-colon
-- token as a bind parameter INSIDE comments as well as in statements, so a
-- file reference carrying a line number detonates the migration. The baseline,
-- 0025 and 0026 carry the same warning for the same reason.

-- WHY THESE MOVE (ADR 0234). The api's persistent disk carried four writable
-- locations and only two things on them could not be rebuilt -- the authored
-- config and profile documents, and one credential file per model service. A
-- disk disables zero-downtime deploys and forbids a second instance, so the
-- two survivors become rows and the directories are written out of those rows
-- at boot.
--
-- The filesystem is still where every LOADER reads from. `settings.get_settings`,
-- `get_ontology`, `get_domain` and `load_rubric` all take a path, and keeping
-- that true is the whole reason this is a projection rather than a port.
CREATE TABLE IF NOT EXISTS config_documents (
    -- The `config_service.EDITABLE` key, not a filename. A document that moves
    -- between `config/` and `profile/`, or is renamed on disk, is the same
    -- document to a reader and must keep its history.
    key         TEXT PRIMARY KEY,
    content     TEXT NOT NULL,
    -- The same sha256 prefix `config_service._version` computes over the
    -- content. Stored rather than derived on read so a conflict check costs no
    -- hashing, and so a row whose content was somehow written past this module
    -- is detectable by recomputing.
    version     TEXT NOT NULL,
    -- ISO-8601 UTC with Z, the wire convention everywhere else. This is what
    -- the materialized file's mtime is set from, so `freshness.config_stamp`
    -- keeps answering "when did a person last change this" rather than "when
    -- did this container boot".
    updated_at  TEXT NOT NULL
);

-- What `.history/` was. Never materialized -- twenty snapshots times twelve
-- documents is 240 files nothing reads except the history panel, and that
-- panel can read rows.
CREATE TABLE IF NOT EXISTS config_document_history (
    key         TEXT NOT NULL,
    -- The content hash of the snapshotted version. Part of the key because the
    -- stamp alone is not unique -- saving, spotting a typo and saving again
    -- inside one second produced two snapshots sharing a stamp, which is why
    -- the filename carried the version too.
    version     TEXT NOT NULL,
    content     TEXT NOT NULL,
    -- When the version INSIDE the snapshot was written, not when the snapshot
    -- was taken. Same reading as the filename stamp it replaces.
    stamp       TEXT NOT NULL,
    -- ALL THREE, because the pair is not unique either and the live instance
    -- proves it. Reverting a document and saving it again produces the same
    -- content under a later stamp, and `.history/` kept both -- the filename
    -- carried stamp AND version. Keying on (key, version) alone collapsed two
    -- of seven ontology snapshots and two of thirteen settings snapshots on the
    -- first import. The remaining conflict is the one the filename had too --
    -- same content, same second -- and it is the same no-op there and here.
    PRIMARY KEY (key, version, stamp)
);

-- Newest first, per document, which is the only way the panel reads it and
-- also what the keep-20 prune ranks on.
CREATE INDEX IF NOT EXISTS ix_config_document_history_key
    ON config_document_history (key, stamp DESC);

-- THE ARCHIVE BYTES THEMSELVES.
--
-- This column exists because the table's own promise rules out the obvious
-- alternative. Regenerating on download was considered and refused by the
-- comment two migrations back -- a second download must return the bytes
-- somebody already has, and rebuilding over a corpus that has moved hands back
-- a different folder under the same name.
--
-- NULLABLE, because the rows written before this migration point at files on
-- the disk being removed. `path` stays for exactly those, and the read path
-- prefers this column when it is present. A row with neither is not ready.
ALTER TABLE data_exports ADD COLUMN IF NOT EXISTS content BYTEA;
