"""The authored documents, and the archives, as rows (ADR 0234).

Revision ID: 0025_config_documents
Revises: 0024_data_exports
"""

from __future__ import annotations

from pathlib import Path

from alembic import op

revision = "0025_config_documents"
down_revision = "0024_data_exports"
branch_labels = None
depends_on = None

#: Same arrangement as every revision before it: the `.sql` file is the DDL and
#: carries the reasoning, this file supplies the ordering and the downgrade.
SQL_FILE = Path(__file__).resolve().parents[2] / "migrations" / "0027_config_documents.sql"


def upgrade() -> None:
    op.execute(SQL_FILE.read_text())


def downgrade() -> None:
    # The archive bytes go last and the tables first, so a downgrade that fails
    # partway leaves the column rather than the documents -- the column is
    # recoverable by re-running an export, and the documents are not.
    op.execute("DROP INDEX IF EXISTS ix_config_document_history_key")
    op.execute("DROP TABLE IF EXISTS config_document_history")
    op.execute("DROP TABLE IF EXISTS config_documents")
    op.execute("ALTER TABLE data_exports DROP COLUMN IF EXISTS content")
