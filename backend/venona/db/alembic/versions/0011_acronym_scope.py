"""Scope the acronyms stage to what it has not already read.

Revision ID: 0011_acronym_scope
Revises: 0010_hot_indexes
"""

from __future__ import annotations

from pathlib import Path

from alembic import op

revision = "0011_acronym_scope"
down_revision = "0010_hot_indexes"
branch_labels = None
depends_on = None

#: Same arrangement as every revision before it: the `.sql` file is the DDL and
#: carries the reasoning, this file supplies the ordering and the downgrade.
SQL_FILE = Path(__file__).resolve().parents[2] / "migrations" / "0013_acronym_scope.sql"


def upgrade() -> None:
    op.execute(SQL_FILE.read_text())


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS ordinary_word_df")
    op.execute("DROP TABLE IF EXISTS surface_sightings")
    op.execute("ALTER TABLE clusters DROP COLUMN IF EXISTS acronyms_scanned_at")
