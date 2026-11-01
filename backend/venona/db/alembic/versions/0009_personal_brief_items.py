"""The personal brief's items: shared machinery, reader-ordered.

Revision ID: 0009_personal_brief_items
Revises: 0008_relevance
"""

from __future__ import annotations

from pathlib import Path

from alembic import op

revision = "0009_personal_brief_items"
down_revision = "0008_relevance"
branch_labels = None
depends_on = None

#: Same arrangement as every revision before it: the `.sql` file is the DDL and
#: carries the reasoning, this file supplies the ordering and the downgrade.
SQL_FILE = Path(__file__).resolve().parents[2] / "migrations" / "0011_personal_brief_items.sql"


def upgrade() -> None:
    op.execute(SQL_FILE.read_text())


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS personal_brief_items")
