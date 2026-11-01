"""Which entities an ambiguous surface could mean.

Revision ID: 0020_ambiguous_readings
Revises: 0019_notifications
"""

from __future__ import annotations

from pathlib import Path

from alembic import op

revision = "0020_ambiguous_readings"
down_revision = "0019_notifications"
branch_labels = None
depends_on = None

#: Same arrangement as every revision before it: the `.sql` file is the DDL and
#: carries the reasoning, this file supplies the ordering and the downgrade.
SQL_FILE = Path(__file__).resolve().parents[2] / "migrations" / "0022_ambiguous_readings.sql"


def upgrade() -> None:
    op.execute(SQL_FILE.read_text())


def downgrade() -> None:
    op.execute("ALTER TABLE ambiguous_surfaces DROP COLUMN IF EXISTS readings")
