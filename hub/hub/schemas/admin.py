"""The admin surface: registration (Epic E1's scaffolder contract), directory
CRUD, and credential rotation. JSON in and out so `new_instance.py`'s offline
mode can print a working curl."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class InstanceIn(BaseModel):
    slug: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9][a-z0-9-]*$")
    display_name: str = Field(min_length=1, max_length=200)
    tagline: str = Field(default="", max_length=500)
    public_url: str = Field(min_length=8, max_length=300)
    # Minted on the INSTANCE (`venona auth mgmt-token`) and handed in here;
    # the hub seals what it is given and never mints one.
    mgmt_token: str = Field(min_length=70, max_length=70)


class RegisteredInstance(BaseModel):
    id: str
    slug: str
    oidc_client_id: str
    # Plaintext exactly once, at registration; only the hash survives.
    oidc_client_secret: str


class InstanceOut(BaseModel):
    id: str
    slug: str
    display_name: str
    tagline: str
    public_url: str
    status: str
    oidc_client_id: str
    last_seen_at: str | None
    poll_misses: int
    created_at: str


class InstanceDetail(InstanceOut):
    last_status: dict[str, Any] | None


class InstanceList(BaseModel):
    instances: list[InstanceOut]


class InstancePatch(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=200)
    tagline: str | None = Field(default=None, max_length=500)
    public_url: str | None = Field(default=None, min_length=8, max_length=300)
    # Only the states an admin may assert: `retired` takes an instance out of
    # every listing and the poll cycle; `provisioning` puts it back in. The
    # poller owns `live` and `offline`.
    status: str | None = Field(default=None, pattern=r"^(retired|provisioning)$")
    mgmt_token: str | None = Field(default=None, min_length=70, max_length=70)


class RotatedSecret(BaseModel):
    oidc_client_id: str
    oidc_client_secret: str


class ProvisionUserIn(BaseModel):
    """Mirrors the instance's own `ProvisionUser` -- the hub adds nothing."""

    email: str = Field(min_length=3, max_length=254)
    role: str = Field(default="reader", pattern=r"^(admin|reader)$")
    action: str = Field(default="add", pattern=r"^(add|update|suspend|reinstate)$")


class ProvisionResult(BaseModel):
    """The instance's answer, passed through verbatim."""

    email: str
    action: str
    role: str | None
    status: str


class GrantIn(BaseModel):
    """`hub grant` over HTTP: hub user (created if new) + membership."""

    email: str = Field(min_length=3, max_length=254)
    slug: str = Field(min_length=1, max_length=64)
    role: str = Field(default="reader", pattern=r"^(admin|reader)$")
    display_name: str | None = None


class GrantResult(BaseModel):
    user_id: str
    email: str
    instance_id: str
    slug: str
    role: str
    created_user: bool
