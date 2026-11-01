-- The folders venona has written out, so one can be fetched again.
--
-- NOTE FOR ANYONE EDITING THIS FILE -- no comment here may contain a colon
-- followed by word characters. SQLAlchemy's `text()` reads a leading-colon
-- token as a bind parameter INSIDE comments as well as in statements, so a
-- file reference carrying a line number detonates the migration. The baseline
-- and 0025 carry the same warning for the same reason.

-- KEPT ON DISK rather than rebuilt on demand. A second download must return
-- the bytes somebody already has; rebuilding over a corpus that has moved
-- since would hand back a different folder under the same name, which is the
-- one thing a dated archive promises not to do.
--
-- THIS TABLE IS NOT PURGED. `services/purge.py` iterates an allowlist and this
-- table is absent from it, so "delete everything" leaves the files and their
-- rows alone. That is the point of the feature -- export first, then delete.
CREATE TABLE IF NOT EXISTS data_exports (
    id          TEXT PRIMARY KEY,
    -- NO FOREIGN KEY, and not an oversight. `runs` IS purged, so a reference
    -- would either take the export away with the run or refuse the delete, and
    -- both are wrong. Kept as provenance only -- the listing must never join
    -- through it, because after a purge every value here dangles.
    run_id      TEXT,
    filename    TEXT NOT NULL,
    -- Absolute, under the instance data directory. Stored rather than derived
    -- so a row written before the directory moved still names the file it
    -- actually wrote.
    path        TEXT NOT NULL,
    bytes       BIGINT NOT NULL DEFAULT 0,
    -- What a later download is checked against, and what lets a test compare
    -- two builds without committing an archive.
    sha256      TEXT NOT NULL,
    counts      TEXT NOT NULL DEFAULT '{}',
    -- ready, pruned, or missing. `pruned` means retention took it on purpose;
    -- `missing` means we went looking and it was gone. Collapsing the two
    -- would tell an administrator a lie about which one happened.
    status      TEXT NOT NULL DEFAULT 'ready',
    created_at  TEXT NOT NULL,
    -- Attribution, never ownership. Deleting the administrator who asked for a
    -- file must not delete the file.
    created_by  TEXT REFERENCES users(id) ON DELETE SET NULL,
    pruned_at   TEXT
);

CREATE INDEX IF NOT EXISTS ix_data_exports_created
    ON data_exports (created_at DESC, id DESC);

-- What makes the same-day suffix correct. Two exports on one date differ by a
-- trailing counter, and the writer asks THIS TABLE which names are taken --
-- never the directory, which can still hold an orphan from a run that was
-- killed between writing its file and recording it.
CREATE UNIQUE INDEX IF NOT EXISTS ux_data_exports_filename
    ON data_exports (filename);

-- Existing destinations get this one, because `default_on` alone would not
-- reach them.
--
-- `default_on` is read at exactly one moment -- when a destination is CREATED
-- (`api/routers/notifications.py`) -- and fan-out asks for a
-- `notify_subscriptions` row, where absence means no. So a notice type added
-- after an operator wired up their channel is silent there forever, and the
-- switch that says it is on by default is a promise to nobody who already
-- exists. The one instance where that matters most is the one that has been
-- running longest.
--
-- Additive and reversible from the screen. An operator who does not want it
-- unticks it, and the row stays unticked.
INSERT INTO notify_subscriptions (destination_id, notice_type, created_at)
SELECT d.id, 'export.finished', to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.MS"Z"')
FROM notify_destinations d
WHERE d.owner_kind = 'instance' AND d.status = 'active'
ON CONFLICT DO NOTHING;

