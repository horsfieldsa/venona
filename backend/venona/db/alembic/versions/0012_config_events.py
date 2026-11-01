"""Who changed what the model is told.

Revision ID: 0012_config_events
Revises: 0011_acronym_scope
"""

from __future__ import annotations

from pathlib import Path

from alembic import op

revision = "0012_config_events"
down_revision = "0011_acronym_scope"
branch_labels = None
depends_on = None

#: Same arrangement as every revision before it: the `.sql` file is the DDL and
#: carries the reasoning, this file supplies the ordering and the downgrade.
SQL_FILE = Path(__file__).resolve().parents[2] / "migrations" / "0014_config_events.sql"


def upgrade() -> None:
    op.execute(SQL_FILE.read_text())


def downgrade() -> None:
    # The indexes go with the table; naming them separately would fail on a
    # partial upgrade, which is the state a downgrade is most likely run from.
    op.execute("DROP TABLE IF EXISTS config_events")
