"""Hub settings. One layer: environment (plus `.env.hub` at the repo root).

Deliberately thinner than the instance's `venona/settings.py`, and thinner than
its `api/security.py` split. The instance keeps auth material OUT of `Settings`
because `config/settings.yaml` is editable over HTTP -- a DSN there would turn
an admin session into a password read. The hub has no HTTP-editable config
surface at all, so one frozen settings object holding everything is honest.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]

MIN_SECRET_CHARS = 32


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="HUB_",
        env_file=REPO_ROOT / ".env.hub",
        extra="ignore",
    )

    # The OIDC issuer, verbatim. The instance compares `iss` byte-for-byte
    # after rstripping its own env var, so the validator below strips here too.
    public_url: str = "http://localhost:8180"
    #: A static front site the poller reads a `/version.json` off, if you run
    #: one. Empty skips the read.
    site_url: str = ""
    database_url: str = "postgresql://venona:venona-local@127.0.0.1:5432/hub"
    session_secret: str = ""
    seal_key: str = ""
    admin_token: str = ""
    oidc_signing_key: str = ""  # base64-encoded PKCS8 PEM; multi-line PEM in env files is a footgun
    google_client_id: str = ""
    google_client_secret: str = ""
    session_ttl_seconds: int = 14 * 24 * 60 * 60
    # Who may open the server-rendered operator pages (/admin): comma-separated
    # emails. Env rather than a role column for the same reason the instance's
    # break-glass allowlist is env: the hub's own database must not be able to
    # promote someone into the surface that edits the hub's database. Empty
    # means the pages refuse everyone -- the CLI and admin API still work.
    operator_emails: str = ""
    # Origins allowed to POST /request-access cross-origin: a separate front site,
    # comma-separated. Empty means no cross-origin caller is answered, which is
    # the right default for every deployment that has no separate front page.
    #
    # Scoped to that ONE route on purpose -- there is no CORS middleware here
    # and there must not be. The hub is the OIDC issuer and its pages run on a
    # session cookie; an app-wide allowance is how the chooser and the operator
    # page become cross-origin readable. See routers/portal.py.
    cors_origins: str = ""
    cookie_secure: bool = True
    # True when this process IS the public edge (a managed host: TLS terminates
    # upstream, nothing of ours in front) -- the app then stamps HSTS and the
    # CSP a reverse proxy would otherwise own. Off by default: HSTS over
    # plain-http localhost pins the dev browser to https for a year.
    edge_headers: bool = False
    poll_interval_seconds: int = 300
    poll_enabled: bool = True

    # ── billing (ADR 0209, 0212) ─────────────────────────────────────
    #
    # Empty secret keys mean billing is OFF: the routes answer 503 rather than
    # calling Stripe with no credential. That is what lets this ship dark and
    # lets a local hub run with no Stripe account at all.
    stripe_secret_key: str = ""
    stripe_webhook_secret: str = ""
    #: There are deliberately no hub-wide prices. Each instance sells its OWN
    #: product, stored on its row (migration 0006), because Stripe's portal
    #: lists a customer's subscriptions by product name -- two domains sharing
    #: a product are indistinguishable there, and a hub-wide pair can only ever
    #: name one of them. `hub instances pricing <slug>` creates them.
    #: There is deliberately no `billing_return_url`. Checkout and the portal
    #: send a reader back to the INSTANCE they were reading, and the hub already
    #: holds every instance's `public_url` -- a second, hand-kept copy could only
    #: ever name one of them, so it sent everybody to whichever instance was
    #: configured however many they subscribed to.
    #: Which customer-portal configuration to render. OPTIONAL, and outside the
    #: all-or-nothing five below on purpose: a Stripe account used by nothing
    #: else can rely on its own default.
    #:
    #: Set it when the account is SHARED. Unset, Stripe renders the account's
    #: default configuration -- which on a shared account belongs to whichever
    #: project created it, and carries their cancel policy, their plan list and
    #: their branding. It is also mutable by them, so Venona's cancel link can
    #: start answering 400 because somebody else turned a feature off. Same
    #: argument as pinning the API version.
    stripe_portal_configuration: str = ""
    #: Three days, and it is Stripe that counts them -- this is only what we ask
    #: for when a Checkout Session is created (ADR 0212).
    trial_days: int = 3

    @property
    def billing_enabled(self) -> bool:
        """Both halves or neither. A secret key with no webhook secret would
        take money and never hear that it had, which is worse than off."""
        return bool(self.stripe_secret_key and self.stripe_webhook_secret)

    @field_validator("public_url", "site_url")
    @classmethod
    def _strip_trailing_slash(cls, value: str) -> str:
        return value.strip().rstrip("/")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def cors_origin_set(settings: Settings) -> frozenset[str]:
    """Compared byte-for-byte against the `Origin` header, so entries carry the
    scheme and no trailing slash -- an origin is not a URL and `https://x/`
    never matches anything a browser sends."""
    return frozenset(
        part.strip().rstrip("/") for part in settings.cors_origins.split(",") if part.strip()
    )


def operator_set(settings: Settings) -> frozenset[str]:
    return frozenset(
        part.strip().lower() for part in settings.operator_emails.split(",") if part.strip()
    )


def assert_usable(settings: Settings) -> None:
    """Refuse to serve with a config that would fail later, or fail open.

    Every branch names the env var and the command that generates a valid
    value -- the `venona.api.security.assert_usable` posture. Called from the
    app lifespan; the CLI deliberately does not call it, so `hub secrets` can
    run on a machine with no configuration at all.
    """
    from hub import jwt, seal

    if len(settings.session_secret) < MIN_SECRET_CHARS:
        raise RuntimeError(
            "HUB_SESSION_SECRET is missing or shorter than "
            f"{MIN_SECRET_CHARS} characters. A guessable secret means forgeable "
            "sessions on an identity provider. Generate one: uv run hub secrets"
        )
    if not seal.key_usable(settings.seal_key):
        raise RuntimeError(
            "HUB_SEAL_KEY is missing or not a valid Fernet key, so instance "
            "management tokens could not be sealed at rest or unsealed for the "
            "poller. Generate one: uv run hub secrets"
        )
    if len(settings.admin_token) < MIN_SECRET_CHARS:
        raise RuntimeError(
            "HUB_ADMIN_TOKEN is missing or shorter than "
            f"{MIN_SECRET_CHARS} characters. It guards instance registration "
            "and user provisioning. Generate one: uv run hub secrets"
        )
    if settings.admin_token.startswith(("vmgmt_", "vmcp_")):
        raise RuntimeError(
            "HUB_ADMIN_TOKEN looks like an instance credential (vmgmt_/vmcp_ "
            "prefix). Those are leashed tokens for a DIFFERENT surface; putting "
            "one in the admin slot grants it everything the leash exists to "
            "refuse. Generate a dedicated token: uv run hub secrets"
        )
    try:
        jwt.load_signing_key(settings.oidc_signing_key)
    except Exception as exc:
        raise RuntimeError(
            "HUB_OIDC_SIGNING_KEY is missing or not a base64-encoded PKCS8 RSA "
            "private key, so no id_token can be signed. Generate one: "
            "uv run hub keys new"
        ) from exc
    if not (settings.google_client_id and settings.google_client_secret):
        raise RuntimeError(
            "HUB_GOOGLE_CLIENT_ID / HUB_GOOGLE_CLIENT_SECRET are not both set. "
            "The hub authenticates people with Google; without a client there "
            "is no way to sign in. Create one in the Google console with "
            f"redirect URI {settings.public_url}/auth/callback"
        )
    if not settings.public_url.startswith(("http://", "https://")):
        raise RuntimeError(
            f"HUB_PUBLIC_URL ({settings.public_url!r}) is not an http(s) origin. "
            "It is the OIDC issuer instances compare byte-for-byte."
        )
    # Billing is optional -- a hub with no Stripe account is a valid hub, and
    # this is what lets the feature ship dark. What is NOT valid is half of it:
    # a secret key with no webhook secret takes money and never hears that it
    # did, and a key with no prices means Checkout 500s at the moment somebody
    # tries to pay. Refuse the partial configuration, allow the empty one.
    billing_vars = {
        "HUB_STRIPE_SECRET_KEY": settings.stripe_secret_key,
        "HUB_STRIPE_WEBHOOK_SECRET": settings.stripe_webhook_secret,
    }
    missing = sorted(name for name, value in billing_vars.items() if not value)
    if missing and len(missing) < len(billing_vars):
        raise RuntimeError(
            "Billing is partly configured: " + ", ".join(missing) + " not set. "
            "Set all of them to take payments, or none of them to run without "
            "billing. A secret key with no webhook secret would take money and "
            "never hear that it had."
        )
