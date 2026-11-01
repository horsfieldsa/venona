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
