-- The hub's whole schema. Five tables.
--
-- Redirect URIs are NEVER stored: always derived as
-- public_url + '/api/auth/callback', matching the instance's own fixed
-- redirect_uri (venona.api.security.AuthConfig.redirect_uri). A stored URI is
-- one more place the value can drift from the only one the instance will send.

CREATE TABLE instances (
    id                      TEXT PRIMARY KEY,            -- inst:<uuid7>
    slug                    TEXT NOT NULL UNIQUE,
    display_name            TEXT NOT NULL,
    tagline                 TEXT NOT NULL DEFAULT '',
    public_url              TEXT NOT NULL,               -- origin, no trailing slash
    status                  TEXT NOT NULL DEFAULT 'provisioning'
        CHECK (status IN ('provisioning', 'live', 'offline', 'retired')),
    oidc_client_id          TEXT NOT NULL UNIQUE,
    oidc_client_secret_hash TEXT NOT NULL,               -- sha256 hexdigest; plaintext shown once at registration
    -- Fernet(HUB_SEAL_KEY). Sealed, not hashed, because the poller must
    -- PRESENT it; sealed, not plaintext, so a pg_dump carries no credential.
    mgmt_token_sealed       TEXT NOT NULL,
    last_status             JSONB,                       -- GET /api/mgmt/status body, verbatim
    last_seen_at            TIMESTAMPTZ,
    -- Consecutive poll failures. A column rather than poller memory so the
    -- count survives a restart; single replica makes it race-free.
    poll_misses             SMALLINT NOT NULL DEFAULT 0,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE users (
    id             TEXT PRIMARY KEY,                     -- user:<uuid7>
    email          TEXT NOT NULL UNIQUE,                 -- stored lowercased
    display_name   TEXT,
    status         TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'suspended')),
    google_subject TEXT UNIQUE,                          -- NULL until first Google login (rows are CLI-provisioned)
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_login_at  TIMESTAMPTZ
);

CREATE TABLE memberships (
    user_id     TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    instance_id TEXT NOT NULL REFERENCES instances(id) ON DELETE CASCADE,
    role        TEXT NOT NULL CHECK (role IN ('admin', 'reader')),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, instance_id)
);

CREATE TABLE auth_codes (
    code_hash   TEXT PRIMARY KEY,                        -- sha256 of the minted code; the code itself is never stored
    user_id     TEXT NOT NULL REFERENCES users(id),
    instance_id TEXT NOT NULL REFERENCES instances(id),
    nonce       TEXT NOT NULL,                           -- echoed into the id_token; the instance verifies it
    expires_at  TIMESTAMPTZ NOT NULL,                    -- mint + 300s
    -- Single-use is enforced by the redeem statement, not application checks:
    -- UPDATE ... SET redeemed_at = now()
    --   WHERE code_hash = %s AND redeemed_at IS NULL AND expires_at > now()
    --   RETURNING ...
    redeemed_at TIMESTAMPTZ
);

CREATE TABLE access_requests (
    id                 TEXT PRIMARY KEY,                 -- req:<uuid7>
    email              TEXT NOT NULL,
    note               TEXT NOT NULL DEFAULT '',
    wanted_instance_id TEXT REFERENCES instances(id),
    status             TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'granted', 'dismissed')),
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- NULLS NOT DISTINCT is load-bearing: the "no particular instance" case is
    -- (email, NULL), and default UNIQUE treats NULLs as distinct -- so an
    -- unauthenticated endpoint could grow the table without bound while the
    -- non-echoing reply looked exactly like a dedup.
    UNIQUE NULLS NOT DISTINCT (email, wanted_instance_id)
);
