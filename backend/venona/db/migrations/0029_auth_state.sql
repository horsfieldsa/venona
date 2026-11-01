-- The instance-wide session kill switch, as a row.
--
-- NOTE FOR ANYONE EDITING THIS FILE -- no comment here may contain a colon
-- followed by word characters. SQLAlchemy's `text()` reads a leading-colon
-- token as a bind parameter INSIDE comments as well as in statements, so a
-- file reference carrying a line number detonates the migration. The baseline
-- and 0025 through 0028 carry the same warning for the same reason.

-- THIS REVERSES A DELIBERATE DECISION, so here is why it no longer holds.
--
-- `api/security.py` argued the epoch should stay a FILE rather than become a
-- table, on three grounds: it is the instance-wide kill switch, it has to work
-- when the database is the thing you are worried about, and it holds one float.
--
-- Removing the disk (ADR 0234) takes the first two apart. A file on a container
-- filesystem now lives until the next deploy, so as a persistent kill switch it
-- would SILENTLY STOP WORKING -- every cookie a logout revoked would come back
-- for the rest of its fourteen-day life at the next push. That is the exact
-- failure the comment above `read_epoch` describes and exists to prevent.
--
-- And the independence bought nothing. When the database is unreachable
-- `_lookup_user` fails closed by design and every request already renders as a
-- signed-out browser, so there is no state in which the file answers and the
-- row does not.
--
-- Per-user revocation was always the common path and has been a column on
-- `users` since identity landed. This is the break-glass beside it.
CREATE TABLE IF NOT EXISTS auth_state (
    -- The `hub_flags` singleton pattern, for the same reason: one row that
    -- always exists means no read is ever a special case.
    id      SMALLINT PRIMARY KEY CHECK (id = 1),
    -- TEXT holding a float, matching `users.session_epoch` exactly so the two
    -- can be compared without a conversion that could round differently.
    epoch   TEXT NOT NULL DEFAULT '0',
    updated_at TEXT
);

-- In the same transaction as the table, so no read is ever a special case.
INSERT INTO auth_state (id) VALUES (1) ON CONFLICT DO NOTHING;
