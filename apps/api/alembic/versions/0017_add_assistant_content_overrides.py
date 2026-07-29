"""Add profile-owned assistant content overrides.

Revision ID: 0017_assistant_overrides
Revises: 0016_lifecycle_cleanup
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision = "0017_assistant_overrides"
down_revision = "0016_lifecycle_cleanup"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "business_profiles",
        sa.Column("owner_context_override", sa.Text(), nullable=True),
    )
    op.add_column(
        "business_profiles",
        sa.Column("system_prompt_override", sa.Text(), nullable=True),
    )
    op.add_column(
        "business_profiles",
        sa.Column("knowledge_base_override", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("business_profiles", "knowledge_base_override")
    op.drop_column("business_profiles", "system_prompt_override")
    op.drop_column("business_profiles", "owner_context_override")
