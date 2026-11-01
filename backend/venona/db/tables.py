"""SQLAlchemy Core table definitions. The QUERY layer, not the DDL authority.

WHAT OWNS THE SCHEMA: `db/migrations/*.sql`, still, and deliberately. Those files
carry the reasoning -- why `mcp_tokens.token_hash` is a bare SHA-256 and not a
KDF, why `aliases` is keyed on the surface rather than the normalised form, why
`brief_items.ord` exists at all -- and a generated `CREATE TABLE` cannot carry a
twenty-line argument. Alembic RUNS them and versions them; this file describes
their result so queries can be built against something typed.

Two sources of truth for one schema is normally a mistake, so it is held together
rather than trusted: `tests/test_tables_metadata.py` reflects the migrated
database and asserts it matches this file, table for table and column for column.
Drift in either direction fails there rather than at a call site.

WHAT THIS IS FOR: `db/repositories/`. A repository builds a statement from these
objects instead of interpolating a string, which is what makes the read-scope seam
of the tenancy work enforceable rather than remembered -- a query that must be
scoped cannot be scoped by a `str.format`.

GENERATED, then reviewed. Emitted by reflecting the migrated database, because 29
tables and 300-odd columns transcribed by hand is a transcription error waiting to
be found by a reader at 2am.
"""

from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    Double,
    ForeignKey,
    Integer,
    LargeBinary,
    MetaData,
    SmallInteger,
    Table,
    Text,
)

#: `schema_migrations` is deliberately absent: it is the LEDGER, written by the
#: migration runner itself, and nothing in the app queries it.
metadata = MetaData()


access_requests = Table(
    "access_requests",
    metadata,
    Column("id", Text, primary_key=True),
    Column("email", Text, nullable=False),
    Column("coverage", Text),
    Column("role", Text, nullable=False),
    Column("status", Text, nullable=False),
    Column("created_at", Text, nullable=False),
    Column("decided_at", Text),
    Column("note", Text),
    Column("decided_by", Text, ForeignKey("users.id")),
    Column("user_id", Text, ForeignKey("users.id")),
)

acronym_definitions = Table(
    "acronym_definitions",
    metadata,
    Column("id", Text, primary_key=True),
    Column("surface", Text, nullable=False),
    Column("normalized", Text, nullable=False),
    Column("expansion", Text),
    Column("entity_id", Text),
    Column("method", Text, nullable=False),
    Column("confidence", Double, nullable=False),
    Column("grounding_article_id", Text),
    Column("grounding_url", Text),
    Column("grounding_quote", Text),
    Column("grounding_span", Text),
    Column("status", Text, nullable=False),
    Column("sighting_count", Integer, nullable=False),
    Column("article_count", Integer, nullable=False),
    Column("first_seen", Text, nullable=False),
    Column("last_seen", Text, nullable=False),
    Column("decided_at", Text),
    Column("last_attempt_at", Text),
    Column("last_attempt_run_id", Text),
    Column("attempt_count", Integer, nullable=False),
    Column("corroboration_count", Integer, nullable=False),
    Column("corroborating_sources", Text),
    Column("decided_by", Text, ForeignKey("users.id")),
)

alias_conflicts = Table(
    "alias_conflicts",
    metadata,
    Column("id", Text, primary_key=True),
    Column("surface", Text, nullable=False),
    Column("normalized", Text, nullable=False),
    Column("existing_entity_id", Text, nullable=False),
    Column("proposed_entity_id", Text, nullable=False),
    Column("entity_type", Text, nullable=False),
    Column("article_id", Text),
    Column("resolved", Integer, nullable=False),
    Column("created_at", Text, nullable=False),
    Column("kind", Text),
    Column("resolution", Text),
    Column("resolved_at", Text),
    Column("suggestion", Text),
    Column("suggestion_note", Text),
    Column("suggestion_child", Text),
    Column("suggested_at", Text),
    Column("suggestion_predicate", Text),
    Column("suggestion_confidence", Text),
    Column("resolved_by", Text, ForeignKey("users.id")),
)

aliases = Table(
    "aliases",
    metadata,
    Column("id", Text, primary_key=True),
    Column("entity_id", Text, ForeignKey("entities.id"), nullable=False),
    Column("surface", Text, nullable=False),
    Column("normalized", Text, nullable=False),
    Column("strictness", Integer, nullable=False),
    Column("is_acronym", Integer, nullable=False),
    Column("added_by", Text, nullable=False),
    Column("evidence_article_id", Text),
    Column("created_at", Text, nullable=False),
)

ambiguous_surfaces = Table(
    "ambiguous_surfaces",
    metadata,
    Column("normalized", Text, primary_key=True),
    Column("reason", Text),
    # JSON: the entity ids this surface could mean, or NULL for a surface that
    # declines without offering a way to choose. See `resolve._pick_reading`.
    Column("readings", Text),
    Column("created_at", Text, nullable=False),
)

assertions = Table(
    "assertions",
    metadata,
    Column("id", Text, primary_key=True),
    Column("run_id", Text, nullable=False),
    Column("article_id", Text, nullable=False),
    Column("cluster_id", Text, nullable=False),
    Column("kind", Text, nullable=False),
    Column("surface", Text),
    Column("entity_type", Text),
    Column("entity_id_raw", Text),
    Column("predicate", Text),
    Column("subject_id_raw", Text),
    Column("object_id_raw", Text),
    Column("evidence_quote", Text, nullable=False),
    # Where in this article the DESCRIPTION lives, as distinct from where the
    # name appeared. Null means nobody has looked; `''` means the article was
    # read for this name and describes nothing. ADR 0244.
    Column("definition_quote", Text),
    Column("evidence_span", Text),
    Column("salience", Double),
    Column("confidence", Double, nullable=False),
    Column("method", Text, nullable=False),
    Column("model", Text),
    Column("created_at", Text, nullable=False),
    Column("retracted_at", Text),
    Column("predicate_kind", Text),
)

brief_blocks = Table(
    "brief_blocks",
    metadata,
    Column("id", Text, primary_key=True),
    Column("brief_id", Text, ForeignKey("briefs.id"), nullable=False),
    Column("section", Integer, nullable=False),
    Column("heading", Text),
    Column("ordinal", Integer, nullable=False),
    Column("type", Text, nullable=False),
    Column("depth", Integer, nullable=False),
    Column("text", Text, nullable=False),
    Column("spans", Text, nullable=False),
    Column("marks", Text, nullable=False),
    Column("item_id", Text),
    Column("role", Text),
)

brief_clusters = Table(
    "brief_clusters",
    metadata,
    Column("brief_id", Text, ForeignKey("briefs.id"), primary_key=True),
    Column("cluster_id", Text, primary_key=True),
    Column("rank", Integer, nullable=False),
    Column("created_at", Text, nullable=False),
)

brief_items = Table(
    "brief_items",
    metadata,
    Column("id", Text, primary_key=True),
    Column("brief_id", Text, nullable=False),
    Column("section", Text, nullable=False),
    Column("rank", Integer, nullable=False),
    Column("cluster_id", Text),
    Column("headline", Text, nullable=False),
    Column("state", Text, nullable=False),
    Column("state_note", Text),
    Column("top_rank", Integer),
    Column("top_line", Text),
    Column("ord", Integer, nullable=False),
    Column("created_at", Text, nullable=False),
)

briefs = Table(
    "briefs",
    metadata,
    Column("id", Text, primary_key=True),
    Column("date", Text, nullable=False),
    Column("run_id", Text),
    Column("title", Text, nullable=False),
    Column("dek", Text),
    Column("generated_at", Text, nullable=False),
    Column("model", Text),
    Column("unresolved_mentions", Text),
    Column("source_ids", Text),
    Column("created_at", Text, nullable=False),
)

clusters = Table(
    "clusters",
    metadata,
    Column("id", Text, primary_key=True),
    Column("canonical_doc_id", Text),
    Column("title", Text),
    Column("published_at", Text),
    Column("relevance_overall", Integer),
    Column("tier", Text),
    Column("relevance_note", Text),
    Column("so_what", Text),
    Column("axes", Text),
    Column("profile_hooks", Text),
    Column("analyzed_at", Text),
    Column("acronyms_scanned_at", Text),
    Column("first_seen", Text, nullable=False),
    Column("last_seen", Text, nullable=False),
    Column("key_facts", Text),
    Column("flags", Text),
)

documents = Table(
    "documents",
    metadata,
    Column("id", Text, primary_key=True),
    Column("run_id", Text),
    Column("source_id", Text),
    Column("url", Text, nullable=False),
    Column("url_key", Text, nullable=False),
    Column("title", Text, nullable=False),
    Column("publisher", Text),
    Column("author", Text),
    Column("text_nfc", Text, nullable=False),
    Column("text_chars", Integer, nullable=False),
    Column("excerpt", Text),
    Column("lang", Text),
    Column("published_at", Text),
    Column("date_confidence", Text, nullable=False),
    Column("fetched_at", Text, nullable=False),
    Column("content_hash", Text, nullable=False),
    Column("simhash", Integer),
    Column("site_domain", Text),
    Column("cluster_id", Text, nullable=False),
    Column("duplicate_of", Text),
    Column("paywalled", Integer, nullable=False),
    Column("created_at", Text, nullable=False),
)

entities = Table(
    "entities",
    metadata,
    Column("id", Text, primary_key=True),
    Column("type", Text, nullable=False),
    Column("name", Text, nullable=False),
    Column("canonical_key", Text, nullable=False),
    Column("expansion", Text),
    Column("summary", Text),
    Column("org_type", Text),
    Column("status", Text, nullable=False),
    Column("relevance", Double, nullable=False),
    Column("mention_count", Integer, nullable=False),
    Column("source_count", Integer, nullable=False),
    Column("props", Text),
    Column("first_seen", Text, nullable=False),
    Column("last_seen", Text, nullable=False),
    Column("created_at", Text, nullable=False),
)

entity_definitions = Table(
    "entity_definitions",
    metadata,
    Column("entity_id", Text, primary_key=True),
    Column("definition", Text),
    Column("method", Text, nullable=False),
    Column("confidence", Double, nullable=False),
    Column("grounding_article_id", Text),
    Column("grounding_url", Text),
    Column("grounding_quote", Text),
    Column("grounding_span", Text),
    Column("source_article_ids", Text),
    Column("status", Text, nullable=False),
    Column("decided_at", Text),
    Column("last_attempt_at", Text),
    Column("last_attempt_run_id", Text),
    Column("attempt_count", Integer, nullable=False),
    Column("first_seen", Text, nullable=False),
    Column("last_seen", Text, nullable=False),
    Column("decided_by", Text, ForeignKey("users.id")),
)

entity_mappings = Table(
    "entity_mappings",
    metadata,
    Column("from_id", Text, primary_key=True),
    Column("to_id", Text, nullable=False),
    Column("merge_id", Text, primary_key=True),
    Column("active", Integer, nullable=False),
    Column("reason", Text),
    Column("created_at", Text, nullable=False),
    Column("merged_by", Text, ForeignKey("users.id")),
)

llm_calls = Table(
    "llm_calls",
    metadata,
    Column("id", Text, primary_key=True),
    Column("run_id", Text),
    Column("task", Text, nullable=False),
    Column("cache_key", Text),
    Column("model", Text),
    Column("models", Text),
    Column("effort", Text),
    Column("tools", Text),
    Column("input_tokens", Integer, nullable=False),
    Column("output_tokens", Integer, nullable=False),
    Column("cache_read_input_tokens", Integer, nullable=False),
    Column("cache_creation_input_tokens", Integer, nullable=False),
    Column("web_search_requests", Integer, nullable=False),
    Column("web_fetch_requests", Integer, nullable=False),
    Column("cost_usd", Double, nullable=False),
    Column("duration_ms", Integer, nullable=False),
    Column("status", Text, nullable=False),
    Column("error", Text),
    Column("stage", Text),
    Column("subject", Text),
    Column("created_at", Text, nullable=False),
    # Which service produced this call and what paid for it. Nullable with no
    # default: a row written before there was a choice records neither, and
    # naming today's service on it would invent a provenance nobody kept.
    Column("harness", Text),
    Column("billing_mode", Text),
    # What `cost_usd` means on this row -- list_price, measured, computed, none.
    Column("cost_basis", Text),
)

quota_samples = Table(
    "quota_samples",
    metadata,
    Column("id", Text, primary_key=True),
    Column("observed_at", Text, nullable=False),
    Column("window_name", Text, nullable=False),
    Column("utilization", Double, nullable=False),
    Column("resets_at", Text, nullable=False),
    Column("status", Text, nullable=False),
    Column("representative", Boolean, nullable=False),
    Column("run_id", Text),
    # A window belongs to the service that reported it; two services' windows
    # in one latest-per-window read is half a chart frozen in the past.
    Column("harness", Text),
)

mcp_tokens = Table(
    "mcp_tokens",
    metadata,
    Column("id", Text, primary_key=True),
    Column("label", Text, nullable=False),
    Column("token_hash", Text, nullable=False),
    Column("prefix", Text, nullable=False),
    Column("created_at", Text, nullable=False),
    Column("last_used_at", Text),
    Column("revoked_at", Text),
    Column("user_id", Text, ForeignKey("users.id")),
    Column("scope", Text, nullable=False),
    Column("expires_at", Text),
    Column("client_id", Text),
)

oauth_clients = Table(
    "oauth_clients",
    metadata,
    Column("client_id", Text, primary_key=True),
    Column("client_name", Text, nullable=False),
    Column("redirect_uris", Text, nullable=False),
    Column("created_at", Text, nullable=False),
    Column("last_used_at", Text),
)

oauth_codes = Table(
    "oauth_codes",
    metadata,
    Column("code_hash", Text, primary_key=True),
    Column("client_id", Text, ForeignKey("oauth_clients.client_id"), nullable=False),
    Column("user_id", Text, ForeignKey("users.id"), nullable=False),
    Column("redirect_uri", Text, nullable=False),
    Column("code_challenge", Text, nullable=False),
    Column("scope", Text, nullable=False),
    Column("expires_at", Text, nullable=False),
    Column("created_at", Text, nullable=False),
    Column("redeemed_at", Text),
)

run_events = Table(
    "run_events",
    metadata,
    Column("run_id", Text, primary_key=True),
    Column("seq", Integer, primary_key=True),
    Column("ts", Text, nullable=False),
    Column("type", Text, nullable=False),
    Column("stage", Text),
    Column("data", Text, nullable=False),
)

run_metrics = Table(
    "run_metrics",
    metadata,
    Column("run_id", Text, primary_key=True),
    Column("stage", Text, primary_key=True),
    Column("key", Text, primary_key=True),
    Column("value", Double, nullable=False),
    Column("unit", Text),
    Column("at", Text, nullable=False),
)

run_source_stats = Table(
    "run_source_stats",
    metadata,
    Column("run_id", Text, primary_key=True),
    Column("source_id", Text, primary_key=True),
    Column("offered", Integer, nullable=False),
    Column("capped", Integer, nullable=False),
    Column("kept", Integer, nullable=False),
    Column("duplicate", Integer, nullable=False),
    Column("skipped", Integer, nullable=False),
    Column("fresh", Integer, nullable=False),
    Column("stale", Integer, nullable=False),
    Column("undated", Integer, nullable=False),
    Column("age_p50_h", Double),
    Column("status", Text),
    Column("error", Text),
    Column("duration_ms", Integer, nullable=False),
)

run_rejected_shapes = Table(
    "run_rejected_shapes",
    metadata,
    Column("run_id", Text, primary_key=True),
    Column("predicate", Text, primary_key=True),
    Column("subject_type", Text, primary_key=True),
    Column("object_type", Text, primary_key=True),
    Column("n", Integer, nullable=False),
    Column("example_subject", Text, nullable=False),
    Column("example_object", Text, nullable=False),
    Column("example_article_id", Text),
    Column("example_evidence", Text),
    Column("at", Text, nullable=False),
)

run_stages = Table(
    "run_stages",
    metadata,
    Column("run_id", Text, primary_key=True),
    Column("stage", Text, primary_key=True),
    Column("status", Text, nullable=False),
    Column("counts", Text),
    Column("error", Text),
    Column("started_at", Text, nullable=False),
    Column("finished_at", Text),
)

runs = Table(
    "runs",
    metadata,
    Column("id", Text, primary_key=True),
    Column("kind", Text, nullable=False),
    Column("status", Text, nullable=False),
    # Whether the run FOUND anything, as opposed to whether it ran. Nullable:
    # a failed or cancelled run never reached an answer. See ADR 0183.
    Column("outcome", Text),
    Column("stage", Text),
    Column("params", Text),
    Column("counters", Text),
    Column("warnings", Text),
    Column("error", Text),
    Column("profile_hash", Text),
    Column("domain_hash", Text),
    Column("brief_id", Text),
    Column("cost_usd", Double, nullable=False),
    Column("started_at", Text, nullable=False),
    Column("finished_at", Text),
    Column("owner_pid", Integer),
    Column("hidden_at", Text),
    Column("started_by", Text, ForeignKey("users.id")),
    # Stamped when the executor is chosen, not when the row is written: a
    # queued run is admitted later and could land on a different service.
    Column("harness", Text),
    Column("billing_mode", Text),
)

sightings = Table(
    "sightings",
    metadata,
    Column("id", Text, primary_key=True),
    Column("cluster_id", Text, nullable=False),
    Column("document_id", Text, nullable=False),
    Column("source_id", Text),
    Column("run_id", Text, nullable=False),
    Column("seen_at", Text, nullable=False),
)

source_health = Table(
    "source_health",
    metadata,
    Column("source_id", Text, ForeignKey("sources.id"), primary_key=True),
    Column("last_success_at", Text),
    Column("last_attempt_at", Text),
    Column("last_item_at", Text),
    Column("consecutive_failures", Integer, nullable=False),
    Column("last_error", Text),
    Column("etag", Text),
    Column("last_modified", Text),
    Column("auto_disabled", Integer, nullable=False),
)

sources = Table(
    "sources",
    metadata,
    Column("id", Text, primary_key=True),
    Column("name", Text, nullable=False),
    Column("kind", Text, nullable=False),
    Column("url", Text),
    Column("publisher", Text),
    Column("enabled", Integer, nullable=False),
    Column("tags", Text),
    Column("created_at", Text, nullable=False),
)

users = Table(
    "users",
    metadata,
    Column("id", Text, primary_key=True),
    Column("email", Text, nullable=False),
    Column("role", Text, nullable=False),
    Column("status", Text, nullable=False),
    Column("display_name", Text),
    Column("picture_url", Text),
    Column("oidc_issuer", Text),
    Column("oidc_subject", Text),
    Column("session_epoch", Text),
    Column("created_at", Text, nullable=False),
    Column("last_seen_at", Text),
    # The hub's projection (migration 0023). The first two decide access, the
    # second two decide what the reader is told -- a first card decline leaves
    # somebody fully entitled and changes only the banner.
    Column("entitlement_status", Text, nullable=False),
    Column("entitled_through", Text),
    Column("billing_notice", Text),
    Column("billing_notice_at", Text),
)

user_events = Table(
    "user_events",
    metadata,
    Column("id", Text, primary_key=True),
    Column("user_id", Text, ForeignKey("users.id"), nullable=False),
    Column("actor_id", Text, ForeignKey("users.id")),
    Column("action", Text, nullable=False),
    Column("detail", Text),
    Column("created_at", Text, nullable=False),
)

#: Who changed what the model is told (migration 0014, ADR 0166). `key` carries
#: no foreign key on purpose: dropping a document from `EDITABLE` must not delete
#: the record of who last edited it.
config_events = Table(
    "config_events",
    metadata,
    Column("id", Text, primary_key=True),
    Column("key", Text, nullable=False),
    Column("actor_id", Text, ForeignKey("users.id")),
    Column("version", Text, nullable=False),
    Column("prev", Text),
    Column("bytes", Integer, nullable=False),
    Column("created_at", Text, nullable=False),
)

learning_cards = Table(
    "learning_cards",
    metadata,
    Column("user_id", Text, ForeignKey("users.id"), primary_key=True),
    Column("card_key", Text, primary_key=True),
    Column("state", Text, nullable=False),
    Column("stability", Double, nullable=False),
    Column("difficulty", Double, nullable=False),
    Column("due", Text, nullable=False),
    Column("last_review", Text, nullable=False),
    Column("reps", Integer, nullable=False),
    Column("lapses", Integer, nullable=False),
    Column("created_at", Text, nullable=False),
    Column("updated_at", Text, nullable=False),
)

learning_reviews = Table(
    "learning_reviews",
    metadata,
    Column("id", Text, primary_key=True),
    Column("user_id", Text, ForeignKey("users.id"), nullable=False),
    Column("card_key", Text, nullable=False),
    Column("rating", Integer, nullable=False),
    Column("review_kind", Text, nullable=False),
    Column("format", Text, nullable=False),
    Column("elapsed_days", Double, nullable=False),
    Column("due_at", Text),
    Column("created_at", Text, nullable=False),
)

llm_questions = Table(
    "llm_questions",
    metadata,
    Column("id", Text, primary_key=True),
    Column("run_id", Text, nullable=False),
    Column("kind", Text, nullable=False),
    Column("entity_id", Text),
    Column("prompt", Text, nullable=False),
    Column("choices", Text),
    Column("answer", Text, nullable=False),
    Column("grounding_article_id", Text, nullable=False),
    Column("grounding_quote", Text, nullable=False),
    Column("grounding_span", Text),
    Column("status", Text, nullable=False),
    Column("decided_by", Text),
    Column("decided_at", Text),
    Column("created_at", Text, nullable=False),
)

learning_settings = Table(
    "learning_settings",
    metadata,
    Column("user_id", Text, ForeignKey("users.id"), primary_key=True),
    Column("new_per_day", Integer, nullable=False),
    Column("daily_goal", Integer, nullable=False),
    Column("retention_target", Double, nullable=False),
    Column("updated_at", Text, nullable=False),
)

relevance_settings = Table(
    "relevance_settings",
    metadata,
    Column("user_id", Text, ForeignKey("users.id"), primary_key=True),
    Column("emphasis", Text, nullable=False),
    Column("updated_at", Text, nullable=False),
)

user_preferences = Table(
    "user_preferences",
    metadata,
    Column("user_id", Text, ForeignKey("users.id"), primary_key=True),
    Column("timezone", Text),
    Column("updated_at", Text, nullable=False),
)

entity_follows = Table(
    "entity_follows",
    metadata,
    Column("user_id", Text, ForeignKey("users.id"), primary_key=True),
    Column("entity_id", Text, primary_key=True),
    Column("created_at", Text, nullable=False),
)

personal_briefs = Table(
    "personal_briefs",
    metadata,
    Column("id", Text, primary_key=True),
    Column("user_id", Text, ForeignKey("users.id"), nullable=False),
    Column("date", Text, nullable=False),
    Column("run_id", Text),
    Column("inputs_hash", Text, nullable=False),
    Column("title", Text, nullable=False),
    Column("dek", Text),
    Column("generated_at", Text, nullable=False),
    Column("source_ids", Text),
    Column("created_at", Text, nullable=False),
)

personal_brief_blocks = Table(
    "personal_brief_blocks",
    metadata,
    Column("id", Text, primary_key=True),
    Column("brief_id", Text, ForeignKey("personal_briefs.id"), nullable=False),
    Column("section", Integer, nullable=False),
    Column("heading", Text),
    Column("ordinal", Integer, nullable=False),
    Column("type", Text, nullable=False),
    Column("depth", Integer, nullable=False),
    Column("text", Text, nullable=False),
    Column("spans", Text, nullable=False),
    Column("marks", Text, nullable=False),
    Column("item_id", Text),
    Column("role", Text),
)

personal_brief_items = Table(
    "personal_brief_items",
    metadata,
    Column("id", Text, primary_key=True),
    Column("brief_id", Text, ForeignKey("personal_briefs.id"), nullable=False),
    Column("cluster_id", Text),
    Column("section", Text),
    Column("headline", Text, nullable=False),
    Column("state", Text, nullable=False),
    Column("state_note", Text),
    Column("top_rank", Integer),
    Column("top_line", Text),
    Column("ord", Integer, nullable=False),
    Column("created_at", Text, nullable=False),
)

#: Corpus-wide sighting totals for every detected surface. Separate from
#: `acronym_definitions`, which holds only surfaces that have a definition or a
#: verdict: most detected surfaces have neither, and a surface with no row
#: there still has to be counted to become a lookup candidate. Migration 0013.
surface_sightings = Table(
    "surface_sightings",
    metadata,
    Column("normalized", Text, primary_key=True),
    Column("surface", Text, nullable=False),
    Column("sighting_count", Integer, nullable=False),
    Column("article_count", Integer, nullable=False),
    Column("first_seen", Text, nullable=False),
    Column("last_seen", Text, nullable=False),
)

#: How many documents use each capitalised token as an ordinary word. Document
#: frequency rather than membership, because one document using a token that
#: way is a coincidence and recurrence is what marks a shouted English word.
ordinary_word_df = Table(
    "ordinary_word_df",
    metadata,
    Column("word", Text, primary_key=True),
    Column("doc_count", Integer, nullable=False),
)


#: Somewhere a notice can go: a webhook URL or an email address, owned by the
#: instance or by one reader. `target` is a credential for a webhook and no
#: route returns it; see `0021_notifications.sql` for why it lives here rather
#: than in the settings document. Migration 0021.
notify_destinations = Table(
    "notify_destinations",
    metadata,
    Column("id", Text, primary_key=True),
    Column("owner_kind", Text, nullable=False),
    Column("owner_id", Text, ForeignKey("users.id")),
    Column("kind", Text, nullable=False),
    Column("label", Text, nullable=False),
    Column("target", Text, nullable=False),
    Column("status", Text, nullable=False),
    Column("failures", Integer, nullable=False),
    Column("last_error", Text),
    Column("created_by", Text, ForeignKey("users.id")),
    Column("created_at", Text, nullable=False),
    Column("updated_at", Text, nullable=False),
)

#: What each destination has asked to be told about. A row means yes; absence
#: means no, so a notice type added in a later release reaches nobody until
#: someone turns it on. `notice_type` carries no foreign key for the reason
#: `config_events.key` carries none: the registry is code. Migration 0021.
notify_subscriptions = Table(
    "notify_subscriptions",
    metadata,
    Column(
        "destination_id",
        Text,
        ForeignKey("notify_destinations.id"),
        primary_key=True,
    ),
    Column("notice_type", Text, primary_key=True),
    Column("created_at", Text, nullable=False),
)

#: One durable attempt to put one notice on one destination, and the first
#: durable outbound state in this repo. `updated_at` is what the freshness
#: stamp watches: a row going `queued` to `delivered` changes no row count.
#: Migration 0021.
notify_outbox = Table(
    "notify_outbox",
    metadata,
    Column("id", Text, primary_key=True),
    Column("notice_type", Text, nullable=False),
    Column("destination_id", Text, ForeignKey("notify_destinations.id"), nullable=False),
    Column("payload", Text, nullable=False),
    Column("dedupe_key", Text),
    Column("status", Text, nullable=False),
    Column("attempts", Integer, nullable=False),
    Column("next_attempt_at", Text),
    Column("last_error", Text),
    Column("created_at", Text, nullable=False),
    Column("updated_at", Text, nullable=False),
    Column("delivered_at", Text),
)


#: The hub's switches, projected. One row, no editor, one writer
#: (`POST /api/mgmt/settings`) -- see migration 0024 for why this is not in
#: `config/settings.yaml`, which an admin session can edit over HTTP.
hub_flags = Table(
    "hub_flags",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("billing_enforced", Boolean, nullable=False),
    Column("updated_at", Text),
)


harness_credentials = Table(
    "harness_credentials",
    metadata,
    Column("id", Text, primary_key=True),
    Column("harness", Text, nullable=False),
    Column("billing_mode", Text, nullable=False),
    # NO ROUTE RETURNS THIS. Nullable because a subscription credential is
    # written by the CLI into its own home and venona never holds a copy; the
    # row still records the account and when it was last confirmed.
    Column("secret", Text),
    Column("account_label", Text),
    Column("verified_at", Text),
    Column("updated_by", Text, ForeignKey("users.id", ondelete="SET NULL")),
    Column("created_at", Text, nullable=False),
    Column("updated_at", Text, nullable=False),
)


data_exports = Table(
    "data_exports",
    metadata,
    Column("id", Text, primary_key=True),
    # NO ForeignKey, matching the migration. `runs` is purged and these rows are
    # not, so a reference would either take the export away with the run or
    # refuse the delete. Provenance only; the listing never joins through it.
    Column("run_id", Text),
    Column("filename", Text, nullable=False),
    Column("path", Text, nullable=False),
    Column("bytes", BigInteger, nullable=False),
    Column("sha256", Text, nullable=False),
    Column("counts", Text, nullable=False),
    # ready, pruned, or missing. The last two are different facts and the
    # download route says which.
    Column("status", Text, nullable=False),
    Column("created_at", Text, nullable=False),
    Column("created_by", Text, ForeignKey("users.id", ondelete="SET NULL")),
    Column("pruned_at", Text),
    # The archive itself (ADR 0234). Regenerating cannot work: a second download
    # must return the bytes somebody has, and the corpus moves. NULLABLE for rows
    # that still name a `path`.
    Column("content", LargeBinary),
)


# ── the authored documents (ADR 0234) ────────────────────────────────
#
# Postgres is the system of record; the files under `VENONA_CONFIG_DIR` and
# `VENONA_PROFILE_DIR` are written out of these rows at boot, because every
# loader takes a path.

config_documents = Table(
    "config_documents",
    metadata,
    # The `config_service.EDITABLE` key, not a filename.
    Column("key", Text, primary_key=True),
    Column("content", Text, nullable=False),
    Column("version", Text, nullable=False),
    # ISO-8601 UTC with Z, and the materialized file's mtime is set from it, so
    # `freshness.config_stamp` still answers "when did a person change this".
    Column("updated_at", Text, nullable=False),
)

auth_state = Table(
    "auth_state",
    metadata,
    # The `hub_flags` singleton: one row that always exists, so no read is a
    # special case.
    Column("id", SmallInteger, primary_key=True),
    # TEXT holding a float, matching `users.session_epoch` so the two compare
    # without a conversion that could round differently.
    Column("epoch", Text, nullable=False),
    Column("updated_at", Text),
)

harness_homes = Table(
    "harness_homes",
    metadata,
    # One row per SERVICE, not per billing mode: a home belongs to the CLI.
    Column("harness", Text, primary_key=True),
    # Relative path to base64 content, only what a driver declares as
    # `credential_paths`. NO ROUTE RETURNS THIS, as with `harness_credentials`.
    Column("files", Text, nullable=False),
    # So an unchanged home is not rewritten after every model call.
    Column("digest", Text, nullable=False),
    Column("captured_at", Text, nullable=False),
)

config_document_history = Table(
    "config_document_history",
    metadata,
    Column("key", Text, primary_key=True),
    Column("version", Text, primary_key=True),
    Column("content", Text, nullable=False),
    # ALL THREE ARE THE KEY: reverting and re-saving is the same content under
    # a later stamp, and keying on (key, version) dropped 2 of 7 ontology and
    # 2 of 13 settings snapshots on the live instance's first import.
    Column("stamp", Text, primary_key=True),
)
