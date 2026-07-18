"""add durable outbox routing target

Revision ID: 0013_outbox_routing_target
Revises: 0012_customer_activation
Create Date: 2026-07-18 12:00:00
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0013_outbox_routing_target"
down_revision: str | None = "0012_customer_activation"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "outbox_events",
        sa.Column(
            "routing_target_provider_number_id",
            sa.String(length=255),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("outbox_events", "routing_target_provider_number_id")
