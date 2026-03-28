"""add call summary data

Revision ID: 0003_add_call_summary_data
Revises: 0002_add_call_soft_delete
Create Date: 2026-03-28 18:30:00
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0003_add_call_summary_data"
down_revision: str | None = "0002_add_call_soft_delete"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("calls", sa.Column("summary_data", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("calls", "summary_data")
