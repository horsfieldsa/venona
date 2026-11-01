-- Authority becomes something you can change from a screen, and therefore
-- something that has to be recorded. Contract C12's authorization addendum.
--
-- WHAT CHANGED ABOVE THIS FILE. `venona users` was CLI-only, and the argument
-- for that was explicit: an HTTP endpoint that grants a role is reachable by
-- anything holding a session, so the first admin would be minted by whoever got
-- there first. That argument survives, narrowed to the half that carries it:
--
--   Over HTTP, authority may be REDUCED without limit, and EXTENDED only as far
--   as `reader`. Crossing into `admin` still requires shell access.
--
-- Granting a reader is not minting a credential -- the credential is the
-- identity provider's, and the address still has to be one its owner can
-- authenticate as. Granting an ADMIN hands out model spend, every `config/*`
-- read including the operator's own relevance profile, and `POST /api/data/purge`,
-- which is the escalation-in-duration C12 refuses for token minting.
--
-- WHY AN AUDIT TABLE IS THE PRICE OF THE SCREEN. A change made over the CLI is
-- attributable to shell access, which is already the highest credential on the
-- box. A change made over HTTP is attributable to nothing unless something
-- writes it down. This table is what makes the HTTP surface reviewable, and it
-- is therefore not optional in the way "add logging" usually is.

CREATE TABLE user_events (
  -- 'uevent:' + uuid7. Sortable by creation, which is the only order this is
  -- ever read in.
  id         TEXT PRIMARY KEY,
  -- WHO IT HAPPENED TO. Not ON DELETE CASCADE, and not by omission: a user is
  -- never deleted (see `users.status`), and if that ever changes, the record of
  -- what was done to them is exactly the thing that must not go with them.
  user_id    TEXT NOT NULL REFERENCES users(id),
  -- WHO DID IT, or NULL. NULL is honest and common, and means the same thing it
  -- means on every other actor column in this schema (contract C1): the CLI
  -- decided, or a process did. It is never backfilled -- putting a name on a
  -- change nobody signed would invent provenance, which invariant 3 forbids
  -- everywhere else.
  actor_id   TEXT REFERENCES users(id),
  -- 'role_changed' | 'suspended' | 'reinstated' | 'created'. Not a CHECK
  -- constraint, for the reason `users.role` is not one: the set is a product
  -- decision that will change, and a CHECK is the wrong place to learn that.
  action     TEXT NOT NULL,
  -- One line, for a person reading the log six weeks later: 'admin -> reader',
  -- or the id of the access request an account was created from. Free text
  -- BECAUSE it is for a human -- anything a query needs belongs in a column.
  detail     TEXT,
  created_at TEXT NOT NULL
);
-- The only query this table has: the last N, newest first. Every panel and every
-- CLI listing reads it that way and nothing reads it any other way.
CREATE INDEX ix_user_events_recent ON user_events (created_at DESC);
-- And "what happened to this person", which is the second question anyone asks
-- once they have seen the first answer.
CREATE INDEX ix_user_events_user ON user_events (user_id, created_at DESC);

-- ── the access queue joins up to real users ───────────────────────────
--
-- `0001_baseline.sql` promises, on this table: "Granting access is NOT a row in
-- this table. Nothing in the request path can grant anything." Both columns
-- below are written by an ADMIN action on a different route with a different
-- credential, never by the public form, so that promise is unchanged. What they
-- add is the ability to answer "who decided, and what did this request become",
-- which the table recorded neither of.

-- Who approved or declined it. Same nullable-actor convention as everything
-- else: NULL means `venona access decide` did it.
ALTER TABLE access_requests ADD COLUMN decided_by TEXT REFERENCES users(id);

-- The account this request became, if it became one. NULL for pending, for
-- declined, and for every row decided before this migration -- which is not
-- backfillable, because matching on the address afterwards would be a guess
-- about which request produced an account, and a guess is what provenance is
-- not.
ALTER TABLE access_requests ADD COLUMN user_id TEXT REFERENCES users(id);
