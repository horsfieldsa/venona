"""Auth wire types. snake_case, ISO-8601 UTC with `Z` (Conventions)."""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


class TokenLogin(BaseModel):
    """Break-glass, and the only login `scripts/ui_smoke.py` can drive.

    A headless browser cannot complete a Google consent screen, and neither can
    an operator whose OAuth client is misconfigured -- which is the state the
    app is in exactly when they most need to get at it.
    """

    token: str = Field(min_length=1)


class BillingState(BaseModel):
    """What the shell shows about a subscription.

    `notice` is a KIND and never a sentence. The words live in
    `web/src/lib/billing.ts` and nowhere else, for the reason CLAUDE.md gives
    about run outcomes: four surfaces once decided the wording of a run
    independently and one of them was wrong about the world. This one would be
    wrong about somebody's money.
    """

    #: none | trialing | active | past_due | lapsed | comped
    status: str
    entitled: bool
    #: When the current period ends: the next charge for a subscriber, the last
    #: covered day for somebody lapsed. NOT the archive ceiling, which is
    #: derived from this and is null for anybody entitled.
    entitled_through: str | None = None
    notice: str | None = None
    notice_at: str | None = None
    #: Whether this instance charges at all. Off means every billing surface
    #: stays hidden, which is the right state for a local or self-hosted box.
    enforced: bool = False
    #: Where the subscribe and manage links point. The hub owns Stripe; the
    #: instance only ever links out (ADR 0209).
    manage_url: str | None = None


class SessionInfo(BaseModel):
    authenticated: bool
    email: str | None = None
    via: str | None = None
    expires_at: str | None = None
    #: `admin` or `reader`. What the shell uses to decide which surfaces to offer.
    #:
    #: A HINT FOR THE UI, NEVER THE ENFORCEMENT. `api/authz.py` refuses the
    #: request server-side, before routing; this only stops a reader being shown a
    #: link to a 403. Anything that treats it as the gate has the check on the
    #: wrong side of the wire.
    role: str | None = None
    #: `users.id`. Stable across an email change, which is why the UI keys on it
    #: rather than on the address.
    user_id: str | None = None
    #: The first-boot gate's signal, on the one request the SPA must answer
    #: before mount. It rode `/health` alone, but health probes Neo4j and the
    #: claude CLI -- so the router's setup gate either blocked first paint on a
    #: subprocess spawn or raced the poll. The session probe already blocks
    #: mount and touches neither.
    setup_required: bool = False
    #: The subscription, as the reader's own shell needs it. Rides the session
    #: for the same reason `timezone` does: this is the one request the SPA
    #: already makes before mount, and the dunning banner has to be able to
    #: appear on the first page painted rather than after a second fetch.
    #:
    #: A HINT FOR THE UI like `role`, never the enforcement -- `AuthMiddleware`
    #: refuses the request before routing.
    billing: BillingState | None = None
    #: The reader's stored zone override, or null to mean "use whatever zone
    #: this browser reports". Rides the session because the brief needs it for
    #: FIRST PAINT and this probe is the one request the SPA already makes
    #: before mount; a second blocking fetch in front of the page would buy
    #: nothing. Written through `PATCH /api/preferences`, never here.
    timezone: str | None = None
    #: Whether this reader has tuned their personal relevance (a
    #: `relevance_settings` row exists). Rides the session payload for the
    #: same reason `setup_required` does: the first-visit invitation must be
    #: decidable before first paint without a second round trip.
    relevance_tailored: bool = False
    #: From the OIDC `name` claim, or whatever the operator typed. None when the
    #: provider never sent one -- `AccountView` falls back to initials off the
    #: address, and an empty string here would render as a blank name rather than
    #: as an absent one.
    display_name: str | None = None
    #: `users.created_at`. "Member since", and the only date the account page can
    #: honestly show: `expires_at` is about this session, not this person.
    created_at: str | None = None
    #: WHO AUTHENTICATED THEM, which is not `via`. `via` is how THIS REQUEST
    #: arrived (`session|bearer|mcp|disabled`) and is never `google`; this is
    #: derived from `users.oidc_issuer`, stamped at the callback. None means they
    #: have never completed an OIDC sign-in -- an account created by an admin and
    #: not yet used, or a bearer principal with no row at all.
    provider: str | None = None
    #: The avatar, from the OIDC `picture` claim, https-only. None until a
    #: login carries one; the account menu falls back to its glyph.
    picture_url: str | None = None
    #: Where the browser goes AFTER `POST /auth/logout`, so ending a session
    #: here also ends it at the identity provider. The hub's end-session URL
    #: on a hub-flipped instance; None otherwise, and the client falls back
    #: to its own /login. Hub is the entry point, so signing out lands on the
    #: hub's sign-in page -- signed out everywhere, not signed out of one
    #: door while the other stays open.
    logout_url: str | None = None


class DisplayNameUpdate(BaseModel):
    """The one thing a reader may change about themselves.

    ONE FIELD, and the model is the whole allowlist. `response_model` filters on
    the way OUT and nothing filters on the way in, so a model carrying `role`
    "for completeness" would be a self-elevation endpoint that reviews clean.

    Bounded because it renders in the shell on every page and is written by the
    person it names; blank is normalised to None so "did they set one?" stays a
    one-case question, the same reason `AccessRequestIn.coverage` does it.
    """

    display_name: str | None = Field(default=None, max_length=120)

    @field_validator("display_name")
    @classmethod
    def _blank_is_none(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip() or None


class AuthMode(BaseModel):
    """What the login page should offer. Unauthenticated by necessity: the
    browser has to know whether to draw a Google button before it can log in."""

    enabled: bool
    google: bool
    token: bool
    #: Domain identity for the pre-auth pages -- the login blurb and the
    #: request-access role list are the descriptor's to state, and this is the
    #: one endpoint those pages can reach.
    display_name: str = ""
    login_blurb: str = ""
    access_roles: list[str] = []
