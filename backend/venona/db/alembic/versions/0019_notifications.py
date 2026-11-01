"""Where a notice goes, who asked for it, and whether it arrived.

Revision ID: 0019_notifications
Revises: 0018_alias_hygiene
"""

from __future__ import annotations

from pathlib import Path

from alembic import op

revision = "0019_notifications"
down_revision = "0018_alias_hygiene"
branch_labels = None
depends_on = None

#: Same arrangement as every revision before it: the `.sql` file is the DDL and
#: carries the reasoning, this file supplies the ordering and the downgrade.
SQL_FILE = Path(__file__).resolve().parents[2] / "migrations" / "0021_notifications.sql"


def upgrade() -> None:
    op.execute(SQL_FILE.read_text())


def downgrade() -> None:
    # Children first: both carry a CASCADE onto `notify_destinations`, and
    # dropping the parent while they exist is an error rather than a cascade.
    op.execute("DROP TABLE IF EXISTS notify_outbox")
    op.execute("DROP TABLE IF EXISTS notify_subscriptions")
    op.execute("DROP TABLE IF EXISTS notify_destinations")
