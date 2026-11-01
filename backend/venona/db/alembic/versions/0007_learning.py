"""Learning: FSRS state, review log, question bank, settings (epic O).

Revision ID: 0007_learning
Revises: 0006_user_picture
"""

from __future__ import annotations

from pathlib import Path

from alembic import op

revision = "0007_learning"
down_revision = "0006_user_picture"
branch_labels = None
depends_on = None

#: Same arrangement as every revision before it: the `.sql` file is the DDL and
#: carries the reasoning, this file supplies the ordering and the downgrade.
SQL_FILE = Path(__file__).resolve().parents[2] / "migrations" / "0009_learning.sql"


def upgrade() -> None:
    op.execute(SQL_FILE.read_text())


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS learning_settings")
    op.execute("DROP TABLE IF EXISTS llm_questions")
    op.execute("DROP TABLE IF EXISTS learning_reviews")
    op.execute("DROP TABLE IF EXISTS learning_cards")
