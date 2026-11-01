"""How sure the adjudicator was.

Revision ID: 0013_suggestion_confidence
Revises: 0012_config_events
"""

from __future__ import annotations

from pathlib import Path

from alembic import op

revision = "0013_suggestion_confidence"
down_revision = "0012_config_events"
branch_labels = None
depends_on = None

#: Same arrangement as every revision before it: the `.sql` file is the DDL and
#: carries the reasoning, this file supplies the ordering and the downgrade.
SQL_FILE = Path(__file__).resolve().parents[2] / "migrations" / "0015_suggestion_confidence.sql"


def upgrade() -> None:
    op.execute(SQL_FILE.read_text())


def downgrade() -> None:
    op.execute("ALTER TABLE alias_conflicts DROP COLUMN IF EXISTS suggestion_confidence")
