-- Sign-out revocation for HUB sessions -- the same shape every instance
-- already has (venona's users.session_epoch): a per-user watermark, and a
-- session token issued before it is dead. Without this a hub session was a
-- 14-day bearer token that logout could only delete from the one browser at
-- hand, and the hub cookie is the SSO master key for every instance -- a
-- second device's surviving hub session silently re-mints the instance
-- sessions that sign-out just revoked.
ALTER TABLE users ADD COLUMN session_epoch DOUBLE PRECISION NOT NULL DEFAULT 0;
