"""add durable LiveKit dispatch identity to calls

Revision ID: 0010_durable_livekit_dispatch
Revises: 0009_transactional_outbox
Create Date: 2026-07-13 20:00:00
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0010_durable_livekit_dispatch"
down_revision: str | None = "0009_transactional_outbox"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "calls",
        sa.Column("agent_config_id", sa.Uuid(), nullable=True),
    )
    op.add_column(
        "calls",
        sa.Column("livekit_dispatch_id", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "calls",
        sa.Column("failure_code", sa.String(length=100), nullable=True),
    )
    op.create_foreign_key(
        "fk_calls_agent_config_id_agent_configs",
        "calls",
        "agent_configs",
        ["agent_config_id"],
        ["id"],
    )
    op.create_index(
        "ix_calls_agent_config_id",
        "calls",
        ["agent_config_id"],
    )
    op.create_unique_constraint(
        "uq_calls_livekit_dispatch_id",
        "calls",
        ["livekit_dispatch_id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_calls_livekit_dispatch_id",
        "calls",
        type_="unique",
    )
    op.drop_index("ix_calls_agent_config_id", table_name="calls")
    op.drop_constraint(
        "fk_calls_agent_config_id_agent_configs",
        "calls",
        type_="foreignkey",
    )
    op.drop_column("calls", "failure_code")
    op.drop_column("calls", "livekit_dispatch_id")
    op.drop_column("calls", "agent_config_id")
