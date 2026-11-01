-- Identity. Contract C12, and the end of "one principal".
--
-- Before this there was no `users` table, no role column and no `current_user`
-- dependency. The only subject identifier was `Principal.email`, re-derived per
-- request from a frozenset parsed out of `VENONA_AUTH_ALLOWED_EMAILS`, and
-- `docs/DEPLOYMENT.md` said the design out loud: "An MCP token is you with a
-- shorter leash, not a second user."
--
-- It is now. Two roles:
--
--   admin   runs, curation, config, ingest, rebuild, purge, metrics, users.
--   reader  briefs, cards, glossary, graph, search, archive, guide, own account.
--
-- NOT a third role: agents. An MCP token belongs to a user and inherits that
-- user's role, narrowed by a reach scope. An admin's agent token is still
-- read-only, because C12's read-only-ness is structural -- `make lint-readonly`,
-- the symbol-level import allowlist and the row-count sweep -- and none of that
-- changes here.
--
-- WHAT THIS DELIBERATELY DOES NOT ADD: a `workspace_id` on the corpus tables.
-- There is one corpus, curated by admins and read by everyone. A discriminator on
-- twenty tables that nothing reads is speculative, and on Postgres 11+ adding a
-- NOT NULL column with a default later is metadata-only -- so deferring is close
-- to free. See ROADMAP for the trigger.

CREATE TABLE users (
  -- 'user:' + uuid7. Stable across an email change, which is the whole reason the
  -- id is not the address.
  id            TEXT PRIMARY KEY,
  -- Lowercased on write. UNIQUE because two rows for one address is an
  -- authorization question with two answers.
  email         TEXT NOT NULL UNIQUE,
  -- admin | reader. Not a CHECK constraint, for the reason
  -- `access_requests.role` is not one: the set is a product decision that will
  -- change. `api/authz.py::ROLES` is the gate, and a role outside it is refused
  -- at request time rather than at write time.
  role          TEXT NOT NULL DEFAULT 'reader',
  -- active | suspended. Suspension is a status, never a DELETE: "when did I turn
  -- this off, and why" is worth being able to answer, and a deleted row cannot.
  --
  -- CHECKED ON EVERY REQUEST, not just at login. The property this preserves is
  -- the one the env allow-list already had -- removing an address ended its
  -- sessions immediately rather than waiting out a fourteen-day cookie.
  status        TEXT NOT NULL DEFAULT 'active',
  -- Display name, from the OIDC `name` claim. Nullable: `AccountView.vue`
  -- currently refuses to render a name because the API does not serve one, and
  -- serving an empty string would be worse than serving nothing.
  display_name  TEXT,
  -- The OIDC identity, which is `iss` + `sub` and NOT the email: an address can
  -- be reassigned inside a tenant, a subject cannot. Both nullable because a row
  -- can be created by an admin before its owner has ever signed in, and are
  -- stamped on first successful login.
  oidc_issuer   TEXT,
  oidc_subject  TEXT,
  -- Per-user session revocation, replacing the single `data/auth_epoch` FILE.
  -- That file held one float for one principal, so one person logging out killed
  -- every session on the instance. Stored as TEXT for the same reason every other
  -- timestamp here is: ISO-8601 UTC with `Z`, compared lexically.
  --
  -- The global epoch file still exists and still wins: it is the break-glass
  -- "log everyone out", and `verify_session` takes the LATER of the two.
  session_epoch TEXT,
  created_at    TEXT NOT NULL,
  last_seen_at  TEXT
);
-- `iss` + `sub` is the identity, so it must be unique when present. Partial,
-- because both are NULL for a row whose owner has not signed in yet and several
-- such rows must be able to coexist.
CREATE UNIQUE INDEX ux_users_oidc ON users (oidc_issuer, oidc_subject)
  WHERE oidc_issuer IS NOT NULL AND oidc_subject IS NOT NULL;
CREATE INDEX ix_users_role ON users (role, status);

-- ── the agent credential grows an owner, a scope and an expiry ────────
--
-- All three were named in ROADMAP as not-built-with-a-trigger. The trigger for
-- every one of them is this migration: `mcp_tokens` had no owner because there
-- was one principal to own them.

-- NULL only for a token minted before this migration. Those are orphans and
-- `token_service.verify` refuses them rather than guessing an owner -- guessing
-- would hand a pre-identity credential whatever role the guess had.
ALTER TABLE mcp_tokens ADD COLUMN user_id TEXT REFERENCES users(id) ON DELETE CASCADE;

-- The reach scope this token is admissible on, resolved through
-- `api/authz.py::SCOPES`. 'mcp' is the historical behaviour and the default;
-- 'annotate' is the narrow set the browser extension needs, which is what lets
-- the extension stop carrying the ROOT bearer.
ALTER TABLE mcp_tokens ADD COLUMN scope TEXT NOT NULL DEFAULT 'mcp';

-- NULL means "does not expire", which is every historical token and remains the
-- default: an expiry that surprises an agent mid-run is worse than none. Set it
-- for a token issued to a machine the operator does not physically control.
ALTER TABLE mcp_tokens ADD COLUMN expires_at TEXT;

CREATE INDEX ix_mcp_tokens_user ON mcp_tokens (user_id) WHERE revoked_at IS NULL;

-- ── who decided, not just when ────────────────────────────────────────
--
-- Every one of these tables already recorded THAT a verdict was reached and
-- WHEN. None recorded WHO, because there was only ever one who. Contract C1.
--
-- All nullable, and NULL is honest rather than lazy: it means "decided before
-- this migration, or by the pipeline rather than a person". Backfilling a name
-- onto a verdict nobody signed would be inventing provenance, in a system whose
-- third invariant is that provenance is never invented.

ALTER TABLE alias_conflicts     ADD COLUMN resolved_by TEXT REFERENCES users(id);
ALTER TABLE acronym_definitions ADD COLUMN decided_by  TEXT REFERENCES users(id);
ALTER TABLE entity_definitions  ADD COLUMN decided_by  TEXT REFERENCES users(id);
ALTER TABLE entity_mappings     ADD COLUMN merged_by   TEXT REFERENCES users(id);

-- `merge_proposals` is NOT here, and looked like it should be. It was created by
-- `0001_init.sql` and DROPPED by `0006_drop_dead_columns.sql` -- "0 rows, 0
-- references anywhere in the repo" -- so it is absent from the Postgres baseline,
-- correctly. The trap is that `docs/schema.sql` is a CONCATENATION of the old
-- migrations and therefore contains both the CREATE and the DROP: read as a
-- schema it shows a table that has not existed for eighteen migrations.

-- HUMAN ownership, distinct from the two process-lease columns beside it.
-- `owner_pid` (and, until 0004, `owner_start_ticks`) answered "is the process that holds this
-- lane slot still alive"; this answers "who asked for it", which is a different
-- question that no column could answer before.
ALTER TABLE runs ADD COLUMN started_by TEXT REFERENCES users(id);
