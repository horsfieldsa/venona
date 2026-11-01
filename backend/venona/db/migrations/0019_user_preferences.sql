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
