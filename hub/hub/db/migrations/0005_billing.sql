-- Money. The hub is the only side that talks to Stripe (ADR 0209): one customer
-- per person, one subscription per seat, and the instances downstream receive a
-- projection they can enforce without holding a credential.

-- One customer per PERSON, not per membership. Somebody reading two domains is
-- one payer with one card on file, and Stripe's customer is where that lives.
-- UNIQUE because two rows pointing at one Stripe customer would make "who is
-- this webhook about" ambiguous, and that question has to have one answer.
ALTER TABLE users ADD COLUMN stripe_customer_id TEXT UNIQUE;

-- What the instance has been told, and what it should have been told. A
-- monotonic counter rather than a timestamp: the sweep that repairs a missed
-- push is then an indexed inequality instead of a clock comparison between two
-- machines, and the wire gets a version the receiver can order by.
--
-- It counts EVERYTHING THE INSTANCE RENDERS, not just access. A first card
-- decline leaves the reader entitled and changes only the banner they see; if
-- this tracked entitlement alone, in-app dunning would silently never fire,
-- which looks exactly like everything working.
ALTER TABLE memberships ADD COLUMN entitlement_version BIGINT NOT NULL DEFAULT 0;
ALTER TABLE memberships ADD COLUMN entitlement_pushed_version BIGINT NOT NULL DEFAULT 0;

-- There is deliberately no `trial_ends_at` here. The card is collected up front
-- (ADR 0212), so the trial is a Stripe object -- `status='trialing'` with a
-- `trial_end` -- and a second copy in this table could only ever disagree with
-- the system that actually converts it.

CREATE TABLE subscriptions (
    id                     TEXT PRIMARY KEY,          -- sub:<uuid7>, ours
    user_id                TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    instance_id            TEXT NOT NULL REFERENCES instances(id) ON DELETE CASCADE,
    stripe_subscription_id TEXT NOT NULL UNIQUE,      -- theirs
    status                 TEXT NOT NULL,
    price_id               TEXT,
    current_period_end     TIMESTAMPTZ,
    cancel_at_period_end   BOOLEAN NOT NULL DEFAULT false,
    trial_end              TIMESTAMPTZ,
    -- Stripe's own notion of when this object last changed, used to discard a
    -- stale event. Events arrive concurrently and out of order; comparing
    -- against our `updated_at` would compare our clock to theirs.
    stripe_updated_at      TIMESTAMPTZ,
    -- What to TELL the reader, as a kind rather than a sentence. Stored rather
    -- than derived because two of the kinds cannot be recovered from a
    -- subscription's status at all: `action_required` comes from
    -- `invoice.payment_action_required` (SCA -- nothing is wrong with the card,
    -- the bank wants a tap) and `final_notice` from the last retry on
    -- `invoice.payment_failed`. Deriving the rest and storing these two would
    -- leave the banner with two sources and no single answer.
    --
    -- The words live in `web/src/lib/billing.ts` and nowhere else. CLAUDE.md's
    -- rule about run outcomes, applied to money.
    notice                 TEXT,
    -- The date the notice is ABOUT: the retry date, the period end. Dated copy
    -- converts and vague copy does not, and the reader's own timezone is
    -- applied at the render site from `user_preferences.timezone`.
    notice_at              TIMESTAMPTZ,
    created_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at             TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- NOT `UNIQUE (user_id, instance_id)`. That is the obvious constraint and it
-- breaks the win-back: somebody who cancels and later resubscribes gets a NEW
-- Stripe subscription id, and a full unique index rejects the insert at the
-- exact moment they hand us money again. Keep the history; constrain what is
-- actually invariant, which is that at most one subscription is LIVE at a time.
--
-- `unpaid` is in the live set on purpose. It is a subscription Stripe has given
-- up retrying, not a closed one, and a second subscription alongside it would
-- bill somebody twice for one seat.
CREATE UNIQUE INDEX ux_subscriptions_live ON subscriptions (user_id, instance_id)
    WHERE status IN ('trialing', 'active', 'past_due', 'unpaid');

CREATE INDEX ix_subscriptions_user ON subscriptions (user_id, instance_id);
CREATE INDEX ix_subscriptions_stripe ON subscriptions (stripe_subscription_id);

-- The idempotency ledger. Stripe retries a webhook until it gets a 2xx, so the
-- same event id arrives more than once as a matter of routine rather than as a
-- fault. `INSERT ... ON CONFLICT (event_id) DO NOTHING` returning zero rows is
-- how a replay is recognised, and it is the only reliable way: an event is not
-- identifiable by its contents, two genuine events can carry identical bodies.
--
-- This catches REPLAYS OF ONE EVENT. It does not order two DIFFERENT events
-- racing the same subscription -- that is what `stripe_updated_at` and a
-- `SELECT ... FOR UPDATE` in the handler are for. The two mechanisms solve
-- different problems and neither substitutes for the other.
CREATE TABLE stripe_events (
    event_id       TEXT PRIMARY KEY,     -- Stripe's `evt_...`
    type           TEXT NOT NULL,
    received_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    processed_at   TIMESTAMPTZ,
    status         TEXT NOT NULL DEFAULT 'received'
                   CHECK (status IN ('received', 'processed', 'ignored', 'failed')),
    -- A digest rather than the payload: enough to tell a redelivery from a
    -- resend of different content when debugging, without keeping customer
    -- data we have no reason to hold.
    payload_digest TEXT,
    error          TEXT
);

CREATE INDEX ix_stripe_events_unprocessed ON stripe_events (received_at)
    WHERE processed_at IS NULL;
