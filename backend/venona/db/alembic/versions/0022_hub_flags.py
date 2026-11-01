"""Switches the hub throws on this instance's behalf.

Revision ID: 0022_hub_flags
Revises: 0021_entitlement
"""

from __future__ import annotations

from pathlib import Path

from alembic import op

revision = "0022_hub_flags"
down_revision = "0021_entitlement"
branch_labels = None
depends_on = None

#: Same arrangement as every revision before it: the `.sql` file is the DDL and
#: carries the reasoning, this file supplies the ordering and the downgrade.
SQL_FILE = Path(__file__).resolve().parents[2] / "migrations" / "0024_hub_flags.sql"


def upgrade() -> None:
    op.execute(SQL_FILE.read_text())


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS hub_flags")
