"""Runs carry the domain descriptor's fingerprint.

Revision ID: 0004_domain_hash
Revises: 0003_authority
"""

from __future__ import annotations

from pathlib import Path

from alembic import op

revision = "0004_domain_hash"
down_revision = "0003_authority"
branch_labels = None
depends_on = None

#: Same arrangement as every revision before it: the `.sql` file is the DDL and
#: carries the reasoning, this file supplies the ordering and the downgrade.
SQL_FILE = Path(__file__).resolve().parents[2] / "migrations" / "0006_domain_hash.sql"


def upgrade() -> None:
    op.execute(SQL_FILE.read_text())


def downgrade() -> None:
    op.execute("ALTER TABLE runs DROP COLUMN IF EXISTS domain_hash")
