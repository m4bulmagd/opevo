"""add call soft delete

Revision ID: 0002_add_call_soft_delete
Revises: 0001_initial_schema
Create Date: 2026-03-28 13:30:00
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0002_add_call_soft_delete"
down_revision: str | None = "0001_initial_schema"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("calls", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("calls", "deleted_at")
