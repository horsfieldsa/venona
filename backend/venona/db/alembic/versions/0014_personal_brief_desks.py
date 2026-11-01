"""The reader's brief gets the shared brief's desks and Sources footer.

Revision ID: 0014_personal_brief_desks
Revises: 0013_suggestion_confidence
"""

from __future__ import annotations

from pathlib import Path

from alembic import op

revision = "0014_personal_brief_desks"
down_revision = "0013_suggestion_confidence"
branch_labels = None
depends_on = None

#: Same arrangement as every revision before it: the `.sql` file is the DDL and
#: carries the reasoning, this file supplies the ordering and the downgrade.
SQL_FILE = Path(__file__).resolve().parents[2] / "migrations" / "0016_personal_brief_desks.sql"


def upgrade() -> None:
    op.execute(SQL_FILE.read_text())


def downgrade() -> None:
    op.execute("ALTER TABLE personal_brief_items DROP COLUMN IF EXISTS section")
    op.execute("ALTER TABLE personal_briefs DROP COLUMN IF EXISTS source_ids")
