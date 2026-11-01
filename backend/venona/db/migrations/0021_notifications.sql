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
