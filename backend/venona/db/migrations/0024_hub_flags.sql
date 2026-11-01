-- Switches the hub throws on this instance's behalf.
--
-- ONE ROW, and deliberately NOT `config/settings.yaml`. That file is editable
-- over HTTP by anyone holding an admin session (`services/config_service.py`),
-- and contract C12's whole argument about the paywall is that a gate an admin
-- session can switch off is not a gate. This table has no editor: the only
-- writer is `POST /api/mgmt/settings`, which is reachable with the hub's
-- `vmgmt_` credential and nothing else.
--
-- A PROJECTION, like the entitlement columns beside it (migration 0023). The
-- hub decides; this is where the decision is kept so the instance can read it
-- without asking anybody, including on a morning when the hub is down.
CREATE TABLE hub_flags (
    id               SMALLINT PRIMARY KEY CHECK (id = 1),
    -- Whether the paywall bites. FALSE is the safe default and the one that
    -- ships: an instance nobody has told anything is an instance that charges
    -- nobody, which is right for a local box, a self-hosted one, and for the
    -- window between this deploying and the hub first pushing.
    billing_enforced BOOLEAN NOT NULL DEFAULT false,
    updated_at       TEXT
);

-- In the same transaction as the table, so no read is ever a special case.
INSERT INTO hub_flags (id) VALUES (1);
