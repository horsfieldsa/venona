"""Run liveness moves from `/proc` to a Postgres advisory lock. Contract C11.

Revision ID: 0002_run_leases
Revises: 0001_baseline
"""

from __future__ import annotations

from pathlib import Path

from alembic import op

revision = "0002_run_leases"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None

#: Same arrangement as the baseline: the `.sql` file is the DDL and carries the
#: reasoning, this file supplies the ordering and the downgrade. See
#: `0001_baseline.py` on why that split rather than `op.drop_column` here.
SQL_FILE = Path(__file__).resolve().parents[2] / "migrations" / "0004_run_leases.sql"


def upgrade() -> None:
    op.execute(SQL_FILE.read_text())


def downgrade() -> None:
    # The column comes back NULLABLE and EMPTY, which is honest: the start ticks
    # it used to hold were read from `/proc` at run-creation time and cannot be
    # recovered afterwards for a process that has since exited. A downgrade
    # restores the shape, not the data -- and the code that read it is gone, so
    # nothing would consult it anyway.
    op.execute("ALTER TABLE runs ADD COLUMN IF NOT EXISTS owner_start_ticks INTEGER")
