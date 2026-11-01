"""Which model service did this, and what paid for it.

Revision ID: 0023_harness
Revises: 0022_hub_flags
"""

from __future__ import annotations

from pathlib import Path

from alembic import op

revision = "0023_harness"
down_revision = "0022_hub_flags"
branch_labels = None
depends_on = None

#: Same arrangement as every revision before it: the `.sql` file is the DDL and
#: carries the reasoning, this file supplies the ordering and the downgrade.
SQL_FILE = Path(__file__).resolve().parents[2] / "migrations" / "0025_harness.sql"


def upgrade() -> None:
    op.execute(SQL_FILE.read_text())


def downgrade() -> None:
    # The quota index is restored to its previous shape rather than dropped:
    # leaving the table without it would let the same reading be recorded twice.
    op.execute("DROP INDEX IF EXISTS ux_quota_samples_reading")
    op.execute("ALTER TABLE quota_samples DROP COLUMN IF EXISTS harness")
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_quota_samples_reading "
        "ON quota_samples (window_name, resets_at, utilization)"
    )
    op.execute("DROP INDEX IF EXISTS ix_runs_harness")
    op.execute("ALTER TABLE runs DROP COLUMN IF EXISTS billing_mode")
    op.execute("ALTER TABLE runs DROP COLUMN IF EXISTS harness")
    op.execute("ALTER TABLE llm_calls DROP COLUMN IF EXISTS cost_basis")
    op.execute("ALTER TABLE llm_calls DROP COLUMN IF EXISTS billing_mode")
    op.execute("ALTER TABLE llm_calls DROP COLUMN IF EXISTS harness")
    op.execute("DROP TABLE IF EXISTS harness_credentials")
