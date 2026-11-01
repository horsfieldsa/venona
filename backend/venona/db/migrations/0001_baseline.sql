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
