"""The hub CLI's triage commands.

Covered here because the CLI reaches the same writes as the operator page over
a different door, and a rule enforced on one door is not enforced.
"""

from __future__ import annotations

import pytest
from hub.repositories import access_requests as requests_repo
from hub.repositories import instances as instances_repo
from hub.repositories import users as users_repo

from hub import cli, ids


@pytest.fixture
def hub_env(monkeypatch, hub_settings):
    """`cli` resolves its own settings from the environment, so the throwaway
    database has to reach it the way a real invocation would."""
    monkeypatch.setenv("HUB_DATABASE_URL", hub_settings.database_url)
    from hub.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _launch_signup(conn, email: str) -> str:
    request_id = ids.request_id()
    requests_repo.submit(
        conn, request_id=request_id, email=email, note="", wanted_instance_id=None, kind="launch"
    )
    return request_id


def test_granting_a_launch_signup_is_refused(hub_env, hub_conn, capsys) -> None:
    """⭐ The same refusal the operator page makes, and it has to be here too:
    this command takes an id off a listing that shows launch rows, so the
    surface that cannot offer a Grant button can still be handed the id."""
    instances_repo.create(
        hub_conn,
        instance_id=ids.instance_id(),
        slug="acme",
        display_name="Acme Watch",
        tagline="the acme watch",
        public_url="https://acme.example",
        oidc_client_id=ids.client_id(),
        oidc_client_secret_hash="x",
        mgmt_token_sealed="y",
    )
    request_id = _launch_signup(hub_conn, "waiting@example.com")

    code = cli.main(["requests", "grant", request_id, "acme", "reader"])

    assert code != 0
    assert "named no domain" in capsys.readouterr().err
    assert users_repo.by_email(hub_conn, "waiting@example.com") is None
    assert requests_repo.by_id(hub_conn, request_id)["status"] == "pending"


def test_the_listing_names_which_door_a_row_came_through(hub_env, hub_conn, capsys) -> None:
    """An operator reading the queue has to be able to tell them apart before
    deciding, and `ls` is the surface with no sections to do it for them."""
    _launch_signup(hub_conn, "waiting@example.com")
    requests_repo.submit(
        hub_conn,
        request_id=ids.request_id(),
        email="asking@example.com",
        note="please",
        wanted_instance_id=None,
    )

    assert cli.main(["requests", "ls"]) == 0
    both = capsys.readouterr().out
    assert "launch" in both and "access" in both

    assert cli.main(["requests", "ls", "--kind", "launch"]) == 0
    only = capsys.readouterr().out
    assert "waiting@example.com" in only
    assert "asking@example.com" not in only
