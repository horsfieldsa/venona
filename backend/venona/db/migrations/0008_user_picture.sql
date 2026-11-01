-- The account's avatar, from the OIDC `picture` claim (Google's own, or
-- forwarded by the hub's id_token). Refreshed on every login that carries the
-- claim and preserved through one that does not -- the opposite rule from
-- display_name, because the picture is the provider's to keep current while
-- the name is the person's to keep.
ALTER TABLE users ADD COLUMN picture_url TEXT;
