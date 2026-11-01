"""The credential itself: minting, verifying, revoking, and what is NOT stored.

`tests/test_mcp_auth.py` proves where a token can go. This proves what it is.
"""

from __future__ import annotations

import inspect

import psycopg
import pytest
from venona.api import security
from venona.services import token_service


# The fixture is named `db`, which is why the module is imported under another
# name inside it rather than at the top of the file: `from venona.db import core
# as db` at module level would be shadowed by this fixture everywhere below.
@pytest.fixture
def db(database_url: str):
    from venona.db import core as dbmod

    conn = dbmod.connect(database_url)
    yield conn
    conn.close()


def _owner(conn, email="operator@example.com", role="admin"):
    """A user to own the token. An agent credential inherits its owner's role, so
    `mint` requires one -- there is no "the operator" to fall back on any more."""
    from venona.services import user_service

    # `allow_admin` because this is ARRANGE standing in for shell access: these
    # tests need an admin owner to prove an agent token inherits an admin's role
    # and is STILL read-only. The no-admin-over-a-wire rule (ADR 0168) is tested
    # where it belongs, in `test_authority.py`.
    return user_service.create(
        conn,
        user_id=f"user:{email}",
        email=email,
        role=role,
        now="2026-08-16T00:00:00Z",
        allow_admin=True,
    )


def _mint(db, label="agent", token_id="mcp_1", now="2026-08-16T00:00:00Z", **over):
    owner = over.pop("owner", None) or _owner(db)
    return token_service.mint(db, label=label, token_id=token_id, now=now, user_id=owner.id, **over)


def test_a_minted_token_round_trips(db) -> None:
    row, secret = _mint(db)
    assert secret.startswith("vmcp_")
    assert token_service.verify(db, secret)["id"] == row.id


def test_only_the_hash_is_stored(db) -> None:
    """Cheap, total, and the one that catches a debugging `print` that became a
    column. If this ever fails, the settings page can leak a live credential."""
    _row, secret = _mint(db)
    stored = db.execute("SELECT * FROM mcp_tokens").fetchone()
    for value in dict(stored).values():
        assert secret != value
        assert not (isinstance(value, str) and secret in value)
    # The prefix is display-only and must not be enough to authenticate with.
    assert token_service.verify(db, stored["prefix"]) is None


def test_mint_takes_no_caller_supplied_secret() -> None:
    """Pins the premise of storing a bare SHA-256 rather than a KDF.

    The argument is that the secret is 32 bytes of `secrets.token_hex` and so has
    no dictionary to attack. A `secret=` parameter would falsify that silently,
    and the hash choice would become wrong without anything failing.
    """
    assert "secret" not in inspect.signature(token_service.mint).parameters


def test_a_token_with_one_byte_flipped_is_refused(db) -> None:
    _row, secret = _mint(db)
    tampered = secret[:-1] + ("0" if secret[-1] != "0" else "1")
    assert token_service.verify(db, tampered) is None


def test_a_revoked_token_is_refused(db) -> None:
    row, secret = _mint(db)
    assert token_service.revoke(db, row.id, now="2026-08-16T01:00:00Z") is True
    assert token_service.verify(db, secret) is None
    # Idempotent: revoking twice is not an error, it just changes nothing.
    assert token_service.revoke(db, row.id, now="2026-08-16T02:00:00Z") is False


def test_revocation_is_a_timestamp_not_a_delete(db) -> None:
    """ "When did I turn this off" is worth being able to answer."""
    row, _secret = _mint(db)
    token_service.revoke(db, row.id, now="2026-08-16T01:00:00Z")
    stored = db.execute("SELECT revoked_at FROM mcp_tokens WHERE id = ?", (row.id,)).fetchone()
    assert stored["revoked_at"] == "2026-08-16T01:00:00Z"


def test_revocation_survives_a_restart(db, database_url: str) -> None:
    from venona.db import core as dbmod

    row, secret = _mint(db)
    token_service.revoke(db, row.id, now="2026-08-16T01:00:00Z")
    db.commit()
    # A SECOND connection to the SAME database -- the point is that revocation is
    # durable, not cached in the connection that performed it.
    fresh = dbmod.connect(database_url)
    assert token_service.verify(fresh, secret) is None
    fresh.close()


def test_a_token_from_another_instance_is_refused(db, make_database) -> None:
    from venona.db import core as dbmod

    # A genuinely SEPARATE database, which is what "another instance" means. The
    # `make_database` factory exists for this: one per-test database cannot make
    # the claim.
    other = dbmod.connect(make_database())
    _row, secret = token_service.mint(
        other,
        label="elsewhere",
        token_id="mcp_x",
        now="2026-08-16T00:00:00Z",
        user_id=_owner(other).id,
    )
    other.close()
    assert token_service.verify(db, secret) is None


def test_logout_does_not_revoke_agent_tokens(db, tmp_path, monkeypatch) -> None:
    """⭐ The trap.

    `bump_epoch` kills every SESSION everywhere, which is the honest thing for a
    browser. If an agent token consulted the epoch, one logout on the operator's
    phone would unplug every connected agent, days later, with nothing naming the
    cause.
    """
    monkeypatch.setenv("VENONA_DATA_DIR", str(tmp_path))
    security.get_auth_config.cache_clear()
    cfg = security.get_auth_config()

    _row, secret = _mint(db)
    security.bump_epoch(cfg, now=9_999_999_999.0)
    assert token_service.verify(db, secret) is not None
    security.get_auth_config.cache_clear()


def test_touch_is_throttled(db) -> None:
    """A write on every read is a write on every read.

    An agent doing hundreds of lookups against a database a forty-minute run is
    also writing to turns a read endpoint into a writer, competing for the same
    rows. The throttle is what keeps `touch` from being one."""
    row, _secret = _mint(db)
    token_service.reset_touch_cache()
    token_service.touch(db, row.id, now="2026-08-16T00:00:01Z")
    first = db.execute("SELECT last_used_at FROM mcp_tokens WHERE id=?", (row.id,)).fetchone()[0]
    token_service.touch(db, row.id, now="2026-08-16T00:00:02Z")
    second = db.execute("SELECT last_used_at FROM mcp_tokens WHERE id=?", (row.id,)).fetchone()[0]
    assert first == second


def test_touch_never_fails_a_request(db, monkeypatch) -> None:
    """A lost telemetry write must not fail the request it is describing -- the
    same fails-soft reasoning `bump_epoch` already carries for its epoch file."""
    row, secret = _mint(db)
    token_service.reset_touch_cache()

    class Locked:
        """`db.Conn.execute` is read-only, so stand in for the whole
        connection rather than patching a method onto it."""

        def execute(self, *_args, **_kwargs):
            raise psycopg.OperationalError("database is locked")

        def commit(self):  # pragma: no cover - never reached
            raise AssertionError("commit after a failed execute")

    token_service.touch(Locked(), row.id, now="2026-08-16T00:00:01Z")  # must not raise
    token_service.reset_touch_cache()
    assert token_service.verify(db, secret)["id"] == row.id


def test_a_label_is_required(db) -> None:
    """A list of nine rows all reading "(no label)" is not a list."""
    with pytest.raises(ValueError, match="label"):
        _mint(db, label="   ")


def test_a_vmcp_token_in_the_root_env_var_refuses_to_start(monkeypatch) -> None:
    """A real paste error, and a dangerous one.

    `scripts/ui_smoke.py` POSTs `VENONA_AUTH_API_TOKEN` to
    `/api/auth/token-login`, so a `vmcp_` value there would be a path from
    "restricted credential" to "unrestricted session".
    """
    monkeypatch.setenv("VENONA_AUTH_ENABLED", "1")
    monkeypatch.setenv("VENONA_AUTH_SESSION_SECRET", "x" * 48)
    monkeypatch.setenv("VENONA_AUTH_API_TOKEN", security.new_mcp_token())
    security.get_auth_config.cache_clear()
    with pytest.raises(security.AuthNotConfigured, match="agent token"):
        security.assert_usable(security.get_auth_config())
    security.get_auth_config.cache_clear()


def test_the_listing_never_carries_a_secret(db) -> None:
    _row, secret = _mint(db)
    for entry in token_service.listing(db):
        assert secret not in repr(entry)
    assert set(token_service.listing(db)[0].__dataclass_fields__) == {
        "id",
        "label",
        "prefix",
        "created_at",
        "last_used_at",
        "revoked_at",
    }


# ── who may end a connection ─────────────────────────────────────────


def test_ownership_is_what_authorises_a_disconnect(db) -> None:
    """⭐ Token ids are NOT secret, so possession of one is not authority over it.

    They are printed by the CLI and rendered on the Agents page. Once ending a
    connection is reachable over HTTP, the only thing standing between one
    reader and another's assistant is this check -- and a route that trusted the
    id would have looked exactly like one that did not.
    """
    mine = _owner(db, email="mine@example.com")
    theirs = _owner(db, email="theirs@example.com")
    row, _secret = token_service.mint(
        db, label="theirs", token_id="mcp_theirs", now="2026-08-16T00:00:00Z", user_id=theirs.id
    )

    assert token_service.owns(db, row.id, user_id=theirs.id)
    assert not token_service.owns(db, row.id, user_id=mine.id)


def test_a_principal_with_no_user_row_owns_nothing(db) -> None:
    """The root bearer and a `via="disabled"` principal have no user row, so the
    route passes `""`. That must never match a token whose `user_id` is NULL --
    every credential minted before identity existed -- or one empty string would
    hand all of them to any caller without an account."""
    orphan_id = "mcp_orphan"
    db.execute(
        "INSERT INTO mcp_tokens (id, label, token_hash, prefix, created_at, scope) "
        "VALUES (?, 'pre-identity', 'deadbeef', 'vmcp_aaaaaa', '2026-01-01T00:00:00Z', 'mcp')",
        (orphan_id,),
    )
    assert (
        db.execute("SELECT user_id FROM mcp_tokens WHERE id = ?", (orphan_id,)).fetchone()[
            "user_id"
        ]
        is None
    )
    assert not token_service.owns(db, orphan_id, user_id="")


def test_owning_survives_revocation_and_expiry(db) -> None:
    """A revoked or expired token still BELONGS to somebody.

    If ownership were answered from a live listing, disconnecting the same row
    twice would turn into "that is not yours" -- which reads as a permission
    problem rather than as the no-op it is.
    """
    owner = _owner(db)
    row, _secret = _mint(db, owner=owner, token_id="mcp_gone")
    assert token_service.revoke(db, row.id, now="2026-08-17T00:00:00Z")
    assert token_service.owns(db, row.id, user_id=owner.id)
    # And the second revoke is a no-op rather than an error.
    assert not token_service.revoke(db, row.id, now="2026-08-17T00:00:01Z")


def test_exists_tells_a_typo_from_a_repeat(db) -> None:
    """`revoke` returns False for both "already revoked" and "never existed",
    which is right for a caller that only wants the token gone and useless to one
    that must answer 404 for a mistyped id."""
    row, _secret = _mint(db)
    assert token_service.exists(db, row.id)
    assert not token_service.exists(db, "mcp_never")
    token_service.revoke(db, row.id, now="2026-08-17T00:00:00Z")
    assert token_service.exists(db, row.id), "a revoked token is still a row"


def test_a_connection_listing_names_where_it_came_from(db) -> None:
    """`client_name` is what lets the page say "signed in from Claude Code"
    rather than making a reader interpret a `vmcp_` prefix. None means a
    terminal, which is the honest answer for a CLI-minted credential."""
    owner = _owner(db)
    _mint(db, owner=owner, token_id="mcp_cli", label="from a terminal")

    rows = token_service.connections(db, user_id=owner.id)
    assert [r.id for r in rows] == ["mcp_cli"]
    assert rows[0].client_name is None
    assert rows[0].scope == "mcp"


def test_connections_are_scoped_to_one_owner(db) -> None:
    """The same property `GET /api/meta/mcp` has always had, now load-bearing for
    a Disconnect button as well as for a listing."""
    mine = _owner(db, email="mine@example.com")
    theirs = _owner(db, email="theirs@example.com")
    _mint(db, owner=mine, token_id="mcp_mine")
    _mint(db, owner=theirs, token_id="mcp_theirs")

    assert [r.id for r in token_service.connections(db, user_id=mine.id)] == ["mcp_mine"]
    # And no owner is no connections, never every connection.
    assert token_service.connections(db, user_id="") == []
