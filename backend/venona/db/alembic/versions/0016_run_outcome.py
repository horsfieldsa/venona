"""What a run FOUND, as opposed to whether it ran.

Revision ID: 0016_run_outcome
Revises: 0015_quota_samples
"""

from __future__ import annotations

from pathlib import Path

from alembic import op

revision = "0016_run_outcome"
down_revision = "0015_quota_samples"
branch_labels = None
depends_on = None

#: Same arrangement as every revision before it: the `.sql` file is the DDL and
#: carries the reasoning, this file supplies the ordering and the downgrade.
SQL_FILE = Path(__file__).resolve().parents[2] / "migrations" / "0018_run_outcome.sql"


def upgrade() -> None:
    op.execute(SQL_FILE.read_text())


def downgrade() -> None:
    # The backfill is not undone: dropping the column takes it with it, and a
    # re-upgrade recomputes it from `run_stages`, which this never touched.
    op.execute("DROP INDEX IF EXISTS ix_runs_outcome")
    op.execute("ALTER TABLE runs DROP COLUMN IF EXISTS outcome")
