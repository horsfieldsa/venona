-- The Postgres baseline. The system of record (invariant 1).
--
-- This file REPLACES the twenty-four SQLite migrations that preceded it. They
-- are not lost -- they are in git history, and the decisions behind them are in
-- docs/DECISIONS.md -- but they existed to evolve a live SQLite database through
-- its history, and there is no such history to preserve on Postgres. Replaying
-- them would mean hand-translating 1231 lines of DDL that nobody will ever run
-- in sequence. Only the final schema matters, so only the final schema is here.
--
-- WHAT CHANGED IN TRANSLATION, and nothing else did:
--
--   REAL                     -> DOUBLE PRECISION
--   DEFAULT (datetime('now')) -> DEFAULT now(), on `schema_migrations` only,
--                                which `db/core.py::migrate` creates itself
--   brief_items.ord           NEW. See the comment on it.
--
-- Everything else is byte-for-byte portable, which was worth checking rather
-- than assuming: the whole schema uses three types, has no AUTOINCREMENT, no
-- INTEGER PRIMARY KEY rowid alias, no triggers, no views, and no virtual tables.
--
-- WHAT DELIBERATELY DID NOT CHANGE:
--
--   Timestamps stay TEXT, ISO-8601 UTC with `Z`, per CLAUDE.md's conventions.
--   They are compared LEXICALLY throughout (`MAX(created_at)`, `ORDER BY
--   generated_at DESC`), which is correct only because the format is
--   fixed-width -- and keeping it preserves the C3 and C6 wire formats for
--   free. Moving to timestamptz would break every `COALESCE(MAX(x), '')` and
--   hand every Python reader a datetime where it expects a string.
--
--   Booleans stay INTEGER. They are written as literal 0/1, compared as `= 1`,
--   and read through `bool(...)`; no Python True/False is ever bound. Making
--   them `boolean` would break all three at once for no gain.
--
--   JSON stays TEXT, with `db.jdump`/`db.jload` doing the work in Python. Two
--   queries reach inside it (`brief_blocks.spans`, `briefs.source_ids`) and
--   they cast at the query site. Migrating the columns to JSONB would hand
--   psycopg-adapted dicts to `jload`, which expects a string -- a change worth
--   making when repositories own those reads, not in the same step as the
--   engine swap.

CREATE TABLE sources (
  id          TEXT PRIMARY KEY,
  name        TEXT NOT NULL,
  kind        TEXT NOT NULL,              -- rss | discovery
  url         TEXT,
  publisher   TEXT,
  enabled     INTEGER NOT NULL DEFAULT 1,
  tags        TEXT,                       -- json array
  created_at  TEXT NOT NULL
);

CREATE TABLE source_health (
  source_id            TEXT PRIMARY KEY REFERENCES sources(id) ON DELETE CASCADE,
  last_success_at      TEXT,
  last_attempt_at      TEXT,
  last_item_at         TEXT,
  consecutive_failures INTEGER NOT NULL DEFAULT 0,
  last_error           TEXT,
  etag                 TEXT,
  last_modified        TEXT,
  auto_disabled        INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE documents (
  id             TEXT PRIMARY KEY,        -- sha256(url_key)
  run_id         TEXT,                    -- first run that saw it
  source_id      TEXT,
  url            TEXT NOT NULL,
  url_key        TEXT NOT NULL,           -- canonicalised; dedup layer 1
  title          TEXT NOT NULL,
  publisher      TEXT,
  author         TEXT,
  -- text_nfc is stored VERBATIM and never re-normalised: every evidence_span
  -- and acronym grounding offset in the database points into it, so mutating
  -- it silently invalidates all of them.
  text_nfc       TEXT NOT NULL,
  text_chars     INTEGER NOT NULL DEFAULT 0,
  excerpt        TEXT,
  lang           TEXT,
  published_at   TEXT,
  date_confidence TEXT NOT NULL DEFAULT 'unknown',  -- feed | parsed | header | unknown
  fetched_at     TEXT NOT NULL,
  content_hash   TEXT NOT NULL,           -- dedup layer 2
  simhash        INTEGER,                 -- reserved: dedup layer 3 (deferred)
  site_domain    TEXT,
  cluster_id     TEXT NOT NULL,
  duplicate_of   TEXT,                    -- non-null => not the canonical doc
  paywalled      INTEGER NOT NULL DEFAULT 0,
  created_at     TEXT NOT NULL
);
CREATE INDEX ix_documents_cluster   ON documents(cluster_id);
CREATE INDEX ix_documents_urlkey    ON documents(url_key);
CREATE INDEX ix_documents_hash      ON documents(content_hash);
CREATE INDEX ix_documents_published ON documents(published_at);
CREATE INDEX ix_documents_run       ON documents(run_id);

CREATE TABLE sightings (
  id          TEXT PRIMARY KEY,
  cluster_id  TEXT NOT NULL,
  document_id TEXT NOT NULL,
  source_id   TEXT,
  run_id      TEXT NOT NULL,
  seen_at     TEXT NOT NULL
);
CREATE INDEX ix_sightings_cluster ON sightings(cluster_id);

CREATE TABLE clusters (
  id                TEXT PRIMARY KEY,
  canonical_doc_id  TEXT,                 -- earliest published_at, ties by source order
  title             TEXT,
  published_at      TEXT,
  relevance_overall INTEGER,              -- 0-100, computed in Python
  tier              TEXT,                 -- must_read | worth_knowing | background
  relevance_note    TEXT,
  so_what           TEXT,
  axes              TEXT,                 -- json {axis_id: {score, why}}
  profile_hooks     TEXT,                 -- json array of verbatim profile phrases
  analyzed_at       TEXT,
  first_seen        TEXT NOT NULL,
  last_seen         TEXT NOT NULL,
  -- Both nullable, and NULL means "not captured", which is honestly different
  -- from "the model returned nothing".
  key_facts         TEXT,
  flags             TEXT
);
CREATE INDEX ix_clusters_relevance ON clusters(relevance_overall);
CREATE INDEX ix_clusters_published ON clusters(published_at);

CREATE TABLE entities (
  id            TEXT PRIMARY KEY,         -- C4: prefix:slug, minted once, immutable
  type          TEXT NOT NULL,            -- see config/ontology.yaml for the nine types
  name          TEXT NOT NULL,
  canonical_key TEXT NOT NULL,            -- normalize(name)
  expansion     TEXT,                     -- acronym expansion when name IS the acronym
  summary       TEXT,
  org_type      TEXT,                     -- Organization only
  status        TEXT NOT NULL DEFAULT 'active',   -- active | provisional | rejected
  relevance     DOUBLE PRECISION NOT NULL DEFAULT 0,
  mention_count INTEGER NOT NULL DEFAULT 0,
  source_count  INTEGER NOT NULL DEFAULT 0,
  props         TEXT,                     -- json, type-specific
  first_seen    TEXT NOT NULL,
  last_seen     TEXT NOT NULL,
  created_at    TEXT NOT NULL
);
CREATE INDEX ix_entities_type   ON entities(type);
CREATE INDEX ix_entities_status ON entities(status);
CREATE INDEX ix_entities_key    ON entities(canonical_key);

CREATE TABLE aliases (
  id                 TEXT PRIMARY KEY,
  entity_id          TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
  surface            TEXT NOT NULL,
  normalized         TEXT NOT NULL,
  strictness         INTEGER NOT NULL DEFAULT 1,
  is_acronym         INTEGER NOT NULL DEFAULT 0,
  added_by           TEXT NOT NULL,       -- seed|acronym_gen|extraction|acronym_lookup|manual
  evidence_article_id TEXT,
  created_at         TEXT NOT NULL,
  -- Keyed on SURFACE, not normalized. These are two different jobs:
  -- resolution looks up by `normalized`, but the annotator matches LITERAL
  -- text, so "Next Generation Command and Control" and
  -- "next-generation command and control" are distinct and both must exist.
  -- Keying on normalized would let one silently overwrite the other and the
  -- full form would stop matching in prose.
  UNIQUE (entity_id, surface)
);
CREATE INDEX ix_aliases_normalized ON aliases(normalized);
CREATE INDEX ix_aliases_entity     ON aliases(entity_id);

CREATE TABLE alias_conflicts (
  id             TEXT PRIMARY KEY,
  surface        TEXT NOT NULL,
  normalized     TEXT NOT NULL,
  existing_entity_id TEXT NOT NULL,
  proposed_entity_id TEXT NOT NULL,
  entity_type    TEXT NOT NULL,
  article_id     TEXT,
  resolved       INTEGER NOT NULL DEFAULT 0,
  created_at     TEXT NOT NULL,
  -- NULL means unclassified, which is honestly different from "classified as
  -- unknown".
  kind           TEXT,
  resolution     TEXT,
  -- Records WHEN a verdict was reached, never WHO reached it. Adding the actor
  -- is Slice 2 of the tenancy work and a contract C1 change.
  resolved_at    TEXT,
  -- The pipeline's proposal, for a human to accept or override. NULL means the
  -- pipeline has not looked at this row yet.
  suggestion     TEXT,       -- merge | part_of | distinct
  suggestion_note TEXT,      -- one line, the reason
  suggestion_child TEXT,     -- for part_of: the subject id
  suggested_at   TEXT,
  -- Which predicate a `part_of` suggestion means. Without it the UI submitted
  -- PART_OF whenever the option list was empty, so the one predicate that
  -- could not apply was the one it always sent.
  suggestion_predicate TEXT
);
CREATE INDEX ix_alias_conflicts_open ON alias_conflicts(resolved, kind);

CREATE TABLE ambiguous_surfaces (
  normalized TEXT PRIMARY KEY,
  reason     TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE assertions (
  id             TEXT PRIMARY KEY,        -- uuid7 from ctx.ids
  run_id         TEXT NOT NULL,
  article_id     TEXT NOT NULL,
  cluster_id     TEXT NOT NULL,
  kind           TEXT NOT NULL CHECK (kind IN ('entity','relation')),

  surface        TEXT,                    -- verbatim from the article
  entity_type    TEXT,
  entity_id_raw  TEXT,                    -- NEVER UPDATED. Merges go via entity_mappings.

  predicate      TEXT,
  subject_id_raw TEXT,
  object_id_raw  TEXT,

  evidence_quote TEXT NOT NULL,
  evidence_span  TEXT,                    -- json [start,end] codepoints into documents.text_nfc
  salience       DOUBLE PRECISION,
  confidence     DOUBLE PRECISION NOT NULL,
  method         TEXT NOT NULL,           -- seed|exact_alias|fuzzy|acronym_gen|manual|provisional
  model          TEXT,                    -- NULL for deterministic methods
  created_at     TEXT NOT NULL,
  retracted_at   TEXT,
  -- `predicate_kind`, not `kind` -- `assertions.kind` above is already the
  -- entity/relation discriminator.
  predicate_kind TEXT
);
CREATE INDEX ix_assertions_article  ON assertions(article_id);
CREATE INDEX ix_assertions_entity   ON assertions(entity_id_raw);
CREATE INDEX ix_assertions_subject  ON assertions(subject_id_raw);
CREATE INDEX ix_assertions_object   ON assertions(object_id_raw);
CREATE INDEX ix_assertions_run      ON assertions(run_id);
CREATE INDEX ix_assertions_cluster  ON assertions(cluster_id);
CREATE INDEX ix_assertions_predicate_kind ON assertions(predicate, predicate_kind);

CREATE TABLE entity_mappings (
  from_id    TEXT NOT NULL,
  to_id      TEXT NOT NULL,
  merge_id   TEXT NOT NULL,
  active     INTEGER NOT NULL DEFAULT 1,
  reason     TEXT,
  created_at TEXT NOT NULL,
  PRIMARY KEY (from_id, merge_id)
);
CREATE INDEX ix_mappings_active ON entity_mappings(active, from_id);

CREATE TABLE acronym_definitions (
  id                   TEXT PRIMARY KEY,   -- gloss:<slug>
  surface              TEXT NOT NULL,
  normalized           TEXT NOT NULL,
  expansion            TEXT,
  entity_id            TEXT,               -- set when it resolves to a real entity
  method               TEXT NOT NULL,      -- in_article|in_corpus|web_grounded|manual|undefined
  confidence           DOUBLE PRECISION NOT NULL DEFAULT 0,
  grounding_article_id TEXT,
  grounding_url        TEXT,
  grounding_quote      TEXT,
  grounding_span       TEXT,               -- json [start,end]
  status               TEXT NOT NULL DEFAULT 'active',  -- active|undefined|rejected|ambiguous
  sighting_count       INTEGER NOT NULL DEFAULT 0,
  article_count        INTEGER NOT NULL DEFAULT 0,
  first_seen           TEXT NOT NULL,
  last_seen            TEXT NOT NULL,
  -- A PERSON ruled. Never set by the pipeline. NULL means "never reviewed by a
  -- person", which is honestly different from "reviewed and left as-is".
  decided_at           TEXT,
  -- NULL means "never looked up", honestly different from "looked up and found
  -- nothing" -- the distinction the card needs to stop asking the user to spend
  -- money on a question already answered.
  last_attempt_at      TEXT,
  last_attempt_run_id  TEXT,
  attempt_count        INTEGER NOT NULL DEFAULT 0,
  -- Corroboration, counted from 1 (the row's own grounding). The first new
  -- piece of evidence seeds the list from the row's own `grounding_url` /
  -- `grounding_article_id`, so nothing is double-counted.
  corroboration_count  INTEGER NOT NULL DEFAULT 1,
  corroborating_sources TEXT,              -- json array
  UNIQUE (normalized, expansion)
);
CREATE INDEX ix_glossary_normalized ON acronym_definitions(normalized);
CREATE INDEX ix_glossary_status     ON acronym_definitions(status);

CREATE TABLE entity_definitions (
  entity_id            TEXT PRIMARY KEY,   -- C4 id. Merges resolve via entity_mappings.
  definition           TEXT,
  -- in_article | in_corpus | web_grounded | manual | undefined
  method               TEXT NOT NULL,
  confidence           DOUBLE PRECISION NOT NULL DEFAULT 0,

  -- Tier 1 and 3: ONE article, quoted verbatim.
  grounding_article_id TEXT,
  grounding_url        TEXT,
  grounding_quote      TEXT,
  grounding_span       TEXT,               -- json [start,end] codepoints

  -- Tier 2: written from several evidence sentences we already hold. A json
  -- array of article ids, never a quote -- the sentence is a synthesis and the
  -- card must never present it as a quotation.
  source_article_ids   TEXT,

  status               TEXT NOT NULL DEFAULT 'active',  -- active|undefined|rejected
  decided_at           TEXT,               -- a PERSON ruled. Never set by the pipeline.
  last_attempt_at      TEXT,
  last_attempt_run_id  TEXT,
  attempt_count        INTEGER NOT NULL DEFAULT 0,
  first_seen           TEXT NOT NULL,
  last_seen            TEXT NOT NULL
);
CREATE INDEX ix_entity_definitions_method ON entity_definitions(method);
CREATE INDEX ix_entity_definitions_status ON entity_definitions(status);

CREATE TABLE briefs (
  id            TEXT PRIMARY KEY,          -- brief:YYYY-MM-DD
  date          TEXT NOT NULL,
  run_id        TEXT,
  title         TEXT NOT NULL,
  dek           TEXT,
  generated_at  TEXT NOT NULL,
  model         TEXT,
  unresolved_mentions TEXT,                -- json array
  source_ids    TEXT,                      -- json array of document ids
  created_at    TEXT NOT NULL
);
CREATE INDEX ix_briefs_date   ON briefs(date DESC);
CREATE INDEX ix_briefs_recent ON briefs(date DESC, generated_at DESC);

CREATE TABLE brief_blocks (
  id         TEXT PRIMARY KEY,
  brief_id   TEXT NOT NULL REFERENCES briefs(id) ON DELETE CASCADE,
  section    INTEGER NOT NULL,
  heading    TEXT,
  ordinal    INTEGER NOT NULL,
  type       TEXT NOT NULL,               -- heading|paragraph|list_item|blockquote|code
  depth      INTEGER NOT NULL DEFAULT 0,
  text       TEXT NOT NULL,
  spans      TEXT NOT NULL DEFAULT '[]',  -- json [{start,end,ref,kind,confidence}]
  marks      TEXT NOT NULL DEFAULT '[]',  -- json [{start,end,type,href}]
  -- Which item a block belongs to, and which half of it. NULL on both for a
  -- legacy narrative section, which renders as it always did.
  item_id    TEXT,
  role       TEXT                          -- summary | why_it_matters
);
CREATE INDEX ix_blocks_brief ON brief_blocks(brief_id, section, ordinal);

CREATE TABLE brief_items (
  id            TEXT PRIMARY KEY,        -- item:<brief_id>:<nn>
  brief_id      TEXT NOT NULL,
  section       TEXT NOT NULL,           -- from profile/brief_sections.yaml
  rank          INTEGER NOT NULL,        -- order within the section, by consequence
  cluster_id    TEXT,                    -- the evidence; NULL only if it vanished
  headline      TEXT NOT NULL,           -- states the finding, not a clever construction
  state         TEXT NOT NULL,           -- new | follow_up | ongoing
  state_note    TEXT,                    -- "new detail on which components survive"
  top_rank      INTEGER,                 -- 1..3 for the TOP block, else NULL
  top_line      TEXT,                    -- the one-sentence hook, only when top_rank set
  -- NEW IN THE PORT, and the one column here that is not a translation.
  --
  -- Render order was `ORDER BY i.rowid` -- the insertion order, with no other
  -- ordering column on the table. Postgres has no rowid, so without this the
  -- brief would silently render its items in whatever order the heap returned,
  -- and nothing would report it. `rank` cannot stand in: it restarts per
  -- section. `id` very nearly can, since it is `item:<brief_id>:i{n:02d}` --
  -- but that is lexical and breaks at a hundred items.
  --
  -- `pipeline/brief.py` writes the `position` it already computes.
  ord           INTEGER NOT NULL DEFAULT 0,
  created_at    TEXT NOT NULL
);
CREATE INDEX ix_brief_items_brief ON brief_items(brief_id, section, rank);
CREATE INDEX ix_brief_items_top   ON brief_items(brief_id, top_rank);
CREATE INDEX ix_brief_items_ord   ON brief_items(brief_id, ord);

CREATE TABLE brief_clusters (
  brief_id   TEXT NOT NULL REFERENCES briefs(id) ON DELETE CASCADE,
  cluster_id TEXT NOT NULL,
  rank       INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  PRIMARY KEY (brief_id, cluster_id)
);
CREATE INDEX ix_brief_clusters_cluster ON brief_clusters(cluster_id);

CREATE TABLE runs (
  id           TEXT PRIMARY KEY,
  kind         TEXT NOT NULL,             -- C11: see services/run_types.py
  status       TEXT NOT NULL,             -- queued|running|succeeded|succeeded_partial|failed|cancelled
  stage        TEXT,
  params       TEXT,
  counters     TEXT,
  warnings     TEXT,
  error        TEXT,
  profile_hash TEXT,
  brief_id     TEXT,
  cost_usd     DOUBLE PRECISION NOT NULL DEFAULT 0,
  started_at   TEXT NOT NULL,
  finished_at  TEXT,
  -- PROCESS ownership, not human ownership: which OS process holds the lane
  -- slot. NULL for a row whose owner is unknown, which counts as ALIVE in both
  -- directions -- unknown ownership is not a reason to trust a `running` row
  -- that survived a restart, and not evidence that nothing is running.
  --
  -- These two read `/proc` and stamp `os.getpid()`, which is a single-machine
  -- assumption baked into contract C11. Replacing them with a database lease is
  -- part of this slice; they are carried across unchanged so that is one change
  -- rather than two.
  owner_pid    INTEGER,
  owner_start_ticks INTEGER,
  -- Hide, never delete.
  hidden_at    TEXT
);
CREATE INDEX ix_runs_started ON runs(started_at DESC);
CREATE INDEX ix_runs_visible ON runs(hidden_at, started_at DESC);

CREATE TABLE run_stages (
  run_id      TEXT NOT NULL,
  stage       TEXT NOT NULL,
  status      TEXT NOT NULL,
  counts      TEXT,
  error       TEXT,
  started_at  TEXT NOT NULL,
  finished_at TEXT,
  PRIMARY KEY (run_id, stage)
);

CREATE TABLE run_events (
  run_id TEXT NOT NULL,
  seq    INTEGER NOT NULL,
  ts     TEXT NOT NULL,
  type   TEXT NOT NULL,
  stage  TEXT,
  data   TEXT NOT NULL DEFAULT '{}',
  -- C6. `seq` is PER RUN, dense, and starts at 1, which is why it is not a
  -- sequence: a Postgres sequence is global and leaves gaps. See
  -- `run_service.append_event` for how it is allocated now that
  -- `BEGIN IMMEDIATE` is gone.
  PRIMARY KEY (run_id, seq)
);

CREATE TABLE run_metrics (
  run_id TEXT NOT NULL,
  stage  TEXT NOT NULL,
  key    TEXT NOT NULL,
  value  DOUBLE PRECISION NOT NULL,
  -- count | ms | ratio | hours
  unit   TEXT,
  at     TEXT NOT NULL,
  PRIMARY KEY (run_id, stage, key)
);

CREATE TABLE run_source_stats (
  run_id      TEXT NOT NULL,
  source_id   TEXT NOT NULL,
  offered     INTEGER NOT NULL DEFAULT 0,
  capped      INTEGER NOT NULL DEFAULT 0,
  kept        INTEGER NOT NULL DEFAULT 0,
  duplicate   INTEGER NOT NULL DEFAULT 0,
  skipped     INTEGER NOT NULL DEFAULT 0,
  fresh       INTEGER NOT NULL DEFAULT 0,
  stale       INTEGER NOT NULL DEFAULT 0,
  undated     INTEGER NOT NULL DEFAULT 0,
  age_p50_h   DOUBLE PRECISION,
  status      TEXT,
  error       TEXT,
  duration_ms INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (run_id, source_id)
);

CREATE TABLE llm_calls (
  id                         TEXT PRIMARY KEY,
  run_id                     TEXT,
  task                       TEXT NOT NULL,
  cache_key                  TEXT,
  model                      TEXT,
  models                     TEXT,        -- json {model: costUSD} -- phantom haiku lives here
  effort                     TEXT,
  tools                      TEXT,
  input_tokens               INTEGER NOT NULL DEFAULT 0,
  output_tokens              INTEGER NOT NULL DEFAULT 0,
  cache_read_input_tokens    INTEGER NOT NULL DEFAULT 0,
  cache_creation_input_tokens INTEGER NOT NULL DEFAULT 0,
  web_search_requests        INTEGER NOT NULL DEFAULT 0,
  -- Zero rather than NULL for a row written before this was recorded: NULL
  -- would break the existing counter reads for no gain.
  web_fetch_requests         INTEGER NOT NULL DEFAULT 0,
  cost_usd                   DOUBLE PRECISION NOT NULL DEFAULT 0,
  duration_ms                INTEGER NOT NULL DEFAULT 0,
  status                     TEXT NOT NULL,   -- ok | refused | error
  error                      TEXT,
  -- Both nullable: what makes per-stage cost and per-item latency answerable at
  -- all, and the aggregates tolerate their absence rather than dropping history.
  stage                      TEXT,
  subject                    TEXT,
  created_at                 TEXT NOT NULL
);
CREATE INDEX ix_llm_calls_run       ON llm_calls(run_id);
CREATE INDEX ix_llm_calls_run_stage ON llm_calls(run_id, stage);

CREATE TABLE access_requests (
  id           TEXT PRIMARY KEY,
  email        TEXT NOT NULL,
  -- Free text, and optional: the form asks what you cover, and "what venona is
  -- for" is exactly the question a stranger answers badly under a character
  -- limit. Capped in the schema layer, not here.
  coverage     TEXT,
  -- analyst | investor | press. A self-declared PERSONA, never a privilege --
  -- see `schemas/access.py::Role`, which is the gate. Not a CHECK constraint:
  -- the set is a product decision that will change.
  --
  -- Granting access is NOT a row in this table. Nothing in the request path can
  -- grant anything, and that separation survives the arrival of a real `users`
  -- table in Slice 2.
  role         TEXT NOT NULL,
  status       TEXT NOT NULL DEFAULT 'pending',
  created_at   TEXT NOT NULL,
  decided_at   TEXT,
  -- What the operator was looking at when they decided. Free text, for their
  -- own benefit six weeks later.
  note         TEXT
);
CREATE INDEX idx_access_requests_status ON access_requests (status, created_at DESC);

CREATE TABLE mcp_tokens (
  -- 'mcp_' + uuid7. NOT secret: this is what `revoke` takes and what a log line
  -- names, so it has to be safe to print.
  id            TEXT PRIMARY KEY,
  -- 'claude-code on the laptop'. The only thing that makes `ls` answerable six
  -- weeks later, which is why it is NOT NULL and the CLI requires it.
  label         TEXT NOT NULL,
  -- sha256 hex of the whole presented string, prefix included.
  --
  -- WHY NOT ARGON2. A password hash exists to make a dictionary attack on a
  -- CHOSEN secret expensive. This is 32 bytes from `secrets.token_hex` and
  -- `mint` takes no caller-supplied secret, so there is no dictionary to slow
  -- down. A 50-100ms KDF on every request of an agent doing 200 lookups is a
  -- real cost on a single-replica API, and it would mean a crypto dependency in
  -- a module whose docstring opens with "Zero new dependencies". The reasoning
  -- inverts the day a token can be chosen by a human;
  -- `test_mint_takes_no_caller_supplied_secret` pins the premise.
  --
  -- WHY NOT HMAC KEYED ON THE SESSION SECRET. It would buy nothing against 256
  -- bits of entropy, and it would silently unplug every agent the day the
  -- operator rotates that secret, with no error naming the cause.
  token_hash    TEXT NOT NULL UNIQUE,
  -- 'vmcp_' + the first six characters of the secret. DISPLAY ONLY, so `ls` and
  -- the settings page can be matched against what is pasted into a client
  -- config without revealing anything.
  prefix        TEXT NOT NULL,
  created_at    TEXT NOT NULL,
  -- Coarse, and written at most once a minute per token -- see
  -- `token_service.touch`. On SQLite the reason was that a write per read on a
  -- single-writer database that a forty-minute run is also appending to is how a
  -- read endpoint becomes an intermittent SQLITE_BUSY. Postgres does not
  -- serialise writers that way, so the throttle is now about write volume alone
  -- -- still worth keeping, for a weaker reason than the one it was built for.
  last_used_at  TEXT,
  -- Revocation is a timestamp, not a DELETE: "when did I turn this off" is a
  -- question worth being able to answer, and a deleted row cannot answer it.
  revoked_at    TEXT
);
CREATE INDEX idx_mcp_tokens_live ON mcp_tokens (token_hash) WHERE revoked_at IS NULL;
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
-- OAuth 2.1 for agents. Contract C12, and the end of the static bearer.
--
-- THE DESIGN DECISION THAT SHAPES EVERYTHING ELSE: the authorization code is
-- exchanged for an ORDINARY `mcp_tokens` ROW. There is no second credential
-- system, no parallel verification path and no second revocation story --
-- `token_service.verify` is still the only thing that turns a presented string
-- into a principal, `venona mcp token revoke` still kills an OAuth-issued token,
-- and C12's read-only-ness is untouched because it was never a property of how
-- the credential was obtained.
--
-- What OAuth adds is the WAY a token is minted: a client the operator never
-- typed a command for can obtain one, scoped and expiring, with the human
-- approving in a browser they are already signed into. What it does not add is a
-- new kind of thing to audit.
--
-- venona is BOTH the resource server and the authorization server. That sounds
-- like a shortcut and is the honest shape: there is no identity provider here to
-- delegate to -- the operator's own OIDC login is what establishes who is
-- approving, and an agent's authorization is a fact about THIS instance's
-- corpus, not a claim any external issuer could make.

CREATE TABLE oauth_clients (
  -- Issued by us at registration, opaque to the client. NOT secret: it is half
  -- of a public-client flow and appears in browser URLs.
  client_id      TEXT PRIMARY KEY,
  -- What the consent screen says. From the client's own metadata, so it is
  -- ATTACKER-CONTROLLED TEXT: rendered as text, never as markup, and the
  -- redirect URI is shown beside it because the name is the easy thing to lie
  -- about and the destination is the thing that matters.
  client_name    TEXT NOT NULL,
  -- JSON array. Exact-match only at authorize time -- no prefix, no wildcard,
  -- no "starts with". A redirect URI comparison that is anything less than
  -- equality is the classic way an authorization code ends up somewhere else.
  redirect_uris  TEXT NOT NULL,
  -- PUBLIC CLIENTS ONLY, so there is no client secret column and that is
  -- deliberate rather than unfinished. An MCP client is a desktop app or a CLI:
  -- anything it stores, the user can read, so a secret would be a secret in
  -- name only. PKCE is what actually binds the code to the client, and it is
  -- REQUIRED below rather than optional -- which is the OAuth 2.1 rule and the
  -- reason this file is not "OAuth 2.0 plus some extensions".
  created_at     TEXT NOT NULL,
  last_used_at   TEXT
);

CREATE TABLE oauth_codes (
  -- The authorization code itself, stored as a SHA-256 exactly like
  -- `mcp_tokens.token_hash` and for the same reason: a row someone can read
  -- must not contain a usable credential, and this one is usable for as long as
  -- it lives.
  code_hash      TEXT PRIMARY KEY,
  client_id      TEXT NOT NULL REFERENCES oauth_clients(client_id) ON DELETE CASCADE,
  -- WHO approved. The token minted from this code inherits this user's role, so
  -- this column is the whole authorization decision.
  user_id        TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  -- Echoed back at token time and compared for equality. RFC 6749 4.1.3.
  redirect_uri   TEXT NOT NULL,
  -- PKCE. `S256` only -- `plain` is permitted by RFC 7636 and forbidden by
  -- OAuth 2.1, and accepting it would mean the verifier travels in the same
  -- browser redirect as the challenge, which is the attack it exists to stop.
  code_challenge TEXT NOT NULL,
  -- Which reach set the resulting token gets. Resolved through
  -- `api/authz.py::SCOPES`, the same table the CLI validates against.
  scope          TEXT NOT NULL DEFAULT 'mcp',
  -- SHORT. A code is a one-time bearer that has just travelled through a
  -- browser redirect and possibly a log; ten minutes is the RFC's ceiling and
  -- there is no reason to sit at it.
  expires_at     TEXT NOT NULL,
  created_at     TEXT NOT NULL,
  -- SINGLE USE, enforced by recording the redemption rather than by deleting
  -- the row. A deleted row cannot tell a replayed code from an unknown one, and
  -- the difference matters: a replay means the code leaked, which is worth being
  -- able to see. `oauth_service.redeem` refuses a row that already has this set.
  redeemed_at    TEXT
);
CREATE INDEX ix_oauth_codes_expiry ON oauth_codes (expires_at);

-- Which client an issued token came from, and NULL for one minted at the CLI.
-- Not a foreign key onto a cascade: revoking a client's registration must not
-- silently delete the audit trail of what it was given.
ALTER TABLE mcp_tokens ADD COLUMN client_id TEXT;
-- Run liveness moves from `/proc` to a Postgres advisory lock. Contract C11.
--
-- WHAT WAS HERE. Migration 0008 added `runs.owner_pid` so the API could tell a
-- crashed run from one executing in another process -- `venona run` is a
-- separate pid and "the server started" is not evidence that a run died.
-- Migration 0020 added `owner_start_ticks`, field 22 of `/proc/<pid>/stat`,
-- because a pid alone is not an identity: in a container the API is pid 1 (or
-- ~7 under an init shim), the previous container's `owner_pid` values land on
-- the new container's own processes, the cmdline check passes because it really
-- is uvicorn, and the research lane wedges forever.
--
-- WHY IT GOES. The pair was correct on ONE host and could not be made correct
-- across two: `/proc/<pid>` is a statement about this kernel, and a pid read
-- from a row written by another machine is meaningless. It also required the
-- reader to be on the same box as the writer, which is the assumption the whole
-- deployment is trying to stop making.
--
-- WHAT REPLACES IT. A session-level advisory lock, keyed on the run id, claimed
-- by `create_run` on the connection the run will use and held until that
-- connection ends. Postgres releases it on ANY ending -- a clean close, a
-- SIGKILL, a container going away, a machine going away -- so there is nothing
-- to time out and nothing to heartbeat. That matters in both directions: no
-- window in which a dead run still looks alive, and no risk of declaring a live
-- run dead because a heartbeat was late. See `db/repositories/run.py`.
--
-- ONE RULE INVERTED, deliberately. Unknown ownership used to count as LIVE,
-- because a NULL `owner_pid` could mean "an old row" as easily as "nothing is
-- running", and wrongly deciding a live run is dead is the expensive direction.
-- A lease removes the ambiguity instead of resolving it: "no lock" is positive
-- evidence that no session is executing the row. Keeping the old asymmetry would
-- now mean a lane wedged permanently by every crash.
--
-- `owner_pid` STAYS, demoted to diagnostics. "Which process is running this" is
-- the first thing an operator asks and a lock id cannot answer it. Nothing
-- decides anything from it.

ALTER TABLE runs DROP COLUMN IF EXISTS owner_start_ticks;
-- Authority becomes something you can change from a screen, and therefore
-- something that has to be recorded. Contract C12's authorization addendum.
--
-- WHAT CHANGED ABOVE THIS FILE. `venona users` was CLI-only, and the argument
-- for that was explicit: an HTTP endpoint that grants a role is reachable by
-- anything holding a session, so the first admin would be minted by whoever got
-- there first. That argument survives, narrowed to the half that carries it:
--
--   Over HTTP, authority may be REDUCED without limit, and EXTENDED only as far
--   as `reader`. Crossing into `admin` still requires shell access.
--
-- Granting a reader is not minting a credential -- the credential is the
-- identity provider's, and the address still has to be one its owner can
-- authenticate as. Granting an ADMIN hands out model spend, every `config/*`
-- read including the operator's own relevance profile, and `POST /api/data/purge`,
-- which is the escalation-in-duration C12 refuses for token minting.
--
-- WHY AN AUDIT TABLE IS THE PRICE OF THE SCREEN. A change made over the CLI is
-- attributable to shell access, which is already the highest credential on the
-- box. A change made over HTTP is attributable to nothing unless something
-- writes it down. This table is what makes the HTTP surface reviewable, and it
-- is therefore not optional in the way "add logging" usually is.

CREATE TABLE user_events (
  -- 'uevent:' + uuid7. Sortable by creation, which is the only order this is
  -- ever read in.
  id         TEXT PRIMARY KEY,
  -- WHO IT HAPPENED TO. Not ON DELETE CASCADE, and not by omission: a user is
  -- never deleted (see `users.status`), and if that ever changes, the record of
  -- what was done to them is exactly the thing that must not go with them.
  user_id    TEXT NOT NULL REFERENCES users(id),
  -- WHO DID IT, or NULL. NULL is honest and common, and means the same thing it
  -- means on every other actor column in this schema (contract C1): the CLI
  -- decided, or a process did. It is never backfilled -- putting a name on a
  -- change nobody signed would invent provenance, which invariant 3 forbids
  -- everywhere else.
  actor_id   TEXT REFERENCES users(id),
  -- 'role_changed' | 'suspended' | 'reinstated' | 'created'. Not a CHECK
  -- constraint, for the reason `users.role` is not one: the set is a product
  -- decision that will change, and a CHECK is the wrong place to learn that.
  action     TEXT NOT NULL,
  -- One line, for a person reading the log six weeks later: 'admin -> reader',
  -- or the id of the access request an account was created from. Free text
  -- BECAUSE it is for a human -- anything a query needs belongs in a column.
  detail     TEXT,
  created_at TEXT NOT NULL
);
-- The only query this table has: the last N, newest first. Every panel and every
-- CLI listing reads it that way and nothing reads it any other way.
CREATE INDEX ix_user_events_recent ON user_events (created_at DESC);
-- And "what happened to this person", which is the second question anyone asks
-- once they have seen the first answer.
CREATE INDEX ix_user_events_user ON user_events (user_id, created_at DESC);

-- ── the access queue joins up to real users ───────────────────────────
--
-- `0001_baseline.sql` promises, on this table: "Granting access is NOT a row in
-- this table. Nothing in the request path can grant anything." Both columns
-- below are written by an ADMIN action on a different route with a different
-- credential, never by the public form, so that promise is unchanged. What they
-- add is the ability to answer "who decided, and what did this request become",
-- which the table recorded neither of.

-- Who approved or declined it. Same nullable-actor convention as everything
-- else: NULL means `venona access decide` did it.
ALTER TABLE access_requests ADD COLUMN decided_by TEXT REFERENCES users(id);

-- The account this request became, if it became one. NULL for pending, for
-- declined, and for every row decided before this migration -- which is not
-- backfillable, because matching on the address afterwards would be a guess
-- about which request produced an account, and a guess is what provenance is
-- not.
ALTER TABLE access_requests ADD COLUMN user_id TEXT REFERENCES users(id);
-- Which domain descriptor a run executed under.
--
-- `runs.profile_hash` answers "which profile scored this" and its recipe is
-- pinned to the four profile files -- folding the descriptor in would silently
-- re-explain every historical cache-miss, so the descriptor gets its own
-- column instead (docs/adr/0138). Stamped centrally in
-- `run_service.create_run` from `domain.domain_hash()`, truncated the way
-- `profile_hash` is: enough to compare, short enough to read.
--
-- Nullable: every row written before this column existed, and nothing joins
-- on it -- it is attribution, read by a human asking "did the wizard apply a
-- new domain mid-week".
ALTER TABLE runs ADD COLUMN domain_hash TEXT;
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
-- The account's avatar, from the OIDC `picture` claim (Google's own, or
-- forwarded by the hub's id_token). Refreshed on every login that carries the
-- claim and preserved through one that does not -- the opposite rule from
-- display_name, because the picture is the provider's to keep current while
-- the name is the person's to keep.
ALTER TABLE users ADD COLUMN picture_url TEXT;
-- Learning: per-user spaced-repetition state, the review log, the generated
-- question bank, and per-user knobs (docs/adr/0157, epic O).
--
-- Cards themselves are NOT stored. A card is a rendering of a row that already
-- carries invariant 3's provenance -- an entity definition, a gloss, a curated
-- assertion, an approved generated question -- and materialising it would copy
-- a sentence-traceable fact into a row that goes stale when the definition is
-- edited, rejected, or its entity merged. Deriving at read time makes "card
-- exists <=> sourced fact exists" structural. What persists is only what
-- cannot be derived: a learner's memory state and the log of their graded
-- reviews.
--
-- PRIVACY (the operator's stance, ADR 0157): rows in the two per-user tables
-- are created only by the review endpoint, whose subject is always the acting
-- user, and read only by learner-scoped routes. There is no admin surface over
-- them. These are the first genuinely per-user data tables in this schema; the
-- deferred `workspace_id` note in 0001 still holds for the corpus tables.

-- Per-user FSRS memory state, one row per (user, card). A row exists only
-- after the user's FIRST explicit grade: "new" is the absence of a row, so an
-- untouched deck writes nothing about anybody. When the underlying fact stops
-- deriving (definition rejected, entity merged away, question retired) the row
-- goes dormant -- never served, never deleted -- because memory state is
-- history, and a fact that returns wakes it with that history intact.
CREATE TABLE learning_cards (
    user_id     TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    -- card:def:<entity_id> | card:gloss:<gloss_id> | card:rel:<assertion_id>
    -- | card:q:<question_id>. Opaque to SQL; the service parses the first two
    -- segments. One row per FACT -- formats (flashcard/choice/cloze) are
    -- renderings rotated per review, not separate schedules.
    card_key    TEXT NOT NULL,
    state       TEXT NOT NULL,              -- learning | review | relearning
    stability   DOUBLE PRECISION NOT NULL,  -- FSRS S, days
    difficulty  DOUBLE PRECISION NOT NULL,  -- FSRS D, 1..10
    due         TEXT NOT NULL,              -- ISO-8601 Z, compared lexically
    last_review TEXT NOT NULL,
    reps        INTEGER NOT NULL DEFAULT 0,
    lapses      INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    PRIMARY KEY (user_id, card_key)
);

-- The queue read is "this user's cards due by now", so the index leads with
-- the user and orders by the due text.
CREATE INDEX ix_learning_cards_due ON learning_cards (user_id, due);

-- Append-only. The daily series, retention, streak and goal all DERIVE from
-- this log; there is no session table because a session is not an act of
-- study -- a streak is "distinct days with at least one review", and a row
-- per review records strictly less than a row per sitting would.
CREATE TABLE learning_reviews (
    id           TEXT PRIMARY KEY,           -- 'review:' + uuid7
    user_id      TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    card_key     TEXT NOT NULL,
    rating       INTEGER NOT NULL CHECK (rating BETWEEN 1 AND 4),  -- Again..Easy
    -- The card's state BEFORE the grade. Retention is computed over non-new
    -- reviews only: a first meeting is not a memory test, and counting it
    -- would flatter or damn the number depending on the deck's age.
    review_kind  TEXT NOT NULL,              -- new | review | relearning
    format       TEXT NOT NULL,              -- flashcard | choice | cloze | relation
    -- Days since the previous review of this card, 0.0 on the first. Stored
    -- rather than recomputed so the log stays interpretable if the state row
    -- is ever reset.
    elapsed_days DOUBLE PRECISION NOT NULL,
    -- What was scheduled when the grade landed. Audit only: it lets "was this
    -- review early or late" be answered from the log alone.
    due_at       TEXT,
    created_at   TEXT NOT NULL
);

-- Both the day-bucketed series and "reviews today" scan one user's recent
-- rows; created_at is ISO text, so the range and the substr() bucketing both
-- ride this index.
CREATE INDEX ix_learning_reviews_user_day ON learning_reviews (user_id, created_at);

-- The generated question bank. Proposed by a run, ruled on by a person: a row
-- is born 'proposed' and enters nobody's deck until an admin makes it
-- 'active' -- the same posture as merges and definitions, where model output
-- is never self-admitting.
CREATE TABLE llm_questions (
    id                   TEXT PRIMARY KEY,  -- 'question:' + uuid7
    run_id               TEXT NOT NULL,
    kind                 TEXT NOT NULL,     -- choice | cloze | qa
    -- Resolved by CODE from surfaces via the alias index; the model never
    -- emits an id. NULL when no surface resolved -- the question still works,
    -- it just carries no entity colour.
    entity_id            TEXT,
    prompt               TEXT NOT NULL,
    choices              TEXT,              -- json array; NULL unless kind = 'choice'
    answer               TEXT NOT NULL,
    -- Bare TEXT like every other grounding_* column in this schema: the
    -- article may be pruned later and the quote must still say where it came
    -- from.
    grounding_article_id TEXT NOT NULL,
    -- Verified by code as an exact substring of that article's text_nfc
    -- before the row is written; a paraphrase is rejected in the run, so a
    -- stored quote is always a real quotation (invariant 3).
    grounding_quote      TEXT NOT NULL,
    grounding_span       TEXT,              -- json [start, end], computed by the verifier
    status               TEXT NOT NULL DEFAULT 'proposed',  -- proposed | active | rejected | retired
    decided_by           TEXT,              -- users.id; NULL = never reviewed. Never backfilled.
    decided_at           TEXT,
    created_at           TEXT NOT NULL
);

-- The review queue lists by status; the run detail page lists by run.
CREATE INDEX ix_llm_questions_status ON llm_questions (status);
CREATE INDEX ix_llm_questions_run ON llm_questions (run_id);

-- Per-user knobs. An absent row means the code defaults, and the row is
-- created on first PATCH -- so, like learning_cards, an untouched feature
-- stores nothing.
CREATE TABLE learning_settings (
    user_id          TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    new_per_day      INTEGER NOT NULL DEFAULT 10,
    daily_goal       INTEGER NOT NULL DEFAULT 30,   -- reviews/day the streak ring fills against
    -- FSRS schedules for this recall probability. A learner preference, not
    -- an operator decision, which is why it lives here and not in profile/.
    retention_target DOUBLE PRECISION NOT NULL DEFAULT 0.9,
    updated_at       TEXT NOT NULL
);
-- Personal relevance: per-user emphasis, followed entities, and the reader's
-- own generated brief (ADR: per-user relevance is derived, never re-scored).
--
-- Nothing per-(user, cluster) is stored. A personal score is
-- compute_overall(clusters.axes, the user's derived weights) at read time, so
-- consistency with the corpus is structural -- the same derive-don't-copy
-- posture as learning cards (0009). What persists is only what cannot be
-- derived: the reader's stated emphasis, their explicit follows, and the one
-- prose document a model call produced for them.
--
-- PRIVACY (the operator's stance, ADR 0157 extended): rows here are created
-- only by routes whose subject is always the acting user, and read only by
-- reader-scoped routes. There is no admin surface over them. An absent row
-- means the instance defaults, so an untouched feature stores nothing about
-- anybody.

-- The reader's emphasis over the instance rubric's axes, as words rather
-- than weights: {axis_id: 'more' | 'normal' | 'less'}. Weights are DERIVED
-- server-side (instance default x factor, renormalized to sum 1.0) so no
-- stored row can put `overall` off the 0-100 scale, and the tier cuts scale
-- with the reader's reachable maximum -- the unreachable-cut bug (ADR 0047)
-- is prevented structurally rather than validated away.
CREATE TABLE relevance_settings (
    user_id    TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    emphasis   TEXT NOT NULL,   -- json {axis_id: level}
    updated_at TEXT NOT NULL
);

-- Names the reader follows. Explicit acts only -- a follow affordance or the
-- picker -- never inferred from reading (the no-passive-tracking stance).
-- Follows GROUP the personal reading list; they never enter the 0-100, which
-- is calibrated by the rubric weights alone. entity_id is bare TEXT like
-- every other entity reference outside the corpus tables: the entity may be
-- merged away, and the read path resolves through entity_mappings, so a
-- dormant follow wakes if its entity returns.
CREATE TABLE entity_follows (
    user_id    TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    entity_id  TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (user_id, entity_id)
);

-- The reader's own brief: one prose document per (user, day), written by one
-- light-lane model call over their personally ranked items. SEPARATE from
-- briefs/brief_blocks so the shared archive, the brief_clusters exclusion
-- ledger, and every existing brief query never see a personal document.
-- inputs_hash covers emphasis + follows + the run's profile hash: a revisit
-- with unchanged inputs is a free read, an emphasis edit regenerates at most
-- once.
CREATE TABLE personal_briefs (
    id           TEXT PRIMARY KEY,          -- 'pbrief:' + uuid7
    user_id      TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    date         TEXT NOT NULL,
    run_id       TEXT,
    inputs_hash  TEXT NOT NULL,
    title        TEXT NOT NULL,
    dek          TEXT,
    generated_at TEXT NOT NULL,
    created_at   TEXT NOT NULL
);

-- The read is "this user's brief for today"; regeneration replaces the row,
-- so the day holds at most one.
CREATE UNIQUE INDEX ix_personal_briefs_user_date ON personal_briefs (user_id, date);

-- Same shape as brief_blocks: the personal document goes through the same
-- markdown -> blocks -> spans annotation path, so the reader's brief gets
-- hoverable mentions exactly like the shared one.
CREATE TABLE personal_brief_blocks (
    id       TEXT PRIMARY KEY,
    brief_id TEXT NOT NULL REFERENCES personal_briefs(id) ON DELETE CASCADE,
    section  INTEGER NOT NULL,
    heading  TEXT,
    ordinal  INTEGER NOT NULL,
    type     TEXT NOT NULL,
    depth    INTEGER NOT NULL,
    text     TEXT NOT NULL,
    spans    TEXT NOT NULL,
    marks    TEXT NOT NULL,
    item_id  TEXT,
    role     TEXT
);

CREATE INDEX ix_personal_brief_blocks_brief ON personal_brief_blocks (brief_id);
-- The reader's brief becomes items: the shared brief's machinery, minus the
-- section desks. The reader's ranking IS the order, so there is no section
-- column and `ord` alone carries the document's order (the supplied-ranking
-- position, assigned by code -- the model's own ordering is not trusted).
--
-- CASCADE, unlike brief_items' deliberate no-FK: regeneration replaces the
-- day's personal_briefs row in place and relies on the cascade to take the
-- old document's children with it, exactly as personal_brief_blocks already
-- does. Without it every regeneration would orphan the previous items.
CREATE TABLE personal_brief_items (
    -- The id is the brief id plus an iNN suffix. No colon in this comment:
    -- alembic runs this file through SQLAlchemy's textual SQL, where a colon
    -- followed by a word is a bind parameter -- even inside a comment.
    id         TEXT PRIMARY KEY,
    brief_id   TEXT NOT NULL REFERENCES personal_briefs(id) ON DELETE CASCADE,
    cluster_id TEXT,
    headline   TEXT NOT NULL,
    state      TEXT NOT NULL,         -- new | follow_up | ongoing
    state_note TEXT,
    top_rank   INTEGER,               -- 1..3 for the TOP block, else NULL
    top_line   TEXT,
    ord        INTEGER NOT NULL,      -- the render order
    created_at TEXT NOT NULL
);

CREATE INDEX ix_personal_brief_items_brief ON personal_brief_items (brief_id, ord);
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
-- Who changed what the model is told.
--
-- `0005_authority.sql` made the argument for `user_events` and it applies here
-- with more force, not less:
--
--     A change made over HTTP is attributable to nothing unless something
--     writes it down.
--
-- Editing `relevance_profile.md` or `research_prompt.md` changes what every
-- reader's brief is written from, for every run after it, and until now it was
-- the one write class in the system with no trail. `run_launcher` records
-- `started_by`, curation verdicts carry `actor_id`, authority changes write
-- `user_events` -- config writes recorded a log line and a `.bak` file named
-- after its own mtime. Twenty of those, in a ring, with no author (ADR 0166).
--
-- THE POINT IS THE JOIN. `runs.profile_hash` already records which profile a run
-- executed under and `runs.domain_hash` does the same for the descriptor, so a
-- row here turns two half-answers into one: *this* person wrote *this* version
-- at *this* time, and *those* runs ran under it.
CREATE TABLE config_events (
  -- 'cevent:' + uuid7. Sortable by creation, the only order this is read in.
  id         TEXT PRIMARY KEY,
  -- The `config_service.EDITABLE` key -- 'relevance_profile', 'ontology'. Not a
  -- foreign key and not a CHECK: the editable set is a product decision that
  -- changes, and a document removed from `EDITABLE` must not take the record of
  -- who last edited it along with it.
  key        TEXT NOT NULL,
  -- WHO DID IT, or NULL. NULL is honest and common, and means what it means on
  -- every other actor column in this schema (contract C1): the CLI decided, or a
  -- process did -- the setup wizard and the hub's provisioning call both write
  -- config with no person behind them. It is never backfilled; putting a name on
  -- a change nobody signed would invent provenance, which invariant 3 forbids
  -- everywhere else.
  actor_id   TEXT REFERENCES users(id),
  -- The first sixteen hex characters of sha256(content) -- the same value
  -- `write_doc` already computes for conflict detection, so the row names a
  -- version that can be recognised rather than merely a moment.
  --
  -- Spelled out in words rather than as a Python slice, and that is not style.
  -- `op.execute` hands this file to SQLAlchemy's `text()`, which scans the WHOLE
  -- file for bind-parameter markers and does not skip comments. A colon followed
  -- by word characters -- in a slice, or in prose explaining one -- is read as a
  -- parameter, and the migration dies with "a value is required for bind
  -- parameter". Nothing in a comment here may carry that shape.
  version    TEXT NOT NULL,
  -- What it replaced, or NULL for the first write of a document. Two versions is
  -- what makes a row answer "what changed" without reading the `.history/` ring,
  -- which prunes at 20 and this table does not.
  prev       TEXT,
  bytes      INTEGER NOT NULL,
  created_at TEXT NOT NULL
);
-- The only query this table has: the last N, newest first, exactly like
-- `user_events`. The admin rail reads it that way and nothing reads it another.
CREATE INDEX ix_config_events_recent ON config_events (created_at DESC);
-- And "what happened to this document", which is the second question anyone
-- asks once they have seen the first answer.
CREATE INDEX ix_config_events_key ON config_events (key, created_at DESC);
-- How sure the adjudicator was.
--
-- The verdict alone was enough while every verdict was a proposal a human read.
-- It is not enough now that a `merge` verdict can be applied without one: the
-- gate needs a signal the model itself controls, so a pair it is guessing about
-- stays in the queue rather than being merged on the strength of the same word
-- it uses when it is sure.
--
-- Free TEXT, matching `kind` and `suggestion` beside it: the enum lives in the
-- response schema (`llm/schemas.py::adjudicate_schema`), which is where it is
-- actually enforced, and a CHECK here would have to be migrated every time that
-- vocabulary moved.
ALTER TABLE alias_conflicts ADD COLUMN IF NOT EXISTS suggestion_confidence TEXT;
-- The reader's brief gets the shared brief's shape: the same desks, and the
-- Sources footer the shared record has always carried.
--
-- The desk is on the item because that is where the shared brief keeps it and
-- because the personal document's order is derived from it -- desks in the
-- profile's declared order, the reader's ranking inside each. The ranking is
-- not discarded by the grouping; it decides what sits at the top of a desk.
--
-- `source_ids` holds the reader's WHOLE ranked window, not the handful of
-- stories the brief argued about. That is the shared brief's meaning of the
-- word (everything retrieved) and a footer that listed only the written-about
-- articles would quietly claim a narrower reading than actually happened.
ALTER TABLE personal_briefs ADD COLUMN source_ids TEXT;
ALTER TABLE personal_brief_items ADD COLUMN section TEXT;
-- What the subscription had left, sampled from the model calls we already make.
--
-- A row is a point observation of ONE window, not a property of a call, which
-- is why it is not a column on `llm_calls`. A single `rate_limit_event` carries
-- every window at once, so one call contributes several rows, and the same
-- window is re-observed by every later call until it moves. Kept as a series
-- rather than a single current value because "how fast is this instance burning
-- its week" is the question a bare percentage cannot answer.
--
-- `utilization` is the 0-to-1 fraction the CLI reports, unscaled. Storing what
-- the source said means the one conversion to a percentage lives at the render
-- boundary and cannot disagree with a second copy of itself.
--
-- The unique index is what makes recording idempotent. Every call re-observes
-- an unmoved window, so without it a single run would write hundreds of
-- identical rows; with it the insert is ON CONFLICT DO NOTHING and never
-- becomes a read-then-write, which at READ COMMITTED is the race CLAUDE.md
-- warns about. A window only earns a row when its reading actually changes.
--
-- `window_name`, not `window`: WINDOW is a reserved word in Postgres (the
-- function frame clause), so the natural spelling would need quoting at every
-- call site forever.
CREATE TABLE quota_samples (
  id           TEXT PRIMARY KEY,
  observed_at  TEXT NOT NULL,
  window_name  TEXT NOT NULL,
  utilization  DOUBLE PRECISION NOT NULL,
  resets_at    TEXT NOT NULL,
  -- allowed | allowed_warning | rejected. Of the whole reading, not this
  -- window: the CLI reports one status across the set.
  status       TEXT NOT NULL,
  -- True when this is the window the CLI itself considers closest to binding.
  -- Not derivable from `utilization` -- the fullest window and the binding one
  -- are not always the same, and the CLI's own warning names this one.
  representative BOOLEAN NOT NULL DEFAULT FALSE,
  -- Nullable: a light-lane call outside any run still observes a window, and
  -- an unattributed reading is honest where a fabricated run id would not be.
  run_id       TEXT
);

CREATE UNIQUE INDEX ux_quota_samples_reading
  ON quota_samples(window_name, resets_at, utilization);

CREATE INDEX ix_quota_samples_window ON quota_samples(window_name, observed_at DESC);
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
-- How a reader wants things shown to them, as opposed to what they are shown.
--
-- The first per-user preference that is about PRESENTATION rather than
-- ranking, which is why it gets its own table instead of a column on
-- `relevance_settings`. The presence of a `relevance_settings` row is itself a
-- signal -- it is what `SessionInfo.relevance_tailored` reports, and what
-- decides whether the first-visit tailor invitation appears -- so a reader who
-- only ever set a clock would be recorded as having tailored their relevance,
-- and would stop being asked to.
--
-- `timezone` is an IANA name, validated through `zoneinfo.ZoneInfo` in
-- `services/preferences_service.py` before it is ever written. NULL is the
-- default and means "use whatever zone the reader's browser reports", which is
-- right far more often than any value this table could guess -- and is the only
-- answer that works before anybody has visited a settings page.
--
-- Same privacy stance as `relevance_settings` and `learning_settings`: rows are
-- created only by a route whose subject is always the acting user, read only by
-- reader-scoped code, and there is no admin surface over them. An absent row
-- means the instance default.
CREATE TABLE user_preferences (
    user_id    TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    timezone   TEXT,
    updated_at TEXT NOT NULL
);
-- Aliases nobody wrote, and one a person withdrew.
--
-- `acronym_variants` output is written to `aliases` at the tier reserved for
-- all-caps acronyms, so a generated form annotates arbitrary prose exactly as a
-- form a publication actually prints does. Two classes of that output were
-- never right, and both were being shown to readers:
--
--   a person's initials    `Adm. Brad Cooper` generates ABC, `Bassam al-Hassan`
--                          generates BAH. Nobody writes a person that way, and
--                          both collide with real acronyms -- BAH with Booz
--                          Allen Hamilton. The ontology now declares which of
--                          its types are abbreviated at all.
--
--   punctuated forms       `"U.S.".isupper()` is true, so the generator reads
--                          it as an already-acronym tail and pastes it in
--                          whole: NU.S. from `U.S. Navy`, SCU.S. from `U.S.
--                          Space Command`, MOSA("MOSA"), N[C2]. No publication
--                          prints any of them, and they were listed on the
--                          entity's own hover card as things it is called.
--
-- The generator stopped producing both, which fixes tomorrow. This is the rows
-- already written. Delete rather than demote: they are derivable, wrong, and a
-- demoted row still reads back as an alias to every surface that lists them.
--
-- Deleted on `entities.type`, not on an id prefix. The prefix is ontology data,
-- editable at runtime and different per domain, and nothing lints a literal in
-- a `.sql` file -- so a prefix here would be a claim no one checks. A domain
-- with no Person type matches nothing, which is the correct answer for it.
DELETE FROM aliases a
 USING entities e
 WHERE a.entity_id = e.id
   AND e.type = 'Person'
   AND a.added_by = 'acronym_gen';

DELETE FROM aliases
 WHERE added_by = 'acronym_gen'
   AND surface ~ '[^A-Z0-9]';

-- General Atomics Aeronautical Systems is a subsidiary, not another name for
-- its parent. `profile/anchors.yaml` declared both `GA-ASI` and the full name
-- as aliases of `company:general-atomics`, which made every GA-ASI mention
-- evidence for the parent; a curator had already ruled the pair `related` on
-- the live corpus, so the declaration and the verdict contradicted each other.
-- The declaration is withdrawn in the same commit. Withdrawing it does not
-- delete anything -- anchor loading is upsert-only -- which is why this is
-- here. See docs/adr/.
DELETE FROM aliases
 WHERE entity_id = 'company:general-atomics'
   AND surface IN ('GA-ASI', 'General Atomics Aeronautical Systems');
-- Where a notice goes, who asked for it, and whether it arrived.
--
-- Until now the whole outward-facing surface was `VENONA_NOTIFY_URL`: one
-- process-environment string, one POST, fire and forget. That is the right
-- shape for one operator on one box and the wrong shape for two questions this
-- schema exists to answer -- "tell me, but not about that" and "did my brief
-- get sent" -- neither of which a single env var can hold.
--
-- WHY THE DATABASE AND NOT `config/settings.yaml`. A webhook URL is a
-- credential: anyone holding a Slack incoming URL can post to that channel.
-- `api/security.py` opens by explaining that the settings document is readable
-- AND writable over HTTP, so anything on the `Settings` model is both
-- attacker-writable and operator-visible, which is why the session secret and
-- the root token are environment-only. A destination has the same property and
-- gets the same answer, arriving at it from the other side: rows, never a
-- document. It also has to be per-user, and a per-user list was never going to
-- be a file.

-- Somewhere a notice can go.
CREATE TABLE notify_destinations (
  -- 'dest:' + uuid7.
  id          TEXT PRIMARY KEY,
  -- 'instance' or 'reader'. THE SAME TWO WORDS `NoticeType.audience` USES, and
  -- deliberately so: a row's owner and a notice's audience are the same
  -- distinction, and giving them separate vocabularies ('user' here, 'reader'
  -- there) is how a destination gets written by one and never found by the
  -- other. That happened once, and the symptom was a reader who had turned
  -- delivery on receiving nothing, with no error anywhere.
  --
  -- Not a CHECK, for the reason `users.role` is not one: the set is a product
  -- decision, and `notify/policy.py` is the gate.
  owner_kind  TEXT NOT NULL,
  -- NULL for an instance destination. CASCADE because a reader's delivery
  -- settings are theirs: deleting the account deletes where it was being sent.
  owner_id    TEXT REFERENCES users(id) ON DELETE CASCADE,
  -- 'webhook' or 'email'.
  kind        TEXT NOT NULL,
  -- What the owner calls it. Shown; never used to address anything.
  label       TEXT NOT NULL,
  -- The URL or the address. FOR A WEBHOOK THIS IS A CREDENTIAL and no route
  -- returns it -- responses carry a hint derived from it instead. The column is
  -- plaintext rather than hashed because unlike a token venona has to REPLAY
  -- it, and unlike a password nothing ever compares it. Encrypting it here
  -- would move the problem to where the key lives, which is the environment,
  -- which is where this started.
  target      TEXT NOT NULL,
  -- 'active' | 'disabled' | 'struck_out'. `disabled` is the owner's choice,
  -- `struck_out` is ours after `STRIKE_LIMIT` consecutive failures. Two states
  -- rather than one boolean because they are cleared by different people for
  -- different reasons, which is the distinction `source_health.auto_disabled`
  -- had to grow later.
  status      TEXT NOT NULL DEFAULT 'active',
  -- Consecutive failures, reset by any success. Consecutive and not a rate, for
  -- the reason `llm/breaker.py` gives: a destination that failed one send in
  -- ten is working.
  failures    INTEGER NOT NULL DEFAULT 0,
  last_error  TEXT,
  -- Attribution, not ownership: any admin may edit any instance destination.
  -- ON DELETE SET NULL, because removing the person who added it must not
  -- remove the alerting.
  created_by  TEXT REFERENCES users(id) ON DELETE SET NULL,
  created_at  TEXT NOT NULL,
  updated_at  TEXT NOT NULL
);

-- One reader cannot register the same address twice. `NULLS NOT DISTINCT` so
-- the rule also binds the instance's own rows, whose `owner_id` is NULL --
-- without it every instance destination is distinct from every other and the
-- index guards only the readers.
CREATE UNIQUE INDEX ux_notify_destinations_target
  ON notify_destinations (owner_id, kind, target) NULLS NOT DISTINCT;

-- The reader's list, and the fan-out's scan.
CREATE INDEX ix_notify_destinations_owner ON notify_destinations (owner_kind, owner_id);

-- What each destination has asked to be told about.
--
-- A row means yes. Absence means no, so a notice type added in a later release
-- reaches nobody until someone turns it on, which is the safe direction: the
-- alternative is a release that starts mailing readers about something they
-- never agreed to.
CREATE TABLE notify_subscriptions (
  destination_id TEXT NOT NULL REFERENCES notify_destinations(id) ON DELETE CASCADE,
  -- A key from `notify/types.py::NOTICE_TYPES`. No foreign key, for the reason
  -- `config_events.key` carries none: the registry is code, and retiring a
  -- notice type must not silently rewrite what people chose. An unknown key is
  -- ignored at fan-out and can be cleaned up deliberately.
  notice_type    TEXT NOT NULL,
  created_at     TEXT NOT NULL,
  PRIMARY KEY (destination_id, notice_type)
);

-- One durable attempt to put one notice on one destination.
--
-- The first durable outbound state in this repo. Everything outward-facing
-- before it was best-effort by design -- `notify.alert` swallows every
-- exception, `report_daily` swallows its own reads -- which is correct when the
-- only reader of an alert is the person who set up the alert. It stops being
-- correct once a reader can ask "did my brief get sent", because that question
-- has no answer unless something wrote one down.
CREATE TABLE notify_outbox (
  id             TEXT PRIMARY KEY,
  notice_type    TEXT NOT NULL,
  destination_id TEXT NOT NULL REFERENCES notify_destinations(id) ON DELETE CASCADE,
  -- The RENDERED body, as JSON, not the facts to render later. A notice
  -- describes something that was true when it was raised; re-rendering at send
  -- time against a corpus that has moved on would deliver a different sentence
  -- than the one that was queued.
  payload        TEXT NOT NULL,
  -- What makes a repeat the same notice. The brief's is the shared edition's
  -- `generated_at` (ADR 0185: the unit of currency is the edition, never the
  -- calendar day), so a second run on the same edition delivers nothing twice.
  -- NULL means every occurrence is its own notice.
  dedupe_key     TEXT,
  -- queued | sending | delivered | failed | blocked.
  --
  -- `blocked` is where an email row waits while no transport is configured. It
  -- is a state and not a failure on purpose: the subscription is honoured, the
  -- row is visible, and nothing is silently dropped -- which is what would have
  -- happened if a missing provider simply skipped the enqueue.
  status         TEXT NOT NULL DEFAULT 'queued',
  attempts       INTEGER NOT NULL DEFAULT 0,
  -- When the drainer may next pick this up. Set on every failure by the backoff.
  next_attempt_at TEXT,
  last_error     TEXT,
  created_at     TEXT NOT NULL,
  -- Moves on every claim, every failure and every delivery. This is the column
  -- the freshness stamp watches: a row going `queued` -> `delivered` changes no
  -- row count, so a `COUNT(*)` term alone would report the table as unchanged
  -- while every status on screen was stale.
  updated_at     TEXT NOT NULL,
  delivered_at   TEXT
);

-- One notice, per destination, once. A partial index because a NULL
-- `dedupe_key` means "this occurrence is its own", and NULLs would otherwise
-- collide under `NULLS NOT DISTINCT` or never collide without it -- neither of
-- which is the rule.
CREATE UNIQUE INDEX ux_notify_outbox_dedupe
  ON notify_outbox (destination_id, notice_type, dedupe_key)
  WHERE dedupe_key IS NOT NULL;

-- The drainer's claim: everything due, oldest first.
CREATE INDEX ix_notify_outbox_due ON notify_outbox (status, next_attempt_at);

-- The history panel, and the per-destination read behind a strike-out.
CREATE INDEX ix_notify_outbox_recent ON notify_outbox (destination_id, created_at DESC);
-- Which entities an ambiguous surface could mean.
--
-- `ambiguous_surfaces` said a surface has more than one plausible owner and
-- stopped there, so the resolver's only move was to decline: the mention was
-- dropped and BOTH readings lost it. Measured on the defense corpus, that is
-- `the Army` in 78 documents, `the Navy` in 41, `the Air Force` in 33 and the
-- bare ministries in 9 -- every one of them a mention of something the graph
-- already holds, thrown away because nothing recorded WHICH things it might be.
--
-- Declining is still the default and still the right answer when the text does
-- not say. What this column adds is the raw material for doing better when it
-- does: with the candidate ids in hand, a resolver can look for the one the
-- surrounding document actually names and fall back to declining otherwise.
-- The same argument `alias_index.senses()` makes for acronyms, one layer up.
--
-- JSON rather than a join table: it is written only by the anchors loader, read
-- whole, and never queried across. A list of ids in a column is the shape the
-- reader has.
ALTER TABLE ambiguous_surfaces ADD COLUMN IF NOT EXISTS readings TEXT;
-- What this reader has paid for, projected from the hub (ADR 0209).
--
-- A PROJECTION, not a record. Stripe lives in the hub, one customer per person
-- across every instance they read; these four columns are what an instance
-- needs to decide who sees what, and nothing here can be edited into a
-- subscription. `venona graph rebuild` is unaffected -- this is not corpus.
--
-- The first two decide ACCESS. The second two decide what the reader is TOLD,
-- which is a different question: a first card decline leaves somebody fully
-- entitled and changes only the banner they see.

-- none      -- never subscribed. The trial has not started, because starting it
--              means entering a card (ADR 0212). Not the same as lapsed: there
--              is nothing to freeze, so `entitled_through` stays NULL and the
--              app offers to start rather than showing an empty archive.
-- trialing  -- three days, card on file, converts by itself.
-- active    -- paying.
-- past_due  -- Stripe is retrying the card. STILL ENTITLED: cutting somebody
--              off at the first decline is how a recoverable lapse becomes a
--              cancellation.
-- lapsed    -- it ended. The archive freezes at `entitled_through` (ADR 0211).
-- comped    -- does not pay and reads everything: the operator, a design
--              partner, a journalist.
ALTER TABLE users ADD COLUMN entitlement_status TEXT NOT NULL DEFAULT 'none'
    CHECK (entitlement_status IN
        ('none', 'trialing', 'active', 'past_due', 'lapsed', 'comped'));

-- The archive ceiling. A lapsed reader sees briefs dated on or before this,
-- forever; resubscribing lifts it and the whole archive returns. One
-- comparison, and `ix_briefs_date` already indexes the column it compares.
ALTER TABLE users ADD COLUMN entitled_through TEXT;

-- The notice KIND, never a sentence. The words live in `web/src/lib/billing.ts`
-- and nowhere else, for the reason CLAUDE.md gives about run outcomes: four
-- surfaces once decided the wording of a run independently and one of them was
-- wrong about the world. This one would be wrong about somebody's money.
ALTER TABLE users ADD COLUMN billing_notice TEXT;

-- The date that notice is ABOUT: the retry date, the period end. Dated copy
-- converts and vague copy does not, and the reader's own timezone is applied at
-- the render site from `user_preferences.timezone` -- a billing date shown a day
-- out is a support email, or a dispute.
ALTER TABLE users ADD COLUMN billing_notice_at TEXT;

-- EVERY EXISTING READER IS COMPED, not 'none'.
--
-- The hub and this instance deploy independently from one commit (the deployment config's
-- build filters), so there is a window where the gate is live here and no hub
-- has pushed anything. With the column defaulting to 'none' that window locks
-- out the entire readership. A reader who is already here does not become a
-- stranger because a column appeared, and the hub's first push corrects anyone
-- who should be paying.
--
-- The DEFAULT stays 'none' so a genuinely new row starts unentitled; this
-- backfill is only about the people who predate the column.
UPDATE users SET entitlement_status = 'comped';

-- The warm pass reads this every day (`users_repo.recently_seen`), so the
-- predicate it filters on belongs in an index rather than a sequential scan
-- once there are more than a handful of readers.
CREATE INDEX ix_users_entitlement ON users (entitlement_status, entitled_through);
-- Switches the hub throws on this instance's behalf.
--
-- ONE ROW, and deliberately NOT `config/settings.yaml`. That file is editable
-- over HTTP by anyone holding an admin session (`services/config_service.py`),
-- and contract C12's whole argument about the paywall is that a gate an admin
-- session can switch off is not a gate. This table has no editor: the only
-- writer is `POST /api/mgmt/settings`, which is reachable with the hub's
-- `vmgmt_` credential and nothing else.
--
-- A PROJECTION, like the entitlement columns beside it (migration 0023). The
-- hub decides; this is where the decision is kept so the instance can read it
-- without asking anybody, including on a morning when the hub is down.
CREATE TABLE hub_flags (
    id               SMALLINT PRIMARY KEY CHECK (id = 1),
    -- Whether the paywall bites. FALSE is the safe default and the one that
    -- ships: an instance nobody has told anything is an instance that charges
    -- nobody, which is right for a local box, a self-hosted one, and for the
    -- window between this deploying and the hub first pushing.
    billing_enforced BOOLEAN NOT NULL DEFAULT false,
    updated_at       TEXT
);

-- In the same transaction as the table, so no read is ever a special case.
INSERT INTO hub_flags (id) VALUES (1);
-- Which model service did this, and what paid for it.
--
-- NOTE FOR ANYONE EDITING THIS FILE: no comment here may contain a colon
-- followed by word characters. SQLAlchemy's `text()` reads a leading-colon
-- token as a bind parameter INSIDE comments as well as in statements, and
-- the pattern includes digits -- so a file reference carrying a line number
-- detonates the migration, and so does spelling the hazard out literally.
-- The baseline carries the same warning for the same reason.

-- A connection record, not only a secret store.
--
-- `secret` is NULLABLE because the two kinds of credential live in different
-- places. A subscription is written by the CLI into an instance-scoped home on
-- disk and venona never holds a copy; the row still exists so the account it
-- signed in as, and when that was last confirmed, survive a restart. The
-- in-process health cache cannot answer either question after one.
--
-- PLAINTEXT where a secret is present, and deliberately. Unlike a password
-- nothing ever compares it, and unlike a hash venona has to REPLAY it, so
-- encrypting here would move the problem to where the key lives, which is the
-- environment, which is where this started. The notifications table records
-- the same decision about the same class of value.
CREATE TABLE IF NOT EXISTS harness_credentials (
    id            TEXT PRIMARY KEY,
    harness       TEXT NOT NULL,
    billing_mode  TEXT NOT NULL,
    -- NO ROUTE RETURNS THIS. Responses carry a hint derived at read time.
    secret        TEXT,
    -- What the service says it is signed in as. Admin surfaces only; the
    -- health route is reader-reachable and must not carry it.
    account_label TEXT,
    verified_at   TEXT,
    -- Attribution, never ownership. ON DELETE SET NULL rather than CASCADE:
    -- deleting the administrator who connected an account must not disconnect
    -- the model for the whole instance.
    updated_by    TEXT REFERENCES users(id) ON DELETE SET NULL,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);

-- One credential per service per way of paying for it, so connecting an API
-- key does not silently discard a subscription an operator may switch back to.
CREATE UNIQUE INDEX IF NOT EXISTS ux_harness_credentials
    ON harness_credentials (harness, billing_mode);

-- Provenance on the ledger and on the run.
--
-- NULLABLE, NO DEFAULT, NEVER BACKFILLED. Every row written before this
-- migration was Claude Code on a subscription, and stamping that is provably
-- true -- but a DEFAULT would also silently stamp every FUTURE row that missed
-- the write path, which is the failure the outcome column was introduced to
-- remove. A row with no service named renders without one.
ALTER TABLE llm_calls ADD COLUMN IF NOT EXISTS harness TEXT;
ALTER TABLE llm_calls ADD COLUMN IF NOT EXISTS billing_mode TEXT;
-- How `cost_usd` was arrived at. One column now holds four kinds of number --
-- a subscription's shadow price at list rates, what a metered provider
-- actually billed, tokens multiplied by a price table this repo maintains, and
-- a plain zero -- and summing across them means nothing. An aggregate that
-- cannot tell them apart is how a chart claims a bill nobody received.
ALTER TABLE llm_calls ADD COLUMN IF NOT EXISTS cost_basis TEXT;

ALTER TABLE runs ADD COLUMN IF NOT EXISTS harness TEXT;
ALTER TABLE runs ADD COLUMN IF NOT EXISTS billing_mode TEXT;

-- Stamped when the executor is CHOSEN, not when the row is written: a queued
-- light-lane run is admitted minutes later, and the service could change in
-- between. A run cancelled before admission keeps NULL, correctly.
CREATE INDEX IF NOT EXISTS ix_runs_harness ON runs (harness);

-- Windows belong to the service that reported them.
--
-- Without this a switch leaves both services' windows in the latest-per-window
-- read forever, two of them frozen at the moment the old service stopped being
-- used, and two rows claiming to be representative at once. NULLS NOT DISTINCT
-- keeps the re-observation guarantee for every row written before the column
-- existed; without it each of those becomes distinct from itself.
ALTER TABLE quota_samples ADD COLUMN IF NOT EXISTS harness TEXT;
DROP INDEX IF EXISTS ux_quota_samples_reading;
CREATE UNIQUE INDEX IF NOT EXISTS ux_quota_samples_reading
    ON quota_samples (harness, window_name, resets_at, utilization) NULLS NOT DISTINCT;
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
