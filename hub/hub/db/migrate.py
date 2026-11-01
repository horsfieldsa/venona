"""A file-ledger migration runner: sorted `*.sql`, applied once, in order.

Deliberately not Alembic. The instance's Alembic revisions each just
`op.execute()` an authored `.sql` file -- the graph and downgrade machinery
version a 20-table schema that four processes migrate concurrently. The hub
has five tables and one process (the compose file forbids a second replica),
so the ledger the instance had before Alembic is the honest size here, and it
keeps sqlalchemy+alembic out of a package whose whole point is a small,
auditable dependency surface.
"""

from __future__ import annotations

from pathlib import Path

from hub.db.core import Conn

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def migrate(conn: Conn) -> list[str]:
    """Apply pending migrations. Idempotent. Returns the names applied."""
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        "  name TEXT PRIMARY KEY,"
        "  applied_at TIMESTAMPTZ NOT NULL DEFAULT now()"
        ")"
    )
    rows = conn.execute("SELECT name FROM schema_migrations").fetchall()
    applied = {row["name"] for row in rows}
    ran: list[str] = []
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        if path.name in applied:
            continue
        # One transaction per file: a failing migration leaves neither its DDL
        # nor its ledger row, so a rerun starts exactly where this one stopped.
        with conn.transaction():
            conn.execute(path.read_text())
            conn.execute("INSERT INTO schema_migrations (name) VALUES (%s)", (path.name,))
        ran.append(path.name)
    return ran
