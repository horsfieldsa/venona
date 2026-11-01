"""What the subscription had left, sampled from calls we already make.

Revision ID: 0015_quota_samples
Revises: 0014_personal_brief_desks
"""

from __future__ import annotations

from pathlib import Path

from alembic import op

revision = "0015_quota_samples"
down_revision = "0014_personal_brief_desks"
branch_labels = None
depends_on = None

#: Same arrangement as every revision before it: the `.sql` file is the DDL and
#: carries the reasoning, this file supplies the ordering and the downgrade.
SQL_FILE = Path(__file__).resolve().parents[2] / "migrations" / "0017_quota_samples.sql"


def upgrade() -> None:
    op.execute(SQL_FILE.read_text())


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS quota_samples")
