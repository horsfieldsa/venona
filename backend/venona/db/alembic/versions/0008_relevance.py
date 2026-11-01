"""Personal relevance: emphasis, follows, and the reader's own brief.

Revision ID: 0008_relevance
Revises: 0007_learning
"""

from __future__ import annotations

from pathlib import Path

from alembic import op

revision = "0008_relevance"
down_revision = "0007_learning"
branch_labels = None
depends_on = None

#: Same arrangement as every revision before it: the `.sql` file is the DDL and
#: carries the reasoning, this file supplies the ordering and the downgrade.
SQL_FILE = Path(__file__).resolve().parents[2] / "migrations" / "0010_relevance.sql"


def upgrade() -> None:
    op.execute(SQL_FILE.read_text())


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS personal_brief_blocks")
    op.execute("DROP TABLE IF EXISTS personal_briefs")
    op.execute("DROP TABLE IF EXISTS entity_follows")
    op.execute("DROP TABLE IF EXISTS relevance_settings")
