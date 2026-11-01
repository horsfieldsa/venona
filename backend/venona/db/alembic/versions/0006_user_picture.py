"""The account's avatar, from the OIDC `picture` claim.

Revision ID: 0006_user_picture
Revises: 0005_rejected_shapes
"""

from __future__ import annotations

from pathlib import Path

from alembic import op

revision = "0006_user_picture"
down_revision = "0005_rejected_shapes"
branch_labels = None
depends_on = None

#: Same arrangement as every revision before it: the `.sql` file is the DDL and
#: carries the reasoning, this file supplies the ordering and the downgrade.
SQL_FILE = Path(__file__).resolve().parents[2] / "migrations" / "0008_user_picture.sql"


def upgrade() -> None:
    op.execute(SQL_FILE.read_text())


def downgrade() -> None:
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS picture_url")
