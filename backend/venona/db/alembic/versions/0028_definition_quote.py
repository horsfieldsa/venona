"""The sentence that DEFINES an entity, beside the one that mentions it.

Revision ID: 0028_definition_quote
Revises: 0027_auth_state
"""

from __future__ import annotations

from pathlib import Path

from alembic import op

revision = "0028_definition_quote"
down_revision = "0027_auth_state"
branch_labels = None
depends_on = None

#: Same arrangement as every revision before it: the `.sql` file is the DDL and
#: carries the reasoning, this file supplies the ordering and the downgrade.
SQL_FILE = Path(__file__).resolve().parents[2] / "migrations" / "0030_definition_quote.sql"


def upgrade() -> None:
    op.execute(SQL_FILE.read_text())


def downgrade() -> None:
    # Dropping the column throws away every re-read the backfill paid for, and
    # `entity_definitions` keeps the definitions themselves, so a downgrade
    # costs the corpus its resumability rather than its answers.
    op.execute("DROP INDEX IF EXISTS ix_assertions_definition_quote")
    op.execute("ALTER TABLE assertions DROP COLUMN IF EXISTS definition_quote")
