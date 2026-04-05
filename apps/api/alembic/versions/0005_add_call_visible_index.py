"""add partial composite index for visible calls

Revision ID: 0005_add_call_visible_index
Revises: 0004_add_call_recording_metadata
Create Date: 2026-04-03 12:00:00
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0005_add_call_visible_index"
down_revision: str | None = "0004_add_call_recording_metadata"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_calls_user_visible",
        "calls",
        ["user_id", sa.text("started_at DESC")],
        postgresql_where="deleted_at IS NULL",
    )


def downgrade() -> None:
    op.drop_index("ix_calls_user_visible", table_name="calls")
