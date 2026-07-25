from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    String,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.schema import conv

from app.models import Base, TimestampMixin, UUIDPrimaryKeyMixin


class ProviderCleanupOperation(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "provider_cleanup_operations"
    __table_args__ = (
        UniqueConstraint(
            "resource_type",
            "provider_resource_id",
            name="uq_provider_cleanup_operations_resource",
        ),
        CheckConstraint(
            "resource_type IN ('phone_number', 'stripe_subscription')",
            name=conv("ck_provider_cleanup_operations_resource_type_allowed"),
        ),
        CheckConstraint(
            "status IN ('pending', 'processing', 'attention_required', 'completed')",
            name=conv("ck_provider_cleanup_operations_status_allowed"),
        ),
        CheckConstraint(
            "(status = 'completed' AND completed_at IS NOT NULL) OR "
            "(status <> 'completed' AND completed_at IS NULL)",
            name=conv("ck_provider_cleanup_operations_completion_consistent"),
        ),
        CheckConstraint(
            "lifecycle_generation >= 1",
            name=conv("ck_provider_cleanup_operations_generation_positive"),
        ),
        CheckConstraint(
            "attempt_count >= 0",
            name=conv("ck_provider_cleanup_operations_attempt_count_nonnegative"),
        ),
    )

    user_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    lifecycle_generation: Mapped[int] = mapped_column(nullable=False)
    resource_type: Mapped[str] = mapped_column(String(32), nullable=False)
    provider_resource_id: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="pending",
    )
    routing_disabled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    attempt_count: Mapped[int] = mapped_column(nullable=False, default=0)
    last_error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
