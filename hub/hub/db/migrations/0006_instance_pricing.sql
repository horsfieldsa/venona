-- Each instance sells its own product, and the subscription row remembers what
-- was actually bought.
--
-- ONE PRODUCT FOR EVERY DOMAIN DOES NOT SURVIVE THE SECOND ONE. Stripe's
-- customer portal lists a customer's subscriptions by PRODUCT NAME, so a reader
-- subscribed to two domains saw "Venona monthly $12" twice, indistinguishable,
-- with no way to tell which was which before cancelling one. The prices were
-- hub-wide environment variables, which can only ever name one domain.

ALTER TABLE instances ADD COLUMN stripe_product_id TEXT;
ALTER TABLE instances ADD COLUMN stripe_price_monthly TEXT;
ALTER TABLE instances ADD COLUMN stripe_price_annual TEXT;

-- An instance with no prices cannot sell, and says so rather than falling back
-- to somebody else's product. There is deliberately no hub-wide default: a
-- default here is the bug this migration exists to remove, one indirection
-- further away.

-- ── what the subscription row remembers ──────────────────────────────
--
-- Each of these has a NAMED CONSUMER. Stripe holds far more and most of it is
-- better fetched than mirrored; a column that nothing reads is a column that
-- silently stops being maintained.

-- The customer this subscription is actually on. `users.stripe_customer_id` is
-- the person's, and the two CAN diverge -- `set_stripe_customer` fills a NULL
-- and never overwrites, so a customer that changes leaves the binding behind.
-- Holding it here makes the divergence visible in one query, and saves the
-- portal route a round trip to Stripe to check the same thing.
ALTER TABLE subscriptions ADD COLUMN stripe_customer_id TEXT;

-- What they pay, so a surface can say it. The reader's account page promises
-- "your next charge is $12 on 30 September" and could only ever render the
-- date: the amount lived in a Stripe Price nothing on this side had read.
ALTER TABLE subscriptions ADD COLUMN unit_amount INTEGER;
ALTER TABLE subscriptions ADD COLUMN currency TEXT;
ALTER TABLE subscriptions ADD COLUMN interval TEXT;   -- month | year

-- When it actually ended, as opposed to when the period runs out. A
-- cancellation scheduled for the period end and one that happened now are
-- different facts and the operator page has to tell them apart.
ALTER TABLE subscriptions ADD COLUMN canceled_at TIMESTAMPTZ;

CREATE INDEX ix_subscriptions_customer ON subscriptions (stripe_customer_id);
