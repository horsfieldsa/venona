"""Aliases nobody wrote, and one a person withdrew.

Revision ID: 0018_alias_hygiene
Revises: 0017_user_preferences
"""

from __future__ import annotations

from pathlib import Path

from alembic import op

revision = "0018_alias_hygiene"
down_revision = "0017_user_preferences"
branch_labels = None
depends_on = None

#: Same arrangement as every revision before it: the `.sql` file is the DDL and
#: carries the reasoning, this file supplies the ordering and the downgrade.
SQL_FILE = Path(__file__).resolve().parents[2] / "migrations" / "0020_alias_hygiene.sql"


def upgrade() -> None:
    op.execute(SQL_FILE.read_text())


def downgrade() -> None:
    # Nothing to undo. These rows were derived from entity names by a generator
    # that no longer produces them, so a downgrade that restored them would be
    # restoring output the code above it cannot make -- and the one declared
    # pair is withdrawn in `profile/anchors.yaml`, which a schema downgrade does
    # not read.
    pass
