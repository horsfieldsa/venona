"""Indexes on the three tables that only ever grow.

Revision ID: 0010_hot_indexes
Revises: 0009_personal_brief_items
"""

from __future__ import annotations

from pathlib import Path

from alembic import op

revision = "0010_hot_indexes"
down_revision = "0009_personal_brief_items"
branch_labels = None
depends_on = None

#: Same arrangement as every revision before it: the `.sql` file is the DDL and
#: carries the reasoning, this file supplies the ordering and the downgrade.
#: The numbering differs because the SQL files started one revision earlier --
#: alembic 0010 is SQL 0012.
SQL_FILE = Path(__file__).resolve().parents[2] / "migrations" / "0012_hot_indexes.sql"


def upgrade() -> None:
    op.execute(SQL_FILE.read_text())


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_runs_active")
    op.execute("DROP INDEX IF EXISTS ix_assertions_active_relations")
    op.execute("DROP INDEX IF EXISTS ix_llm_calls_created_at")
