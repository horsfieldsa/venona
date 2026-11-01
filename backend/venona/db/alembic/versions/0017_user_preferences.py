"""How a reader wants things shown to them.

Revision ID: 0017_user_preferences
Revises: 0016_run_outcome
"""

from __future__ import annotations

from pathlib import Path

from alembic import op

revision = "0017_user_preferences"
down_revision = "0016_run_outcome"
branch_labels = None
depends_on = None

#: Same arrangement as every revision before it: the `.sql` file is the DDL and
#: carries the reasoning, this file supplies the ordering and the downgrade.
SQL_FILE = Path(__file__).resolve().parents[2] / "migrations" / "0019_user_preferences.sql"


def upgrade() -> None:
    op.execute(SQL_FILE.read_text())


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS user_preferences")
