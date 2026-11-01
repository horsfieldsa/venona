"""Every statement that oauth_service runs.

Serves `services/oauth_service.py`. The module keeps its
decisions; this holds the reads and writes they are made of. See
`db/repositories/__init__.py` for the rules.
"""

from __future__ import annotations

from venona.db import core as db


def update_oauth_clients_by_last_used_at_client_id(conn: db.Conn, p0, p1):
    return conn.execute("UPDATE oauth_clients SET last_used_at = ? WHERE client_id = ?", (p0, p1))


def delete_oauth_codes(conn: db.Conn, p0):
    return conn.execute(
        "DELETE FROM oauth_codes WHERE expires_at <= ? AND redeemed_at IS NULL", (p0,)
    )


def delete_oauth_clients_never_used(conn: db.Conn, p0):
    """Registrations that were never carried through to an authorization.

    `last_used_at` is stamped only when a code is redeemed, so NULL means the
    row is a registration and nothing more -- and `/register` is unauthenticated
    by RFC 7591, which makes that the row anyone on the internet can create as
    many of as they like. A paired extension has a redemption behind it and is
    therefore never NULL, which is what keeps this from deleting live pairings
    (contract C12: those rows accumulate by design).
    """
    return conn.execute(
        "DELETE FROM oauth_clients WHERE last_used_at IS NULL AND created_at < ?", (p0,)
    )


def update_oauth_codes_by_redeemed_at_code_hash(conn: db.Conn, p0, p1):
    return conn.execute("UPDATE oauth_codes SET redeemed_at = ? WHERE code_hash = ?", (p0, p1))


def select_oauth_clients_by_client_id(conn: db.Conn, p0):
    return conn.execute(
        "SELECT client_id, client_name, redirect_uris FROM oauth_clients WHERE client_id = ?", (p0,)
    )


def select_oauth_codes_by_code_hash(conn: db.Conn, p0):
    return conn.execute("SELECT * FROM oauth_codes WHERE code_hash = ? FOR UPDATE", (p0,))
