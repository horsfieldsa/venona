-- Somewhere for the hub to say something.
--
-- Everything the hub knows about its own working happens where nobody is
-- looking. An access request arrives at 02:00 and sits `pending` until an
-- operator thinks to open `/admin`. An instance stops answering its status poll
-- and is demoted to `offline` after three misses, which is written to a column
-- and a log line and nowhere a person will meet it. The reading on that domain
-- has already stopped by then.
--
-- The instance answered the same problem first and this is deliberately the
-- same shape: a destination is a row, a subscription is a row, and a delivery
-- is a row. Not because symmetry is nice, but because the two questions that
-- shape could answer are the two this needs -- "tell me, but not about that",
-- and "did it actually go out".
--
-- WHAT IS DIFFERENT, and it is one thing: there is no `owner`. The instance
-- addresses two audiences, an administrator and a reader, and carries
-- `owner_kind`/`owner_id` to keep one from receiving the other's messages. The
-- hub has one audience. A column whose only value is 'operator' is not a
-- safeguard, it is a lie that reads like one, so there isn't one -- and
-- `notices` shares the rendering and the channel rather than the registry, for
-- exactly this reason.
--
-- The rules about what may be dialled, and about escaping a stranger's words,
-- are shared: `notices.urlguard` and `notices.render`. Two copies of an SSRF
-- guard is not a duplicated rule, it is a hole.

-- Somewhere a notice can go.
CREATE TABLE notify_destinations (
    -- 'dest:' + uuid7.
    id          TEXT PRIMARY KEY,
    -- 'webhook'. A column rather than an assumption, so adding a second way out
    -- is a row's value and not a schema change.
    kind        TEXT NOT NULL,
    -- What the operator calls it. Shown; never used to address anything.
    label       TEXT NOT NULL,
    -- The URL. FOR A WEBHOOK THIS IS A CREDENTIAL: anyone holding a Slack
    -- incoming address can post to that channel, so no page renders it and the
    -- console shows a derived hint instead. Plaintext rather than hashed
    -- because unlike a token it has to be REPLAYED, and unlike a password
    -- nothing ever compares it. Encrypting it would move the problem to where
    -- the key lives, which is the environment, which is where this started.
    target      TEXT NOT NULL,
    -- 'active' | 'disabled' | 'struck_out'. `disabled` is the operator's
    -- choice, `struck_out` is the hub's after three consecutive failures. Two
    -- states rather than one boolean because different people clear them for
    -- different reasons.
    status      TEXT NOT NULL DEFAULT 'active',
    -- Consecutive failures, reset by any success. Consecutive and not a rate,
    -- the same reasoning `instances.poll_misses` carries: a destination that
    -- failed one send in ten is working.
    failures    SMALLINT NOT NULL DEFAULT 0,
    last_error  TEXT,
    -- Attribution, not ownership: any operator may edit any destination.
    -- Operators are named in the environment and have no row here, so this is
    -- an address rather than a foreign key.
    created_by  TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- The same address twice is a mistake, never an intent: it would double every
-- message rather than widen anything.
CREATE UNIQUE INDEX ux_notify_destinations_target ON notify_destinations (kind, target);

-- What each destination has asked to be told about.
--
-- A row means yes. Absence means no, so a notice type added in a later release
-- reaches nobody until somebody turns it on. That is the safe direction and it
-- is also a known gap: absence cannot distinguish "switched off on purpose"
-- from "did not exist when this destination was made", so a reconcile that
-- filled in the second would silently undo the first at every boot. The fix is
-- a record of what a destination has been OFFERED, and it is not built here.
CREATE TABLE notify_subscriptions (
    destination_id TEXT NOT NULL REFERENCES notify_destinations(id) ON DELETE CASCADE,
    -- A key from `hub/notices.py::NOTICE_TYPES`. No foreign key, for the reason
    -- the instance's carries none: the registry is code, and retiring a notice
    -- type must not silently rewrite what somebody chose. An unknown key is
    -- ignored at fan-out and can be cleaned up deliberately.
    notice_type    TEXT NOT NULL,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (destination_id, notice_type)
);

-- One durable attempt to put one notice on one destination.
--
-- A queue rather than a POST at the call site, because the call sites are a
-- request handler and a poller cycle: the first must not wait on somebody
-- else's server to answer a stranger's form, and the second must not lose a
-- message because the cycle it fired in was the one that crashed.
CREATE TABLE notify_outbox (
    id             TEXT PRIMARY KEY,
    notice_type    TEXT NOT NULL,
    destination_id TEXT NOT NULL REFERENCES notify_destinations(id) ON DELETE CASCADE,
    -- The RENDERED notice, as JSON, not the facts to render later. A notice
    -- describes something that was true when it was raised; re-rendering at
    -- send time against a hub that has moved on would deliver a different
    -- sentence than the one the queue accepted, and on a retry that difference
    -- is silent.
    payload        TEXT NOT NULL,
    -- What makes a repeat the SAME notice. NULL means every occurrence is its
    -- own. An instance going offline keys on the instance and the outage, so a
    -- domain that stays down says so once rather than every five minutes
    -- forever.
    dedupe_key     TEXT,
    -- queued | sending | delivered | failed.
    status         TEXT NOT NULL DEFAULT 'queued',
    attempts       SMALLINT NOT NULL DEFAULT 0,
    -- When the sender may next pick this up. Set on every failure by the
    -- backoff, and to now() on the way in.
    next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_error     TEXT,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    delivered_at   TIMESTAMPTZ
);

-- One notice, per destination, once. A PARTIAL index because a NULL
-- `dedupe_key` means "this occurrence is its own", and NULLs would otherwise
-- either all collide or never collide -- neither of which is the rule.
CREATE UNIQUE INDEX ux_notify_outbox_dedupe
    ON notify_outbox (destination_id, notice_type, dedupe_key)
    WHERE dedupe_key IS NOT NULL;

-- The sender's claim: everything due, oldest first.
CREATE INDEX ix_notify_outbox_due ON notify_outbox (status, next_attempt_at);

-- The console's history panel, and the per-destination read behind a strike.
CREATE INDEX ix_notify_outbox_recent ON notify_outbox (destination_id, created_at DESC);
