"""Authority changes become auditable, and the access queue joins to users.

Revision ID: 0003_authority
Revises: 0002_run_leases
"""

from __future__ import annotations

from pathlib import Path

from alembic import op

revision = "0003_authority"
down_revision = "0002_run_leases"
branch_labels = None
depends_on = None

#: Same arrangement as the two revisions before it: the `.sql` file is the DDL
#: and carries the reasoning, this file supplies the ordering and the downgrade.
#:
#: NOTE THE TWO NUMBERING SCHEMES, because they do not line up and the mismatch
#: looks like a mistake. `db/migrations/` is numbered by authored SQL file and is
#: at 0005; `alembic/versions/` is numbered by REVISION and is at 0003, because
#: `0001_baseline.py` executes three of those files at once. The revision id is
#: what Alembic orders by; the filename below is what it runs.
SQL_FILE = Path(__file__).resolve().parents[2] / "migrations" / "0005_authority.sql"


def upgrade() -> None:
    op.execute(SQL_FILE.read_text())


def downgrade() -> None:
    # The table goes and the columns go. Nothing is preserved and nothing can be:
    # an audit trail's whole value is that it is append-only, so there is no
    # sensible place to put its rows on the way down. A downgrade restores the
    # shape, not the history -- the same honesty `0002_run_leases` states about
    # `owner_start_ticks`.
    op.execute("ALTER TABLE access_requests DROP COLUMN IF EXISTS user_id")
    op.execute("ALTER TABLE access_requests DROP COLUMN IF EXISTS decided_by")
    op.execute("DROP TABLE IF EXISTS user_events")
