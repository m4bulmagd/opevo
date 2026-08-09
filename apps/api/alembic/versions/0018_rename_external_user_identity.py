"""Rename the hosted-provider user identifier to neutral terminology.

Revision ID: 0018_external_user_identity
Revises: 0017_assistant_overrides
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision = "0018_external_user_identity"
down_revision = "0017_assistant_overrides"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "users",
        "clerk_user_id",
        new_column_name="external_user_id",
        existing_type=sa.String(length=255),
        existing_nullable=False,
    )
    op.execute(
        "ALTER TABLE users RENAME CONSTRAINT "
        "uq_users_clerk_user_id TO uq_users_external_user_id"
    )
    op.execute(
        "ALTER INDEX ix_users_clerk_user_id RENAME TO ix_users_external_user_id"
    )


def downgrade() -> None:
    op.execute(
        "ALTER INDEX ix_users_external_user_id RENAME TO ix_users_clerk_user_id"
    )
    op.execute(
        "ALTER TABLE users RENAME CONSTRAINT "
        "uq_users_external_user_id TO uq_users_clerk_user_id"
    )
    op.alter_column(
        "users",
        "external_user_id",
        new_column_name="clerk_user_id",
        existing_type=sa.String(length=255),
        existing_nullable=False,
    )
