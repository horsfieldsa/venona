-- What a separate front site says it is running.
--
-- THE THIRD PROPERTY. Three surfaces deploy from one branch on separate build
-- filters, so they can each be a different release at the same time, and the
-- operator page exists to make that visible. The two Docker services answer for
-- themselves when polled; a static site has no process to ask, so its build
-- stamps `/version.json` into the published tree and this row is where the last
-- successful read of it lands.

-- NOT COLUMNS ON `hub_settings`. That table holds the switches an operator
-- throws -- facts the hub DECIDED -- and this is a fact the hub OBSERVED. The
-- distinction is the same one `instances` draws between its own columns and
-- `last_status`, and it is worth a table because the two have different writers:
-- an operator writes one, the poller writes the other.
--
-- ONE ROW, EVER, for the reason `hub_settings` gives: there is one marketing
-- site, so this is one fact about the hub rather than a fact about a domain, and
-- the CHECK makes that structural instead of a convention an INSERT breaks.
CREATE TABLE site_status (
    id         SMALLINT PRIMARY KEY CHECK (id = 1),
    -- The body verbatim, like `instances.last_status`, for the same reason: the
    -- shape belongs to the surface that publishes it, and a hub that unpacked it
    -- into columns would need a migration to see a field the site added.
    last_read  JSONB,
    -- When it last answered. A version with no recency cannot be told from a
    -- version that stopped being confirmed a month ago.
    last_seen_at TIMESTAMPTZ
);

-- Inserted here, in the same transaction as the table, exactly as
-- `hub_settings` does: a missing row would make every read a special case.
INSERT INTO site_status (id) VALUES (1);
