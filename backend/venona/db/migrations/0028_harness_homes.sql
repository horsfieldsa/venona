-- A model service's own credential store, as a row.
--
-- NOTE FOR ANYONE EDITING THIS FILE -- no comment here may contain a colon
-- followed by word characters. SQLAlchemy's `text()` reads a leading-colon
-- token as a bind parameter INSIDE comments as well as in statements, so a
-- file reference carrying a line number detonates the migration. The baseline
-- and 0025 through 0027 carry the same warning for the same reason.

-- WHY THIS EXISTS (ADR 0234). Removing the api's persistent disk looked like it
-- meant giving up subscription auth for the services whose credential cannot be
-- pasted -- Codex writes and refreshes its own `auth.json` and offers nothing to
-- copy. It does not. Every harness already declares `home_env`
-- (`CLAUDE_CONFIG_DIR`, `CODEX_HOME`, Grok's equivalent) and every driver
-- already points the child at a directory of our choosing, so the LOCATION was
-- always ours; only the durability was missing.
--
-- WHAT IS KEPT IS THE CREDENTIAL, NOT THE HOME. Measured on a workstation
-- 2026-09-14 -- the Claude home is 1.5 GB and its credential file is 2,676
-- bytes; the Codex home is 138 MB and its `auth.json` is 4,178. The rest is a
-- models catalog fetched over the network, SQLite session state and logs, which
-- the API image already calls regenerable. So each driver declares the files
-- that carry authentication, and only those are kept.
CREATE TABLE IF NOT EXISTS harness_homes (
    -- The harness key, one row per service. Not keyed on billing mode as
    -- `harness_credentials` is -- a home belongs to the CLI, and a metered
    -- credential is an environment variable rather than anything on disk.
    harness     TEXT PRIMARY KEY,
    -- A JSON object of relative path to base64 content. Base64 because nothing
    -- promises these files are text, and an encoding guess that is wrong here
    -- corrupts the one thing this table exists to keep.
    --
    -- NO ROUTE RETURNS THIS, the same rule `harness_credentials.secret` carries.
    -- The capture is internal and the restore writes to a directory no route
    -- can read back.
    files       TEXT NOT NULL,
    -- The digest of what was captured, so an unchanged home is not rewritten on
    -- every call. This is checked before the write, not after.
    digest      TEXT NOT NULL,
    captured_at TEXT NOT NULL
);
