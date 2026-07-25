from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base, TimestampMixin, UUIDPrimaryKeyMixin


class SubscriptionCycleHistory(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "subscription_cycle_history"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "lifecycle_generation",
            name="uq_subscription_cycle_history_user_generation",
        ),
        UniqueConstraint(
            "stripe_subscription_id",
            name="uq_subscription_cycle_history_stripe_subscription_id",
        ),
    )

    user_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    lifecycle_generation: Mapped[int] = mapped_column(nullable=False)
    stripe_customer_id: Mapped[str] = mapped_column(String(255), nullable=False)
    stripe_subscription_id: Mapped[str] = mapped_column(String(255), nullable=False)
    plan_tier: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[str] = mapped_column(String(50), nullable=False)
    allocated_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    current_period_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    current_period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    stripe_subscription_created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    last_stripe_event_created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    cancel_at_period_end: Mapped[bool] = mapped_column(Boolean, nullable=False)
    cancellation_effective_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
