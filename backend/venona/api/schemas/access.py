"""Access-request wire types. snake_case, ISO-8601 UTC with `Z` (Conventions)."""

from __future__ import annotations

import re

from pydantic import BaseModel, Field, field_validator

# Deliberately NOT `pydantic.EmailStr`, which needs the `email-validator`
# package -- a dependency this project does not carry, added here for one field
# on one form.
#
# And deliberately not an attempt at RFC 5322, which permits quoted strings,
# comments and nested parens, and which no hand-written regex has ever got
# right. The only question this has to answer is "could a human plausibly
# receive mail here", because the operator reads every row before acting on it
# and the real validation is that a reply arrives. One `@`, something either
# side, a dot in the domain, no whitespace.
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s.]+(\.[^@\s.]+)+$")

EMAIL_MAX = 254  # RFC 5321 §4.5.3.1, the one length limit that is not arbitrary.

# The role set the form offers comes from `config/domain.yaml::access_roles`
# and is enforced by the validator on `AccessRequestIn.role` -- a closed set
# rather than a free string because this is the one field the operator triages
# on, and "Analyst"/"analyst "/"anaylst" in the same column makes that column
# useless within a week.
#
# THIS IS THE GATE: the `access_requests.role` column carries no CHECK
# constraint (`0001_baseline.sql`), on the grounds that the set is a product
# decision that will change and adding to a CHECK rewrites the table. The set lives in the
# domain descriptor because it is a statement about who reads a domain's
# press, which is exactly what that file is for. The defense set includes
# `other` on purpose -- a required field with no honest answer gets a
# dishonest one, and a wrong role is worse than a vague one.

# Long enough for a real paragraph about what someone follows, short enough that
# the column is not an upload endpoint. A public unauthenticated write with an
# unbounded text field is a way to fill someone's disk.
COVERAGE_MAX = 2000


class AccessRequestIn(BaseModel):
    """What the form posts.

    `extra` is left at pydantic's default rather than `forbid`: this model is
    filled in by a browser on the open internet, and rejecting a request outright
    because a form library added a field is a worse failure than ignoring it. The
    contract that matters here is that nothing UNDECLARED is ever read.
    """

    email: str = Field(min_length=3, max_length=EMAIL_MAX)
    coverage: str | None = Field(default=None, max_length=COVERAGE_MAX)
    role: str = Field(max_length=32)

    @field_validator("role")
    @classmethod
    def _role_is_offered(cls, value: str) -> str:
        from venona.domain import get_domain

        roles = get_domain().access_roles
        if value not in roles:
            raise ValueError(f"role must be one of: {', '.join(roles)}")
        return value

    @field_validator("email")
    @classmethod
    def _parses(cls, value: str) -> str:
        """Lower-cased on the way in, because the allow-list is lower-cased on
        the way out (`security.py::email_is_allowed`). Storing the address as
        typed would make "is this the person I already added?" a manual
        comparison the operator has to remember to do case-insensitively."""
        trimmed = value.strip().lower()
        if not EMAIL_RE.match(trimmed):
            raise ValueError("that does not look like an email address")
        return trimmed

    @field_validator("coverage")
    @classmethod
    def _blank_is_none(cls, value: str | None) -> str | None:
        """An empty textarea posts `""`, not absence. Storing that as a value
        makes "did they tell us anything?" a two-case question forever."""
        if value is None:
            return None
        trimmed = value.strip()
        return trimmed or None


class AccessRequest(BaseModel):
    """One row of the queue, as an ADMIN sees it.

    A different model from `AccessRequestOut` on purpose. That one is the
    unauthenticated response and says almost nothing, because echoing a stored
    row would confirm which addresses are on file. This one is behind
    default-deny and shows everything, including the coverage note in full --
    truncating it is what `cli.py::_access_list` refuses to do, on the grounds
    that it is the only thing distinguishing a real analyst from a scraper.
    """

    id: str
    email: str
    coverage: str | None = None
    role: str
    status: str
    created_at: str
    decided_at: str | None = None
    note: str | None = None
    #: Who decided, as an address. None means `venona access decide` did -- the
    #: same honest NULL every actor column in this schema carries (C1).
    decided_by_email: str | None = None
    #: The account this request became, if it became one. None for pending, for
    #: declined, and for every row decided before migration 0005.
    account_email: str | None = None
    account_status: str | None = None


class AccessRequestList(BaseModel):
    """A window over the queue.

    `pending` is the badge -- always the unfiltered count of what awaits a
    decision. `total` is what `items` is a page of, under whatever filter
    selected it. They answer different questions and are both wanted.
    """

    items: list[AccessRequest] = Field(default_factory=list)
    pending: int = 0
    total: int = 0


class AccessDecision(BaseModel):
    """What an admin was looking at when they decided. Free text, for their own
    benefit six weeks later -- the same field `access_requests.note` has always
    been, now reachable from the screen that makes the decision."""

    note: str | None = Field(default=None, max_length=500)


class AccessRequestOut(BaseModel):
    """Deliberately says almost nothing.

    This endpoint is unauthenticated, so its response must not become an oracle.
    Echoing the stored row would confirm which addresses are already on file, and
    returning a different shape for a duplicate would confirm it just as well --
    so a duplicate and a first-time request are byte-identical here. `received`
    is not `created`: whether a row was written is not the caller's business.
    """

    received: bool = True
    message: str
