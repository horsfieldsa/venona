"""The folders venona has written out, so one can be fetched again.

Revision ID: 0024_data_exports
Revises: 0023_harness
"""

from __future__ import annotations

from pathlib import Path

from alembic import op

revision = "0024_data_exports"
down_revision = "0023_harness"
branch_labels = None
depends_on = None

#: Same arrangement as every revision before it: the `.sql` file is the DDL and
#: carries the reasoning, this file supplies the ordering and the downgrade.
SQL_FILE = Path(__file__).resolve().parents[2] / "migrations" / "0026_data_exports.sql"


def upgrade() -> None:
    op.execute(SQL_FILE.read_text())


def downgrade() -> None:
    # The files themselves are left on disk. A schema downgrade is a statement
    # about the database, and deleting an operator's archives because a
    # migration was rolled back is not something they asked for.
    op.execute("DROP TABLE IF EXISTS data_exports")
