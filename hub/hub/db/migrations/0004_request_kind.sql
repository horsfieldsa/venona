-- Two doors now open onto this queue, and they ask for different things.
--
-- `wanted_instance_id` already says WHICH domain someone wants. It cannot say
-- whether they are asking to be let in today or asking to be told when there
-- is anything to be let into: a separate front site's form promises one message
-- at launch and nothing else, while the portal's form is a person who has
-- already signed in and is standing at a locked door. Granting the first is
-- premature and dismissing the second is a mistake, so the operator has to be
-- able to tell them apart before deciding.
--
-- 'access' is the default because every row that existed before this column
-- came through the portal, which is the access door.
ALTER TABLE access_requests ADD COLUMN kind TEXT NOT NULL DEFAULT 'access'
    CHECK (kind IN ('access', 'launch'));

-- The dedup key gains `kind` for a case the two-door split creates: someone
-- leaves an address at launch, then signs in later and asks for access. Both
-- carry a NULL `wanted_instance_id`, so under the old key the second insert
-- hit ON CONFLICT DO NOTHING and vanished -- silently, because the endpoint is
-- non-echoing by design and cannot report a dropped row. The request that
-- actually needs a decision was the one being thrown away.
ALTER TABLE access_requests DROP CONSTRAINT access_requests_email_wanted_instance_id_key;
ALTER TABLE access_requests ADD CONSTRAINT access_requests_email_wanted_kind_key
    UNIQUE NULLS NOT DISTINCT (email, wanted_instance_id, kind);
