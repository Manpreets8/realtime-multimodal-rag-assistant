"""Documents: extracted metadata, processing statistics and a failure code.

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Existing documents keep working; they gain metadata and statistics when re-processed.
    op.add_column("documents", sa.Column("error_code", sa.String(length=40), nullable=True))
    op.add_column("documents", sa.Column("extracted_metadata", postgresql.JSONB(), nullable=True))
    op.add_column("documents", sa.Column("processing_stats", postgresql.JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("documents", "processing_stats")
    op.drop_column("documents", "extracted_metadata")
    op.drop_column("documents", "error_code")
