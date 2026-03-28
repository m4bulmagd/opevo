"""add call recording metadata

Revision ID: 0004_add_call_recording_metadata
Revises: 0003_add_call_summary_data
Create Date: 2026-03-28 20:20:00
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0004_add_call_recording_metadata"
down_revision: str | None = "0003_add_call_summary_data"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("calls", sa.Column("recording_object_key", sa.String(length=512), nullable=True))
    op.add_column("calls", sa.Column("recording_egress_id", sa.String(length=255), nullable=True))


def downgrade() -> None:
    op.drop_column("calls", "recording_egress_id")
    op.drop_column("calls", "recording_object_key")
