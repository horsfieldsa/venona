"""Who may change what someone else may do. Contract C12's authorization addendum.

WHY THIS IS ITS OWN FILE. `venona users` was CLI-only, and the argument was that
an HTTP endpoint granting a role is reachable by anything holding a session. This
phase puts role and status changes on a screen, narrowed to "authority may be
reduced without limit and extended only as far as `reader`" -- and the rules that
make that safe are not in the router. They are in
`user_service.change_authority`, because `venona users` reaches them from another
process and a check in a route is a check the CLI does not run.

THE ONE WORTH READING is `test_two_admins_demoting_each_other_cannot_empty_the_instance`.
Everything else here is a refusal that a reviewer would think to ask for; that one
is the failure that passes review, passes every single-threaded test, and leaves
an instance with no administrator.
"""

from __future__ import annotations

import threading

import pytest
from venona.api import authz
from venona.db import core as db
from venona.db.repositories import users as repo
from venona.services import user_service

NOW = "2026-08-17T00:00:00Z"
EPOCH = 1_800_000_000.0


def _add(conn: db.Conn, ident: str, email: str, role: str) -> str:
    # `allow_admin` because these are ARRANGE steps standing in for shell access.
    # What this file tests is the four refusals below; the fifth (no admin over a
    # wire, ADR 0168) has its own tests and would otherwise make it impossible to
    # set up the admins the other refusals are about.
    user_service.create(conn, user_id=ident, email=email, role=role, now=NOW, allow_admin=True)
    return ident


def _change(conn: db.Conn, *, user_id: str, actor: str | None = None, event: str = "e1", **kw):
    """One authority change in its own transaction, as every caller makes it."""
    with db.transaction(conn, what="test_change"):
        return user_service.change_authority(
            conn,
            user_id=user_id,
            actor=actor,
            event_id=f"uevent:{event}",
            now=NOW,
            epoch=EPOCH,
            **kw,
        )


@pytest.fixture
def two_admins(conn: db.Conn) -> db.Conn:
    _add(conn, "user:a", "a@example.com", authz.ADMIN)
    _add(conn, "user:b", "b@example.com", authz.ADMIN)
    return conn


# ── the last administrator ───────────────────────────────────────────


def test_the_last_admin_cannot_be_demoted(conn: db.Conn) -> None:
    """`actor=None` -- the CLI -- because a sole admin demoting THEMSELVES is
    refused one check earlier, by the self-target rule. Testing it that way would
    pass for the wrong reason and say nothing about this rule."""
    _add(conn, "user:solo", "solo@example.com", authz.ADMIN)
    _add(conn, "user:r", "r@example.com", authz.READER)

    with pytest.raises(user_service.AuthorityError, match="only active administrator"):
        _change(conn, user_id="user:solo", role=authz.READER, actor=None)

    assert user_service.get(conn, "user:solo").is_admin


def test_a_sole_admin_demoting_themselves_is_refused_by_the_self_rule_first(
    conn: db.Conn,
) -> None:
    """Both rules would refuse it; the ordering decides which sentence they see,
    and "you cannot change your own role" is the more useful one -- it names an
    action they can take (ask someone else) rather than a fact about the
    instance."""
    _add(conn, "user:solo", "solo@example.com", authz.ADMIN)

    with pytest.raises(user_service.AuthorityError, match="your own role or status"):
        _change(conn, user_id="user:solo", role=authz.READER, actor="user:solo")


def test_the_last_admin_cannot_be_suspended(conn: db.Conn) -> None:
    """Suspension empties the instance exactly as thoroughly as demotion, and it
    is the one people forget: the role column still says `admin`."""
    _add(conn, "user:solo", "solo@example.com", authz.ADMIN)

    with pytest.raises(user_service.AuthorityError, match="only active administrator"):
        _change(conn, user_id="user:solo", status=user_service.SUSPENDED, actor=None)

    assert user_service.get(conn, "user:solo").active


def test_a_suspended_admin_does_not_count_towards_the_last_one(conn: db.Conn) -> None:
    """Two admin ROWS, one of them suspended, is one administrator. Counting rows
    by role alone would let the live one be demoted and leave nobody who can
    sign in."""
    _add(conn, "user:live", "live@example.com", authz.ADMIN)
    _add(conn, "user:dead", "dead@example.com", authz.ADMIN)
    _change(conn, user_id="user:dead", status=user_service.SUSPENDED, actor="user:live")

    with pytest.raises(user_service.AuthorityError, match="only active administrator"):
        _change(conn, user_id="user:live", role=authz.READER, actor=None, event="e2")


def test_one_of_two_admins_may_be_demoted(two_admins: db.Conn) -> None:
    """The other half. A rule that refused everyone would pass every test above."""
    changed = _change(two_admins, user_id="user:b", role=authz.READER, actor="user:a")
    assert changed.role == authz.READER


def test_force_is_how_a_shell_suspends_a_compromised_sole_admin(conn: db.Conn) -> None:
    """`venona users` passes it and no route can. Without it the answer to "my
    admin account is compromised" would be "add a second admin first", on the one
    path that has to work under pressure."""
    _add(conn, "user:solo", "solo@example.com", authz.ADMIN)
    changed = _change(
        conn, user_id="user:solo", status=user_service.SUSPENDED, actor=None, force=True
    )
    assert not changed.active
    assert repo.active_admin_count(conn) == 0


@pytest.mark.negative
def test_two_admins_demoting_each_other_cannot_empty_the_instance(
    two_admins: db.Conn, database_url: str
) -> None:
    """⭐ THE ONE THE LOCK EXISTS FOR, and the one no single-threaded test sees.

    At READ COMMITTED each transaction reads the OTHER admin as still active --
    neither sees the other's uncommitted write -- so both pass a
    `WHERE EXISTS (another admin)` guard, both commit, and the instance has no
    administrator. `db/core.py::transaction` says exactly this: how a transaction
    BEGINS cannot fix a lost update, so it is fixed at the site.

    Both connections are real and both run at once. The barrier makes them
    overlap rather than hoping they do -- without it the two would usually
    serialise by luck and this would pass against a broken implementation.
    """
    ready = threading.Barrier(2)
    outcomes: dict[str, object] = {}

    def demote(name: str, target: str, actor: str) -> None:
        own = db.connect(database_url)
        try:
            ready.wait(timeout=10)
            with db.transaction(own, what=f"demote_{name}"):
                user_service.change_authority(
                    own,
                    user_id=target,
                    role=authz.READER,
                    actor=actor,
                    event_id=f"uevent:{name}",
                    now=NOW,
                    epoch=EPOCH,
                )
            outcomes[name] = "demoted"
        except user_service.AuthorityError as exc:
            outcomes[name] = exc
        except Exception as exc:  # noqa: BLE001 - reported, not swallowed
            outcomes[name] = exc
        finally:
            own.close()

    threads = [
        threading.Thread(target=demote, args=("a", "user:a", "user:b")),
        threading.Thread(target=demote, args=("b", "user:b", "user:a")),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert len(outcomes) == 2, f"a thread did not finish: {outcomes}"
    demoted = [name for name, result in outcomes.items() if result == "demoted"]
    refused = [r for r in outcomes.values() if isinstance(r, user_service.AuthorityError)]

    assert len(demoted) == 1, f"both demotions were allowed: {outcomes}"
    assert len(refused) == 1, f"the loser was not refused with an AuthorityError: {outcomes}"
    assert repo.active_admin_count(two_admins) == 1, (
        "the instance was left with no active administrator, which is the whole "
        "failure this lock prevents"
    )


# ── nobody edits their own authority ─────────────────────────────────


@pytest.mark.negative
@pytest.mark.parametrize(
    ("kwargs",),
    [({"role": authz.READER},), ({"status": user_service.SUSPENDED},)],
)
def test_you_cannot_change_your_own_role_or_status(two_admins: db.Conn, kwargs) -> None:
    with pytest.raises(user_service.AuthorityError, match="your own role or status"):
        _change(two_admins, user_id="user:a", actor="user:a", **kwargs)


def test_the_cli_may_change_anyone_because_it_has_no_actor(two_admins: db.Conn) -> None:
    """`actor=None` is shell access, which is a higher tier than any row -- it is
    the credential the whole CLI-only argument rests on."""
    assert _change(two_admins, user_id="user:a", role=authz.READER, actor=None).role == (
        authz.READER
    )


@pytest.mark.negative
def test_an_actor_suspended_a_moment_ago_is_refused(two_admins: db.Conn) -> None:
    """`AuthMiddleware` resolves the principal BEFORE routing and this commits
    after, so the actor's row is re-read inside the lock. Without that, an admin
    suspended between the two completes their own authority change."""
    _change(two_admins, user_id="user:a", status=user_service.SUSPENDED, actor=None, event="s1")

    with pytest.raises(user_service.AuthorityError, match="no longer an active administrator"):
        _change(two_admins, user_id="user:b", role=authz.READER, actor="user:a", event="s2")


@pytest.mark.negative
def test_a_reader_actor_is_refused_even_if_a_router_let_them_through(
    two_admins: db.Conn,
) -> None:
    """Defence in depth. `api/authz.py` refuses a reader pre-routing, but this is
    the layer `venona users` and any future caller also pass through."""
    _add(two_admins, "user:r", "r@example.com", authz.READER)
    with pytest.raises(user_service.AuthorityError, match="no longer an active administrator"):
        _change(two_admins, user_id="user:b", role=authz.READER, actor="user:r")


# ── the audit trail ──────────────────────────────────────────────────


def test_a_change_and_its_event_are_one_transaction(two_admins: db.Conn) -> None:
    _change(two_admins, user_id="user:b", role=authz.READER, actor="user:a")
    events = user_service.events(two_admins, limit=10)

    assert len(events) == 1
    assert events[0]["action"] == user_service.ROLE_CHANGED
    assert events[0]["user_email"] == "b@example.com"
    assert events[0]["actor_email"] == "a@example.com"
    # The BEFORE as well as the after: "role_changed" alone answers the least
    # interesting half of the question.
    assert "admin -> reader" in events[0]["detail"]


def test_a_refused_change_records_nothing(two_admins: db.Conn) -> None:
    """The event is written inside the same transaction as the change, so a
    refusal must leave no trace of a change that did not happen."""
    with pytest.raises(user_service.AuthorityError):
        _change(two_admins, user_id="user:a", actor="user:a", role=authz.READER)

    assert user_service.events(two_admins, limit=10) == []


def test_a_cli_change_signs_itself_null(two_admins: db.Conn) -> None:
    """NULL is honest and is the convention every other actor column carries
    (C1): it means the CLI decided, or a process did. Never backfilled."""
    _change(two_admins, user_id="user:b", status=user_service.SUSPENDED, actor=None)
    event = user_service.events(two_admins, limit=1)[0]

    assert event["actor_email"] is None
    assert event["action"] == user_service.SUSPENDED_EVENT


def test_suspending_and_reinstating_are_different_actions(two_admins: db.Conn) -> None:
    """A log that called both "status_changed" would make "when did I turn this
    off" a question you answer by reading the detail column."""
    _change(two_admins, user_id="user:b", status=user_service.SUSPENDED, actor="user:a", event="1")
    _change(two_admins, user_id="user:b", status=user_service.ACTIVE, actor="user:a", event="2")

    actions = [e["action"] for e in user_service.events(two_admins, limit=10)]
    assert actions == [user_service.REINSTATED_EVENT, user_service.SUSPENDED_EVENT]


# ── suspension is not deletion ───────────────────────────────────────


@pytest.mark.negative
def test_deleting_a_user_who_signed_a_verdict_is_refused(conn: db.Conn) -> None:
    """⭐ WHY THERE IS NO DELETE, over HTTP or CLI.

    The five actor columns are bare `REFERENCES users(id)`, so the default
    `NO ACTION` blocks the delete for exactly the users who have decided
    something -- and permits it for those who never used the system. A delete
    feature would therefore pass its own tests and fail on every real case, which
    is the worst shape a feature can have. Suspension is the supported removal
    and it keeps the row that answers "when did I turn this off".
    """
    import psycopg

    _add(conn, "user:signer", "signer@example.com", authz.ADMIN)
    conn.execute(
        "INSERT INTO alias_conflicts "
        "(id, surface, normalized, existing_entity_id, proposed_entity_id, entity_type, "
        " resolved, created_at, resolved_by) "
        "VALUES ('c1','X','x','org:a','org:b','Organization',1,?,?)",
        (NOW, "user:signer"),
    )
    conn.commit()

    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        conn.execute("DELETE FROM users WHERE id = ?", ("user:signer",))
    conn.rollback()

    assert user_service.get(conn, "user:signer") is not None


def test_suspension_still_bumps_the_session_epoch(two_admins: db.Conn) -> None:
    """Both halves are belt and braces, and the epoch is what reaches a
    fourteen-day cookie that has already been issued."""
    before = user_service.get(two_admins, "user:b").session_epoch
    _change(two_admins, user_id="user:b", status=user_service.SUSPENDED, actor="user:a")
    assert user_service.get(two_admins, "user:b").session_epoch > before


# ── refusals that are caller mistakes, not policy ────────────────────


def test_an_unknown_role_is_refused_rather_than_stored(two_admins: db.Conn) -> None:
    """`_to_record` degrades an unrecognised role to reader on the way out, so a
    role that means one thing written and another read is the kind of thing
    nobody finds until it matters."""
    with pytest.raises(ValueError, match="unknown role"):
        _change(two_admins, user_id="user:b", role="superuser", actor="user:a")


def test_changing_nothing_is_a_caller_mistake(two_admins: db.Conn) -> None:
    with pytest.raises(ValueError, match="change nothing"):
        _change(two_admins, user_id="user:b", actor="user:a")


# ── the admin ceiling (ADR 0168) ─────────────────────────────────────


def test_nothing_over_a_wire_may_create_an_administrator(conn: db.Conn) -> None:
    """⭐ C12's ceiling, enforced where the CLI cannot reach around it.

    It used to live in `api/schemas/admin.py` as a `Literal["reader"]` on ONE
    request model, which is the arrangement `change_authority`'s own docstring
    rejects: a check in a route is a check the other callers do not run. It
    guarded the admin router while the hub's login sync and `provision-user`
    minted administrators freely.
    """
    with pytest.raises(user_service.AuthorityError, match="shell access"):
        user_service.create(
            conn, user_id="user:x", email="x@example.com", role=authz.ADMIN, now=NOW
        )


def test_nothing_over_a_wire_may_raise_authority_to_administrator(conn: db.Conn) -> None:
    _add(conn, "user:signer2", "signer2@example.com", authz.ADMIN)
    _add(conn, "user:target", "target@example.com", authz.READER)

    with pytest.raises(user_service.AuthorityError, match="shell access"):
        _change(conn, user_id="user:target", actor="user:signer2", role=authz.ADMIN)

    assert user_service.get(conn, "user:target").role == authz.READER


def test_the_ceiling_is_one_directional(conn: db.Conn) -> None:
    """C12's wording exactly: authority may be REDUCED without limit and extended
    only as far as `reader`. A directory that cannot demote is worse than no
    directory (ADR 0141), and that half survives 0168 untouched."""
    _add(conn, "user:a2", "a2@example.com", authz.ADMIN)
    _add(conn, "user:b2", "b2@example.com", authz.ADMIN)

    changed = _change(conn, user_id="user:b2", actor="user:a2", role=authz.READER)
    assert changed.role == authz.READER


def test_a_reader_may_still_be_granted_over_a_wire(conn: db.Conn) -> None:
    """The ceiling is a ceiling, not a wall. Granting a reader is not minting a
    credential -- the identity is the provider's, and the address still has to be
    one its owner can authenticate as."""
    _add(conn, "user:a3", "a3@example.com", authz.ADMIN)
    user_service.create(
        conn, user_id="user:new", email="new@example.com", role=authz.READER, now=NOW
    )
    assert user_service.get(conn, "user:new").role == authz.READER


def test_the_cli_keeps_shell_authority(conn: db.Conn) -> None:
    """`allow_admin=True` is what `venona users` passes, and it is the whole of
    C12's "crossing into admin requires shell access". If this stops working the
    ceiling has become a wall and no instance can ever gain a second admin."""
    user_service.create(
        conn,
        user_id="user:shell",
        email="shell@example.com",
        role=authz.ADMIN,
        now=NOW,
        allow_admin=True,
    )
    assert user_service.get(conn, "user:shell").is_admin

    _add(conn, "user:other", "other@example.com", authz.ADMIN)
    down = _change(conn, user_id="user:shell", actor=None, role=authz.READER, event="down")
    assert down.role == authz.READER
    back = _change(
        conn, user_id="user:shell", actor=None, role=authz.ADMIN, allow_admin=True, event="up"
    )
    assert back.is_admin, "a shell must be able to put an admin back, or the ceiling is a wall"
