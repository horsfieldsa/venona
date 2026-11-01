"""Granting access: the one sequence shared by the admin API, the operator
page and the CLI -- create the user row if it is new, then grant (or update)
the membership. Three surfaces perform it; the semantics must not drift."""

from __future__ import annotations

from hub import ids
from hub.db.core import Conn
from hub.repositories import users as users_repo


def grant_access(
    conn: Conn, *, email: str, instance_id: str, role: str, display_name: str | None = None
) -> tuple[str, bool]:
    """Returns (user_id, created_user)."""
    email = email.strip().lower()
    user = users_repo.by_email(conn, email)
    created = user is None
    user_id = ids.user_id() if user is None else user["id"]
    if created:
        users_repo.create(conn, user_id=user_id, email=email, display_name=display_name)
    users_repo.grant(conn, user_id=user_id, instance_id=instance_id, role=role)
    return user_id, created
