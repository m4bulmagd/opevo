"""add phone number provisioning state

Revision ID: 0006_phone_number_provisioning
Revises: 0005_add_call_visible_index
Create Date: 2026-04-11 12:00:00
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0006_phone_number_provisioning"
down_revision: str | None = "0005_add_call_visible_index"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "phone_number_provisionings",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("phone_number_id", sa.Uuid(), nullable=True),
        sa.Column("target_country_code", sa.String(length=2), nullable=False),
        sa.Column("status", sa.String(length=50), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("can_retry", sa.Boolean(), nullable=False),
        sa.Column("last_error_reason", sa.String(length=255), nullable=True),
        sa.Column("last_error_payload", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["phone_number_id"], ["phone_numbers.id"], name=op.f("fk_phone_number_provisionings_phone_number_id_phone_numbers")),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_phone_number_provisionings_user_id_users")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_phone_number_provisionings")),
        sa.UniqueConstraint("user_id", name=op.f("uq_phone_number_provisionings_user_id")),
    )
    op.create_index(
        op.f("ix_phone_number_provisionings_phone_number_id"),
        "phone_number_provisionings",
        ["phone_number_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_phone_number_provisionings_user_id"),
        "phone_number_provisionings",
        ["user_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_phone_number_provisionings_user_id"), table_name="phone_number_provisionings")
    op.drop_index(op.f("ix_phone_number_provisionings_phone_number_id"), table_name="phone_number_provisionings")
    op.drop_table("phone_number_provisionings")
