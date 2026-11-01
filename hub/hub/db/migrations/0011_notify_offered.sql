-- What each destination has been OFFERED, as against what it has turned on.
--
-- `notify_subscriptions` answers "does this destination want this notice", and
-- a row means yes while absence means no. That is one bit doing two jobs:
-- absence cannot tell "switched off on purpose" from "did not exist when this
-- destination was made". So a notice type added in a later release reached
-- nobody until somebody found the switch, and a reconcile that filled the gap
-- in would have silently undone every deliberate no at the same time.
--
-- This is the second bit. A destination is offered every type that exists when
-- it is created, and offered any new type once, at the boot that first knows
-- about it; `default_on` then decides whether that offer arrives ticked.
-- Un-ticking still sticks, because the type stays offered.
CREATE TABLE notify_offered (
    destination_id TEXT NOT NULL REFERENCES notify_destinations(id) ON DELETE CASCADE,
    -- A key from `hub/notices.py::NOTICE_TYPES`, and no foreign key for the
    -- same reason `notify_subscriptions` carries none: the registry is code.
    notice_type    TEXT NOT NULL,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (destination_id, notice_type)
);

-- Every destination that already exists has been offered the five types that
-- existed before this table did. Named literally rather than derived from
-- `notify_subscriptions`, because deriving it would read a deliberate no as
-- "never offered" and hand it back ticked at the next boot -- which is the
-- exact silent undo this table exists to prevent.
--
-- The cost is the other direction and it is the survivable one: a destination
-- made before `launch.signup` shipped is recorded as having been offered it, so
-- it stays off until somebody ticks it. One switch, found where the operator
-- would look for it anyway.
INSERT INTO notify_offered (destination_id, notice_type)
SELECT d.id, t.notice_type
FROM notify_destinations d
CROSS JOIN (VALUES
    ('access.requested'),
    ('instance.unreachable'),
    ('instance.answering'),
    ('launch.signup'),
    ('billing.changed')
) AS t(notice_type)
ON CONFLICT DO NOTHING;
