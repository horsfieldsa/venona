"""Prefixed uuid7 ids, the instance convention: type-prefixed, sortable."""

from __future__ import annotations

from uuid_extensions import uuid7str


def instance_id() -> str:
    return f"inst:{uuid7str()}"


def user_id() -> str:
    return f"user:{uuid7str()}"


def request_id() -> str:
    return f"req:{uuid7str()}"


def client_id() -> str:
    """An OIDC client id. Prefixed like every other id, but distinctly from
    the row id -- it travels in querystrings and `aud` claims, and a value
    that reads `hubc:` is self-describing in an instance's logs."""
    return f"hubc:{uuid7str()}"


def subscription_id() -> str:
    return f"sub:{uuid7str()}"


def destination_id() -> str:
    return f"dest:{uuid7str()}"


def notice_id() -> str:
    """One queued delivery. `ntf:` matches the instance's, so an operator
    reading either service's rows meets one vocabulary."""
    return f"ntf:{uuid7str()}"
