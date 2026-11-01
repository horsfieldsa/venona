"""What this reader has paid for, projected from the hub.

Revision ID: 0021_entitlement
Revises: 0020_ambiguous_readings
"""

from __future__ import annotations

from pathlib import Path

from alembic import op

revision = "0021_entitlement"
down_revision = "0020_ambiguous_readings"
branch_labels = None
depends_on = None

#: Same arrangement as every revision before it: the `.sql` file is the DDL and
#: carries the reasoning, this file supplies the ordering and the downgrade.
#: The numbering diverges (revision 0021 executes migration 0023) and has since
#: alembic was adopted after the first two SQL files.
SQL_FILE = Path(__file__).resolve().parents[2] / "migrations" / "0023_entitlement.sql"


def upgrade() -> None:
    op.execute(SQL_FILE.read_text())


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_users_entitlement")
    for column in (
        "billing_notice_at",
        "billing_notice",
        "entitled_through",
        "entitlement_status",
    ):
        op.execute(f"ALTER TABLE users DROP COLUMN IF EXISTS {column}")
