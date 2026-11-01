"""Per-run aggregate of the shapes the ontology refused at extraction.

Revision ID: 0005_rejected_shapes
Revises: 0004_domain_hash
"""

from __future__ import annotations

from pathlib import Path

from alembic import op

revision = "0005_rejected_shapes"
down_revision = "0004_domain_hash"
branch_labels = None
depends_on = None

#: Same arrangement as every revision before it: the `.sql` file is the DDL and
#: carries the reasoning, this file supplies the ordering and the downgrade.
SQL_FILE = Path(__file__).resolve().parents[2] / "migrations" / "0007_rejected_shapes.sql"


def upgrade() -> None:
    op.execute(SQL_FILE.read_text())


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS run_rejected_shapes")
