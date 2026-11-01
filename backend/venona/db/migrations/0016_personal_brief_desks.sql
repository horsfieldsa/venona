-- The reader's brief gets the shared brief's shape: the same desks, and the
-- Sources footer the shared record has always carried.
--
-- The desk is on the item because that is where the shared brief keeps it and
-- because the personal document's order is derived from it -- desks in the
-- profile's declared order, the reader's ranking inside each. The ranking is
-- not discarded by the grouping; it decides what sits at the top of a desk.
--
-- `source_ids` holds the reader's WHOLE ranked window, not the handful of
-- stories the brief argued about. That is the shared brief's meaning of the
-- word (everything retrieved) and a footer that listed only the written-about
-- articles would quietly claim a narrower reading than actually happened.
ALTER TABLE personal_briefs ADD COLUMN source_ids TEXT;
ALTER TABLE personal_brief_items ADD COLUMN section TEXT;
