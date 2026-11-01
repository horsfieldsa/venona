"""A model service's own credential store, as a row (ADR 0234).

Revision ID: 0026_harness_homes
Revises: 0025_config_documents
"""

from __future__ import annotations

from pathlib import Path

from alembic import op

revision = "0026_harness_homes"
down_revision = "0025_config_documents"
branch_labels = None
depends_on = None

#: Same arrangement as every revision before it: the `.sql` file is the DDL and
#: carries the reasoning, this file supplies the ordering and the downgrade.
SQL_FILE = Path(__file__).resolve().parents[2] / "migrations" / "0028_harness_homes.sql"


def upgrade() -> None:
    op.execute(SQL_FILE.read_text())


def downgrade() -> None:
    # Dropping this costs a sign-in per service on the next restart, which is
    # what a lost disk cost before it existed.
    op.execute("DROP TABLE IF EXISTS harness_homes")
