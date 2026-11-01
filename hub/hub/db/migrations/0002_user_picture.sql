-- The Google avatar. Written at login when Google sends the `picture` claim;
-- a login without it preserves the last one, same rule as display_name. The
-- hub forwards it to instances as an id_token claim, where it becomes the
-- account menu's face.
ALTER TABLE users ADD COLUMN picture_url TEXT;
