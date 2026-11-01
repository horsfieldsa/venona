"""The instance-wide session kill switch, as a row (ADR 0234).

Revision ID: 0027_auth_state
Revises: 0026_harness_homes
"""

from __future__ import annotations

from pathlib import Path

from alembic import op

revision = "0027_auth_state"
down_revision = "0026_harness_homes"
branch_labels = None
depends_on = None

#: Same arrangement as every revision before it: the `.sql` file is the DDL and
#: carries the reasoning, this file supplies the ordering and the downgrade.
SQL_FILE = Path(__file__).resolve().parents[2] / "migrations" / "0029_auth_state.sql"


def upgrade() -> None:
    op.execute(SQL_FILE.read_text())


def downgrade() -> None:
    # Dropping this un-revokes whatever the last break-glass logout revoked,
    # for the remainder of those cookies' lives. Per-user revocation on `users`
    # is untouched.
    op.execute("DROP TABLE IF EXISTS auth_state")
