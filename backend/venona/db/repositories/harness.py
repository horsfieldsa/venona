"""Every statement about `harness_credentials`, and nothing else.

`make lint-repositories` walks the backend and fails on the first SQL that
grows back outside `db/`. The reason is the read-scope seam: a query assembled
in a router looks exactly like a repository call at the call site, so the
boundary leaks one convenient statement at a time and the leak is invisible in
review.
"""

from __future__ import annotations

from typing import Any

from venona.db import core as db

#: Every column except the secret. The credential is fetched by exactly one
#: function, so a route that selects a row cannot accidentally serve it.
_PUBLIC = (
    "id, harness, billing_mode, account_label, verified_at, updated_by, created_at, updated_at"
)


def all_credentials(conn: db.Conn) -> list[db.Row]:
    """Every stored secret, for the in-process cache the executor reads.

    The one function that returns `secret`, and it is called by the service
    that owns the cache -- never by a router.
    """
    return list(
        conn.execute(
            "SELECT harness, billing_mode, secret FROM harness_credentials WHERE secret IS NOT NULL"
        ).fetchall()
    )


def listing(conn: db.Conn) -> list[db.Row]:
    return list(
        conn.execute(
            f"SELECT {_PUBLIC} FROM harness_credentials ORDER BY harness, billing_mode"
        ).fetchall()
    )


def get(conn: db.Conn, *, harness: str, billing_mode: str) -> db.Row | None:
    return conn.execute(
        f"SELECT {_PUBLIC} FROM harness_credentials WHERE harness = ? AND billing_mode = ?",
        (harness, billing_mode),
    ).fetchone()


def upsert(
    conn: db.Conn,
    *,
    credential_id: str,
    harness: str,
    billing_mode: str,
    secret: str | None,
    account_label: str | None,
    verified_at: str | None,
    updated_by: str | None,
    now: str,
) -> None:
    """One row per service per way of paying for it, replaced rather than edited.

    Replaced because there is no route that carries a secret to an UPDATE: a
    screen that could offer an edit control pre-filled with a credential is a
    screen that can leak one, so connecting again is how a credential changes.
    """
    conn.execute(
        "INSERT INTO harness_credentials "
        "(id, harness, billing_mode, secret, account_label, verified_at, "
        " updated_by, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT (harness, billing_mode) DO UPDATE SET "
        "  secret = EXCLUDED.secret, "
        "  account_label = EXCLUDED.account_label, "
        "  verified_at = EXCLUDED.verified_at, "
        "  updated_by = EXCLUDED.updated_by, "
        "  updated_at = EXCLUDED.updated_at",
        (
            credential_id,
            harness,
            billing_mode,
            secret,
            account_label,
            verified_at,
            updated_by,
            now,
            now,
        ),
    )


def delete(conn: db.Conn, *, harness: str, billing_mode: str) -> int:
    cursor = conn.execute(
        "DELETE FROM harness_credentials WHERE harness = ? AND billing_mode = ?",
        (harness, billing_mode),
    )
    return int(cursor.rowcount or 0)


def stamp_run(conn: db.Conn, run_id: str, *, harness: str, billing_mode: str) -> None:
    """Record the service on the run, at the moment the executor is chosen.

    Not when the row is created: a queued light-lane run is admitted minutes
    later and could be admitted onto a different service than the one that was
    configured when it was submitted. A run cancelled before admission is never
    stamped, which is why the column is nullable.
    """
    conn.execute(
        "UPDATE runs SET harness = ?, billing_mode = ? WHERE id = ?",
        (harness, billing_mode, run_id),
    )


def spend_since(conn: db.Conn, *, run_id: str) -> float:
    """What this run has been billed so far, for the API-mode ceiling.

    Only rows whose cost was MEASURED or COMPUTED count. A subscription's
    shadow price is what the same tokens would have cost at published rates,
    and capping a run against money nobody was charged is the confusion ADR
    0057 removed.
    """
    row = conn.execute(
        "SELECT COALESCE(SUM(cost_usd), 0) AS spent FROM llm_calls "
        "WHERE run_id = ? AND cost_basis IN ('measured', 'computed')",
        (run_id,),
    ).fetchone()
    return float((row or {}).get("spent") or 0.0)


def row_to_public(row: db.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


# ── the CLI's own credential store (ADR 0234) ────────────────────────


def home(conn: db.Conn, *, harness: str) -> db.Row | None:
    """The captured credential files. Returns a secret, so under
    `all_credentials`' rule: never called by a route."""
    return conn.execute(
        "SELECT harness, files, digest, captured_at FROM harness_homes WHERE harness = ?",
        (harness,),
    ).fetchone()


def home_digest(conn: db.Conn, *, harness: str) -> str | None:
    row = conn.execute("SELECT digest FROM harness_homes WHERE harness = ?", (harness,)).fetchone()
    return row["digest"] if row else None


#: The repo's ISO-8601-with-Z convention, rendered by the server.
#:
#: THE CLOCK IS HERE BECAUSE IT CANNOT BE AT THE CALL SITE. Capture runs from
#: `llm/executor.py` and `services/harness_service.py`, both guarded packages
#: where `scripts/lint_determinism.py` bans every way of asking the time. `db/`
#: is not guarded, and this column is bookkeeping rather than anything a replay
#: reproduces.
_CAPTURED_AT = "to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD\"T\"HH24:MI:SS.MS\"Z\"')"


def upsert_home(conn: db.Conn, *, harness: str, files: str, digest: str) -> None:
    conn.execute(
        "INSERT INTO harness_homes (harness, files, digest, captured_at) "
        f"VALUES (?, ?, ?, {_CAPTURED_AT}) "
        "ON CONFLICT (harness) DO UPDATE SET "
        "files = EXCLUDED.files, digest = EXCLUDED.digest, "
        "captured_at = EXCLUDED.captured_at",
        (harness, files, digest),
    )
