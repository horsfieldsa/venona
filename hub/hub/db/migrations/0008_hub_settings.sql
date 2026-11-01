-- The switches an operator throws, moved out of the environment.
--
-- Both of these decide what venona IS on a given day: whether the paywall bites,
-- and whether a signed-in stranger gets a domain or waits for a person. Both
-- lived in the host's environment, so changing either meant editing YAML and
-- waiting for a deploy -- which is the wrong interface for the one you reach for
-- when a subscription has gone wrong and readers are locked out.
--
-- A DATABASE ROW RATHER THAN A DASHBOARD VARIABLE, and that is a change of mind
-- about `HUB_SELF_SERVE` (the deployment config said a variable living only in the
-- dashboard is one sync away from vanishing, which is true). A row is backed up
-- with everything else, is visible on a page, and has one writer.

-- PER INSTANCE. Domains are separate products with separate prices (migration
-- 0006), so one of them charging is not an answer about the others.
ALTER TABLE instances ADD COLUMN billing_enforced BOOLEAN NOT NULL DEFAULT false;

-- The same monotonic pair `entitlement_version` carries, for the same reason:
-- push is due when pushed < current, which is a cheap compare on a row the
-- poller already holds, and it makes an out-of-order delivery detectable in a
-- way a timestamp would not.
ALTER TABLE instances ADD COLUMN settings_version        BIGINT NOT NULL DEFAULT 0;
ALTER TABLE instances ADD COLUMN settings_pushed_version BIGINT NOT NULL DEFAULT 0;

-- ONE ROW, EVER. The hub is a single front door, so `self_serve` is one fact
-- about it rather than a fact about a domain. The CHECK is what makes that
-- structural instead of a convention somebody breaks with an INSERT.
CREATE TABLE hub_settings (
    id          SMALLINT PRIMARY KEY CHECK (id = 1),
    self_serve  BOOLEAN NOT NULL DEFAULT false,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- Who threw the switch. NULL for the row this migration inserts, which
    -- nobody threw.
    updated_by  TEXT
);

-- Inserted HERE, in the same transaction as the table. A missing row would make
-- every read a special case, and the one place that must never be a special
-- case is the one deciding whether strangers get in for free.
INSERT INTO hub_settings (id) VALUES (1);
