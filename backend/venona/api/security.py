"""Auth primitives. Env-only, and deliberately so.

WHY ENV-ONLY, AND NOT `Settings`: `config/settings.yaml` is writable over HTTP
(`PUT /api/config/settings`) and readable over HTTP (`GET` returns the whole
document). Anything on the `Settings` model is therefore both attacker-writable
and operator-visible. Auth material must be neither, so it is read from
`os.environ` here and is not a settings field. `config_service.FROZEN_TOP`
closes the same door from the other side.

Zero new dependencies. `hmac` + `hashlib` sign the session, `secrets` compares
tokens, and Google's identity is established by a server-to-server code
exchange (see `oauth.py`) rather than by verifying a JWT signature locally --
which is what would have required `cryptography`.

`api/` is outside `lint:determinism`'s guarded packages, so `time.time()` here
is legal. It is also correct: a session's expiry is wall-clock by definition and
has nothing to do with a run's reproducibility.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

log = logging.getLogger(__name__)

COOKIE_NAME = "venona_session"
STATE_COOKIE = "venona_oauth_state"

# The login round trip: long enough to read a consent screen and pick an
# account, short enough that a leaked state cookie is worthless.
STATE_TTL_SECONDS = 300

DEFAULT_SESSION_TTL = 14 * 24 * 60 * 60

# A hand-typed API token is not a token. `venona auth token` prints 32 random
# bytes; anything materially shorter is a password someone thought of, and this
# credential is the break-glass path into a machine on the internet.
MIN_TOKEN_CHARS = 32


class AuthNotConfigured(RuntimeError):
    """Auth is on but unusable. Raised at startup, never per request."""


def cors_origins() -> tuple[str, ...]:
    """Origins allowed to call `/api` cross-origin. Empty by default.

    Read from `VENONA_CORS_ORIGINS` (comma-separated) for the same reason
    everything else in this module is env-only: `config/settings.yaml` is both
    writable and readable over HTTP, so an origin allowlist living there would
    be attacker-writable -- and adding your own origin to it is a complete
    read of the API from a page you control.

    Empty means the middleware is never installed at all. The browser extension
    is what this exists for: the SPA is same-origin in every deployment -- Vite's
    proxy in development, the api itself or a reverse proxy in front of it in production
    -- so nothing the app serves needs it.

    `*` is REFUSED rather than honoured. With a wildcard, every page on the
    internet can read `/api` -- and because this app is single-user and the
    interesting endpoints are all authenticated, the wildcard reads as harmless
    right up until someone pairs it with a session cookie. Naming the origin is
    two seconds of work and the difference between a door and a doorway.
    """
    raw = os.environ.get("VENONA_CORS_ORIGINS", "")
    origins = tuple(o.strip() for o in raw.split(",") if o.strip())
    if "*" in origins:
        raise AuthNotConfigured(
            "VENONA_CORS_ORIGINS must name origins explicitly; `*` would let any "
            "page on the internet read /api. Use the extension's "
            "chrome-extension://<id> origin, or a comma-separated list."
        )
    return origins


# Agent credentials, for the MCP surface. `vmcp_` + 32 random bytes.
#
# The prefix is load-bearing three times: `principal_for` routes on it so that a
# root-token request never opens a database connection (a session request does,
# through the injected `lookup_user`), a leaked string is greppable and
# self-identifying in a log, and `assert_usable` can
# refuse one pasted into VENONA_AUTH_API_TOKEN -- a real mistake, and one that
# would silently hand the root token's power to a string the operator believed
# was restricted.
MCP_TOKEN_PREFIX = "vmcp_"

#: The hub's management credential (docs/adr/0142). Env-only, like the root
#: token -- no DB row, so revocation is rotate-and-redeploy -- and leashed to
#: the `/api/mgmt/*` paths named in `authz.MGMT_REACH`. Prefixed so that
#: `verify_bearer` can refuse it the way it refuses `vmcp_`: a management
#: credential must never be exchangeable for an unscoped session.
MGMT_TOKEN_PREFIX = "vmgmt_"


def mint_mgmt_token() -> str:
    return MGMT_TOKEN_PREFIX + secrets.token_hex(32)


def looks_like_mgmt_token(value: str) -> bool:
    return value.startswith(MGMT_TOKEN_PREFIX)


def new_mcp_token() -> str:
    return MCP_TOKEN_PREFIX + secrets.token_hex(32)


def looks_like_mcp_token(value: str) -> bool:
    return value.startswith(MCP_TOKEN_PREFIX)


def hash_token(presented: str) -> str:
    """The stored form. See `0001_baseline.sql` for why this is not a KDF."""
    return hashlib.sha256(presented.encode()).hexdigest()


@dataclass(frozen=True)
class Principal:
    """Who is making this request, how they proved it, and what it may reach."""

    email: str
    via: str  # "session" | "bearer" | "mcp" | "mgmt" | "disabled"
    # The paths this credential is admissible on. `None` means unrestricted and
    # is the DEFAULT: session, root-bearer and disabled principals are untouched.
    #
    # A frozenset is a leash. `AuthMiddleware` checks it against the REQUEST
    # path, not against a route's self-declaration, which is what makes a route
    # added next month unreachable by an agent without somebody naming it. That
    # is `EXEMPT_PATHS`' guarantee run backwards -- see contract C12.
    reach: frozenset[str] | None = None
    # The `mcp_tokens` row, for revocation and for the per-credential rate-limit
    # bucket. Keying the bucket on this rather than on the client address is the
    # whole reason the bucket can be exact: a hosted agent's requests arrive from
    # a shared egress range.
    token_id: str | None = None
    # The `users.id` behind this request, and the role `api/authz.py` checks.
    #
    # `role` DEFAULTS TO ADMIN, which looks like the wrong way round and is not:
    # the two principals that carry no user row are the root bearer and
    # `via="disabled"`. Defaulting them to reader would silently break `make dev`
    # (auth off) and every break-glass use of the root token, in a way whose
    # symptom is a 403 from a machine holding the highest credential there is.
    #
    # A SESSION principal is different: it is only ever constructed from a user
    # row, so its role is whatever that row says. `user_id` is None exactly when
    # there is no row — never as a fallback for one that failed to load.
    #
    # The literal rather than `authz.ADMIN` because this module imports nothing
    # from `venona.*`; see the module docstring. `tests/test_authz.py` pins the
    # two spellings together so they cannot drift.
    user_id: str | None = None
    role: str = "admin"
    # Whether this principal may have the PAID product, and how far back the
    # archive opens for them. Same defaulting argument as `role` above and for
    # the same reason: the principals carrying no user row are the root bearer,
    # the break-glass env admin and `via="disabled"`, and a paywall refusing any
    # of those would fire exactly when everything else is already broken.
    #
    # `entitled=True` with `archive_ceiling=None` is "no limit". A ceiling is
    # only ever set from a real user row.
    entitled: bool = True
    archive_ceiling: str | None = None
    # The status word itself, because `entitled` alone cannot tell trialing from
    # active from past_due -- and those are three different things to say to
    # somebody. `comped` is the honest default for a principal with no row.
    billing_status: str = "comped"
    # The raw `users.entitled_through`, which is NOT `archive_ceiling`. The
    # ceiling is derived and is None for anybody entitled -- correct for a query,
    # useless for a page, because "what will I be charged and when" is the
    # question a subscriber actually has. Two facts, two fields.
    billing_through: str | None = None
    # What to TELL this reader about their subscription, as a kind. The words
    # live in `web/src/lib/billing.ts`; nothing here or in a template turns one
    # into a sentence (CLAUDE.md, the run-outcome rule).
    billing_notice: str | None = None
    billing_notice_at: str | None = None


@dataclass(frozen=True)
class AuthConfig:
    enabled: bool
    session_secret: bytes
    allowed_emails: frozenset[str]
    api_token: str | None
    #: VENONA_TOKEN_LOGIN. Whether the BROWSER token path (the /login form and
    #: POST /api/auth/token-login) is offered once an OIDC login exists. Read
    #: through `token_login_enabled`, never directly.
    token_login: bool
    #: The hub's per-instance management token (VENONA_MGMT_TOKEN). None means
    #: no hub reaches this instance's mgmt surface at all.
    mgmt_token: str | None
    #: The OIDC issuer this instance trusts. None means Google; a hub deployment
    #: sets VENONA_OIDC_ISSUER to its own origin and the endpoints derive from it
    #: (api/oauth.py). Client id/secret follow the same override: VENONA_OIDC_*
    #: wins, VENONA_GOOGLE_* is the fallback.
    oidc_issuer: str | None
    google_client_id: str | None
    google_client_secret: str | None
    public_url: str
    cookie_secure: bool
    session_ttl: int

    @property
    def google_configured(self) -> bool:
        """Named for Google; true whenever AN OIDC login is configured, Google
        or hub. The login page's button reads this."""
        return bool(self.google_client_id and self.google_client_secret)

    @property
    def hub_issuer(self) -> str | None:
        """The custom issuer, when one is configured. None = Google."""
        return self.oidc_issuer

    @property
    def token_login_enabled(self) -> bool:
        """Whether the browser token-login path is offered AND accepted.

        Implicitly on while no OIDC login is configured -- a token-only
        instance has no other way in, and the pre-hub bootstrap depends on it
        -- and otherwise off unless VENONA_TOKEN_LOGIN=1 says so, which is the
        break-glass switch for the day the hub is down. Bearer auth on the
        API itself (`verify_bearer`) is a different thing and never gated
        here: the extension and MCP clients hold tokens, not sessions.
        """
        return bool(self.api_token) and (self.token_login or not self.google_configured)

    @property
    def redirect_uri(self) -> str:
        """Fixed, from configuration. NEVER derived from the request.

        `request.url_for` renders `http://` behind a TLS-terminating proxy
        unless the forwarded headers are trusted, and Google rejects the
        mismatch; deriving it from the `Host` header instead would take the
        value from the caller. Both failure modes are confusing and one is a
        vulnerability, so the redirect URI is simply told to us.
        """
        return self.public_url.rstrip("/") + "/api/auth/callback"


def _flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() not in ("0", "false", "no", "off")


def load_dotenv_into_environ() -> None:
    """Make `.env` mean what everyone assumes it means.

    `Settings` reads `.env` through pydantic-settings, which populates the MODEL
    and never touches `os.environ`. This module deliberately reads `os.environ`
    and not `Settings` -- see the module docstring -- so without this,
    `VENONA_AUTH_ENABLED=0` in `.env` is silently ignored and `make dev` refuses
    to start with a message about a variable the developer has already set.

    (That is not hypothetical. It is what happened the first time `make dev` ran
    after auth landed.)

    `override=False`: a real environment variable always wins, so a container's
    `env_file:` and `environment:` are unaffected by a stray `.env` in an image.
    """
    try:
        from dotenv import load_dotenv
    except ImportError:  # pragma: no cover - pydantic-settings brings it in
        return
    root = Path(__file__).resolve().parents[3]
    load_dotenv(root / ".env", override=False)


@lru_cache(maxsize=1)
def get_auth_config() -> AuthConfig:
    load_dotenv_into_environ()
    enabled = _flag("VENONA_AUTH_ENABLED", True)
    secret = os.environ.get("VENONA_AUTH_SESSION_SECRET", "").strip()
    emails = frozenset(
        e.strip().lower()
        for e in os.environ.get("VENONA_AUTH_ALLOWED_EMAILS", "").split(",")
        if e.strip()
    )
    token = os.environ.get("VENONA_AUTH_API_TOKEN", "").strip() or None

    cfg = AuthConfig(
        enabled=enabled,
        session_secret=secret.encode(),
        allowed_emails=emails,
        api_token=token,
        token_login=_flag("VENONA_TOKEN_LOGIN", False),
        mgmt_token=os.environ.get("VENONA_MGMT_TOKEN", "").strip() or None,
        oidc_issuer=os.environ.get("VENONA_OIDC_ISSUER", "").strip().rstrip("/") or None,
        google_client_id=(
            os.environ.get("VENONA_OIDC_CLIENT_ID", "").strip()
            or os.environ.get("VENONA_GOOGLE_CLIENT_ID", "").strip()
            or None
        ),
        google_client_secret=(
            os.environ.get("VENONA_OIDC_CLIENT_SECRET", "").strip()
            or os.environ.get("VENONA_GOOGLE_CLIENT_SECRET", "").strip()
            or None
        ),
        public_url=os.environ.get("VENONA_PUBLIC_URL", "http://localhost:5173").strip(),
        cookie_secure=_flag("VENONA_AUTH_COOKIE_SECURE", True),
        session_ttl=int(os.environ.get("VENONA_AUTH_SESSION_TTL_SECONDS") or DEFAULT_SESSION_TTL),
    )
    return cfg


def assert_usable(cfg: AuthConfig) -> None:
    """Startup gate. Every branch here fails CLOSED and says what to do.

    The empty-allowlist case is the one that matters most: a Google OAuth client
    will happily authenticate *any* Google account. Treating "no allowlist" as
    "allow everyone" would put the app on the internet behind a login page that
    looks exactly like security and is not.
    """
    if not cfg.enabled:
        return
    if len(cfg.session_secret) < 32:
        raise AuthNotConfigured(
            "VENONA_AUTH_SESSION_SECRET is missing or too short (need >=32 bytes). "
            "Generate one with `uv run venona auth new-secret`. "
            "For local development without auth, set VENONA_AUTH_ENABLED=0 in .env."
        )
    if not cfg.google_configured and not cfg.api_token:
        raise AuthNotConfigured(
            "auth is enabled but there is no way to log in: set "
            "VENONA_GOOGLE_CLIENT_ID/VENONA_GOOGLE_CLIENT_SECRET, or "
            "VENONA_AUTH_API_TOKEN, or both. See docs/DEPLOYMENT.md."
        )
    if cfg.google_configured and not cfg.allowed_emails and not cfg.hub_issuer:
        # A hub issuer asserts MEMBERSHIP with every login (the venona_role
        # claim), so an empty env allowlist is legal there -- the hub's
        # memberships table is the allowlist. Google asserts identity only.
        raise AuthNotConfigured(
            "VENONA_GOOGLE_CLIENT_ID is set but VENONA_AUTH_ALLOWED_EMAILS is empty. "
            "A Google OAuth client authenticates ANY Google account, so an empty "
            "allowlist means anyone with a Google account is you. Set it."
        )
    if cfg.api_token and looks_like_mgmt_token(cfg.api_token):
        raise AuthNotConfigured(
            "VENONA_AUTH_API_TOKEN is set to a `vmgmt_` management token. That is "
            "the hub's leashed credential, not the root token -- generate a real "
            "one with `uv run venona auth token`."
        )
    if cfg.mgmt_token and not looks_like_mgmt_token(cfg.mgmt_token):
        raise AuthNotConfigured(
            "VENONA_MGMT_TOKEN does not start with `vmgmt_`. Mint one with the "
            "hub's registration flow (or `venona auth mgmt-token`), and never "
            "reuse the root token here -- the whole point of the prefix is the "
            "leash."
        )
    if cfg.api_token and looks_like_mcp_token(cfg.api_token):
        raise AuthNotConfigured(
            "VENONA_AUTH_API_TOKEN is set to a `vmcp_` agent token. That is the "
            "restricted credential minted by `venona mcp token new`, and this "
            "variable is the ROOT token -- setting it here would give an agent "
            "credential full access to every endpoint, which is the exact "
            "opposite of what minting it was for. `scripts/ui_smoke.py` also "
            "POSTs this value to /api/auth/token-login, so it would become a "
            "session. Generate a root token with `uv run venona auth token`."
        )
    if cfg.api_token and len(cfg.api_token) < MIN_TOKEN_CHARS:
        raise AuthNotConfigured(
            f"VENONA_AUTH_API_TOKEN is shorter than {MIN_TOKEN_CHARS} characters. "
            f"That is a password someone thought of, not a token, and it is the "
            f"break-glass path into a machine on the internet. "
            f"Generate one with `uv run venona auth token`."
        )


def email_is_allowed(cfg: AuthConfig, email: str) -> bool:
    return email.strip().lower() in cfg.allowed_emails


# ── the session epoch ────────────────────────────────────────────────
#
# GLOBAL revocation -- the break-glass "log everyone out". Logout writes `now`;
# a session minted before that is refused.
#
# NOT THE ONLY EPOCH. `users.session_epoch` is the per-user one, and
# `verify_session` takes the LATER of the two -- without it, one person signing
# out ends every session on the instance, invisibly, from the moment there are
# two readers.
#
# It is a ROW, in `auth_state`. Anything that lives only in this process starts
# at zero on the next boot, and a file on a container filesystem lives only
# until the next deploy -- either way every cookie a logout killed comes back
# for the rest of its fourteen-day life, which is the one thing revocation
# exists to prevent. Migration `0029_auth_state.sql` carries the argument; ADR
# 0234 is the disk it turns on.


#: The epoch this process last read. `verify_session` runs on EVERY
#: authenticated request, so this cannot be a query -- another connection per
#: request, on top of the one `AuthMiddleware` already opens, for an answer that
#: changes yearly. The arrangement `authz.refresh_billing_enforced` already uses.
#:
#: IT DOES NOT SURVIVE A SECOND REPLICA, which would not see the other's bump
#: until its next boot: a partial break-glass logout, silently.
#: `tests/test_single_process_assumptions.py` says so; epic AA's T2.
_EPOCH: float = 0.0


def refresh_epoch(conn) -> float:
    """Read the stored epoch into this process. Called once, from the lifespan."""
    from venona.db.repositories import auth_state

    global _EPOCH
    _EPOCH = auth_state.read(conn)
    return _EPOCH


def read_epoch(cfg: AuthConfig) -> float:
    """What `verify_session` compares a cookie's `iat` against.

    `cfg` is accepted and unused; dropping it would edit call sites in the one
    module where a wrong edit signs everybody out. comment-history: ok.
    """
    return _EPOCH


def bump_epoch(cfg: AuthConfig, *, now: float, conn=None) -> None:
    """Revoke every session issued before `now`. The break-glass logout.

    In-memory first so the refusal is in force before this returns, then the
    row. A logout that cannot persist still clears the cookie and refuses the
    token here; failing the request would not un-log anybody out.
    """
    global _EPOCH
    _EPOCH = now
    if conn is None:
        return
    from datetime import UTC, datetime

    from venona.db.repositories import auth_state

    try:
        stamp = datetime.fromtimestamp(now, tz=UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        auth_state.bump(conn, epoch=f"{now:.3f}", now=stamp)
    except Exception:  # noqa: BLE001 - see the docstring
        log.warning("break-glass logout could not be persisted", exc_info=True)


# ── session tokens ───────────────────────────────────────────────────


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _sign(cfg: AuthConfig, payload: bytes) -> str:
    return _b64(hmac.new(cfg.session_secret, payload, hashlib.sha256).digest())


def mint_session(cfg: AuthConfig, *, email: str, now: float) -> tuple[str, float]:
    """Returns (token, expires_at)."""
    expires = now + cfg.session_ttl
    payload = json.dumps(
        {"sub": email, "iat": now, "exp": expires}, separators=(",", ":"), sort_keys=True
    ).encode()
    return f"{_b64(payload)}.{_sign(cfg, payload)}", expires


def verify_session(
    cfg: AuthConfig,
    token: str | None,
    *,
    now: float,
    lookup_user: Callable[[str], object] | None = None,
) -> Principal | None:
    """Validate the cookie and resolve who it belongs to.

    `lookup_user` is injected for the reason `verify_mcp` is: this module holds no
    `venona.*` imports, which is what lets `tests/test_auth.py` exercise the whole
    boundary with no database. It takes an email and returns something with
    `.id`, `.role`, `.active` and `.session_epoch` — `services/user_service.py`'s
    `UserRecord` — or None.

    WHEN IT IS None, the env allow-list decides and the principal is an admin.
    Deliberately: `tests/test_auth.py` has no database, and `make dev` with
    `VENONA_AUTH_ENABLED=0` never reaches here at all. It is not a fallback for a
    lookup that FAILED — a lookup that returns None refuses the request.
    """
    if not token or "." not in token:
        return None
    encoded, _, mac = token.partition(".")
    try:
        payload = _unb64(encoded)
    except Exception:  # noqa: BLE001 - any malformed token is simply not a token
        return None
    if not hmac.compare_digest(_sign(cfg, payload), mac):
        return None
    try:
        claims = json.loads(payload)
    except json.JSONDecodeError:
        return None
    if not isinstance(claims, dict):
        return None
    if float(claims.get("exp") or 0) <= now:
        return None
    issued = float(claims.get("iat") or 0)
    if issued < read_epoch(cfg):
        return None
    email = str(claims.get("sub") or "")
    if not email:
        return None

    if lookup_user is None:
        # No database in reach. See the docstring.
        if not email_is_allowed(cfg, email):
            return None
        return Principal(email=email, via="session")

    user = lookup_user(email)
    # MEMBERSHIP AND STATUS ARE RE-CHECKED ON EVERY REQUEST, not just at login:
    # removing or suspending someone ends their sessions now, rather than waiting
    # out a fourteen-day cookie.
    if user is None or not user.active:
        return None
    # The PER-USER epoch, on top of the global one checked above.
    # `bump_session_epoch` logs one person out without touching anybody else;
    # bumping the global one ends every session on the instance.
    if issued < user.session_epoch:
        return None
    # ENTITLEMENT IS RE-READ ON EVERY REQUEST TOO, from the same row and for the
    # same reason status is: a subscription that ended an hour ago must not keep
    # reading for the fortnight a cookie lasts.
    return Principal(
        email=user.email,
        via="session",
        user_id=user.id,
        role=user.role,
        entitled=user.entitled,
        archive_ceiling=user.archive_ceiling,
        billing_status=user.entitlement_status,
        billing_through=user.entitled_through,
        billing_notice=user.billing_notice,
        billing_notice_at=user.billing_notice_at,
    )


def verify_bearer(cfg: AuthConfig, header: str | None) -> Principal | None:
    """The ROOT token only. Never an agent credential -- see below.

    AGENT TOKENS ARE REFUSED EXPLICITLY, and that refusal is the point of this
    function rather than an implementation detail. `routers/auth.py::token_login`
    exchanges a bearer for a SESSION COOKIE, and a session is unscoped: it reaches
    every route the owner's role allows. So an agent token accepted here would
    escalate a credential leashed to a named path set into one with no leash at
    all -- the full C12 write matrix, from a token sitting in a browser profile
    or an MCP client's config.

    C12 recorded this seam as "closed only incidentally": it was closed because
    `token_login` happened to call this function instead of `principal_for`, which
    DOES resolve `vmcp_` tokens against the database. Anyone consolidating the two
    for consistency would have reopened it, and every existing test would still
    have passed. The prefix check below is that seam closed on purpose, so the
    refusal survives the refactor -- `tests/test_annotate_scope.py` pins it.

    The comparison is `compare_digest` because this is a secret equality test on a
    path an unauthenticated caller can reach.
    """
    if not cfg.api_token or not header:
        return None
    scheme, _, value = header.partition(" ")
    if scheme.lower() != "bearer" or not value:
        return None
    token = value.strip()
    if token.startswith(MCP_TOKEN_PREFIX) or token.startswith(MGMT_TOKEN_PREFIX):
        return None
    if not secrets.compare_digest(token, cfg.api_token):
        return None
    return Principal(email="token", via="bearer")


# ── the short-lived OAuth state cookie ───────────────────────────────


def mint_state(cfg: AuthConfig, *, now: float, next_path: str) -> tuple[str, str, str]:
    """Returns (cookie_value, state, nonce).

    State and nonce live in a signed cookie rather than server memory so that a
    restart mid-login does not strand the callback, and so nothing has to be
    evicted. `next_path` rides along so a deep link survives the round trip.
    """
    state = secrets.token_urlsafe(24)
    nonce = secrets.token_urlsafe(24)
    payload = json.dumps(
        {"state": state, "nonce": nonce, "exp": now + STATE_TTL_SECONDS, "next": next_path},
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return f"{_b64(payload)}.{_sign(cfg, payload)}", state, nonce


def verify_state(cfg: AuthConfig, cookie: str | None, *, state: str, now: float) -> dict | None:
    """Returns the claims when `state` matches, else None."""
    if not cookie or "." not in cookie or not state:
        return None
    encoded, _, mac = cookie.partition(".")
    try:
        payload = _unb64(encoded)
    except Exception:  # noqa: BLE001
        return None
    if not hmac.compare_digest(_sign(cfg, payload), mac):
        return None
    try:
        claims = json.loads(payload)
    except json.JSONDecodeError:
        return None
    if not isinstance(claims, dict) or float(claims.get("exp") or 0) <= now:
        return None
    if not secrets.compare_digest(str(claims.get("state") or ""), state):
        return None
    return claims


def _bearer_value(header: str | None) -> str | None:
    if not header:
        return None
    scheme, _, value = header.partition(" ")
    if scheme.lower() != "bearer" or not value.strip():
        return None
    return value.strip()


def principal_for(
    cfg: AuthConfig,
    *,
    cookie: str | None,
    authorization: str | None,
    verify_mcp: Callable[[str], Principal | None] | None = None,
    lookup_user: Callable[[str], object] | None = None,
) -> Principal | None:
    """Bearer first: it is the explicit, machine-driven path, and an expired
    cookie riding along in a curl session should not shadow it.

    A PRESENTED BEARER IS THE CREDENTIAL. IT DOES NOT FALL THROUGH TO THE COOKIE.
    NOT `verify_bearer(...) or verify_session(...)`: that `or` is wrong in two
    ways, both of which appear only once a second bearer class exists:

      * A wrong, revoked or foreign `vmcp_` token presented FROM A BROWSER --
        which is exactly what the "test this token" control on /settings/agents
        does -- fell through to `venona_session` and authenticated as the
        operator, unrestricted. The control would have reported "connected" for
        every credential it was given, including a revoked one.
      * More quietly: any request carrying both a dead agent token and a live
        cookie was silently UPGRADED from `reach=MCP_PATHS` to unrestricted.

    So: if a bearer is presented at all, it decides the request. The cookie is
    consulted only when no bearer was offered.

    `verify_mcp` is injected rather than imported because this module holds no
    `venona.*` imports at all -- which is what lets `tests/test_auth.py` exercise
    the whole boundary with no database.
    """
    if not cfg.enabled:
        return Principal(email="anonymous", via="disabled")

    presented = _bearer_value(authorization)
    if presented is not None:
        if looks_like_mcp_token(presented):
            return verify_mcp(presented) if verify_mcp is not None else None
        if looks_like_mgmt_token(presented):
            return verify_mgmt(cfg, presented)
        return verify_bearer(cfg, authorization)

    return verify_session(cfg, cookie, now=time.time(), lookup_user=lookup_user)


def verify_mgmt(cfg: AuthConfig, token: str) -> Principal | None:
    """The hub's credential, leashed to the mgmt surface.

    Constant-time equality against the env value, like the root token -- and
    like it, never a fall-through: a wrong `vmgmt_` bearer is a refused
    request, not a cookie's problem. The reach import is local because this
    module deliberately holds no venona imports at module level.
    """
    if not cfg.mgmt_token:
        return None
    if not secrets.compare_digest(token, cfg.mgmt_token):
        return None
    from venona.api.authz import MGMT_REACH

    # Role stays the ADMIN default: the mgmt routes are unclassified so they
    # demand admin, and the reach frozenset is what keeps this credential from
    # being an admin anywhere else.
    return Principal(email="hub", via="mgmt", reach=MGMT_REACH)


def new_secret() -> str:
    return _b64(secrets.token_bytes(48))


def new_token() -> str:
    return secrets.token_hex(32)
