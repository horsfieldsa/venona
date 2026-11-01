-- OAuth 2.1 for agents. Contract C12, and the end of the static bearer.
--
-- THE DESIGN DECISION THAT SHAPES EVERYTHING ELSE: the authorization code is
-- exchanged for an ORDINARY `mcp_tokens` ROW. There is no second credential
-- system, no parallel verification path and no second revocation story --
-- `token_service.verify` is still the only thing that turns a presented string
-- into a principal, `venona mcp token revoke` still kills an OAuth-issued token,
-- and C12's read-only-ness is untouched because it was never a property of how
-- the credential was obtained.
--
-- What OAuth adds is the WAY a token is minted: a client the operator never
-- typed a command for can obtain one, scoped and expiring, with the human
-- approving in a browser they are already signed into. What it does not add is a
-- new kind of thing to audit.
--
-- venona is BOTH the resource server and the authorization server. That sounds
-- like a shortcut and is the honest shape: there is no identity provider here to
-- delegate to -- the operator's own OIDC login is what establishes who is
-- approving, and an agent's authorization is a fact about THIS instance's
-- corpus, not a claim any external issuer could make.

CREATE TABLE oauth_clients (
  -- Issued by us at registration, opaque to the client. NOT secret: it is half
  -- of a public-client flow and appears in browser URLs.
  client_id      TEXT PRIMARY KEY,
  -- What the consent screen says. From the client's own metadata, so it is
  -- ATTACKER-CONTROLLED TEXT: rendered as text, never as markup, and the
  -- redirect URI is shown beside it because the name is the easy thing to lie
  -- about and the destination is the thing that matters.
  client_name    TEXT NOT NULL,
  -- JSON array. Exact-match only at authorize time -- no prefix, no wildcard,
  -- no "starts with". A redirect URI comparison that is anything less than
  -- equality is the classic way an authorization code ends up somewhere else.
  redirect_uris  TEXT NOT NULL,
  -- PUBLIC CLIENTS ONLY, so there is no client secret column and that is
  -- deliberate rather than unfinished. An MCP client is a desktop app or a CLI:
  -- anything it stores, the user can read, so a secret would be a secret in
  -- name only. PKCE is what actually binds the code to the client, and it is
  -- REQUIRED below rather than optional -- which is the OAuth 2.1 rule and the
  -- reason this file is not "OAuth 2.0 plus some extensions".
  created_at     TEXT NOT NULL,
  last_used_at   TEXT
);

CREATE TABLE oauth_codes (
  -- The authorization code itself, stored as a SHA-256 exactly like
  -- `mcp_tokens.token_hash` and for the same reason: a row someone can read
  -- must not contain a usable credential, and this one is usable for as long as
  -- it lives.
  code_hash      TEXT PRIMARY KEY,
  client_id      TEXT NOT NULL REFERENCES oauth_clients(client_id) ON DELETE CASCADE,
  -- WHO approved. The token minted from this code inherits this user's role, so
  -- this column is the whole authorization decision.
  user_id        TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  -- Echoed back at token time and compared for equality. RFC 6749 4.1.3.
  redirect_uri   TEXT NOT NULL,
  -- PKCE. `S256` only -- `plain` is permitted by RFC 7636 and forbidden by
  -- OAuth 2.1, and accepting it would mean the verifier travels in the same
  -- browser redirect as the challenge, which is the attack it exists to stop.
  code_challenge TEXT NOT NULL,
  -- Which reach set the resulting token gets. Resolved through
  -- `api/authz.py::SCOPES`, the same table the CLI validates against.
  scope          TEXT NOT NULL DEFAULT 'mcp',
  -- SHORT. A code is a one-time bearer that has just travelled through a
  -- browser redirect and possibly a log; ten minutes is the RFC's ceiling and
  -- there is no reason to sit at it.
  expires_at     TEXT NOT NULL,
  created_at     TEXT NOT NULL,
  -- SINGLE USE, enforced by recording the redemption rather than by deleting
  -- the row. A deleted row cannot tell a replayed code from an unknown one, and
  -- the difference matters: a replay means the code leaked, which is worth being
  -- able to see. `oauth_service.redeem` refuses a row that already has this set.
  redeemed_at    TEXT
);
CREATE INDEX ix_oauth_codes_expiry ON oauth_codes (expires_at);

-- Which client an issued token came from, and NULL for one minted at the CLI.
-- Not a foreign key onto a cascade: revoking a client's registration must not
-- silently delete the audit trail of what it was given.
ALTER TABLE mcp_tokens ADD COLUMN client_id TEXT;
