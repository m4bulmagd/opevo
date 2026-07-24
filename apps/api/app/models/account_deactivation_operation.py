from datetime import datetime
from typing import Literal
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.schema import conv

from app.models import Base, TimestampMixin, UUIDPrimaryKeyMixin


ACCOUNT_STATUSES = frozenset({"active", "deactivating", "inactive"})
DEACTIVATION_TRIGGERS = frozenset({"owner_request", "subscription_ended"})
DEACTIVATION_STATUSES = frozenset(
    {"pending", "processing", "attention_required", "completed"}
)
DeactivationTrigger = Literal["owner_request", "subscription_ended"]


class AccountDeactivationOperation(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "account_deactivation_operations"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "lifecycle_generation",
            name="uq_account_deactivation_operations_user_generation",
        ),
        CheckConstraint(
            "trigger IN ('owner_request','subscription_ended')",
            name=conv("ck_account_deactivation_operations_trigger_allowed"),
        ),
        CheckConstraint(
            "status IN ('pending','processing','attention_required','completed')",
            name=conv("ck_account_deactivation_operations_status_allowed"),
        ),
        CheckConstraint(
            "lifecycle_generation > 0",
            name=conv("ck_account_deactivation_operations_generation_positive"),
        ),
        CheckConstraint(
            "(status = 'completed' AND completed_at IS NOT NULL) OR "
            "(status <> 'completed' AND completed_at IS NULL)",
            name=conv("ck_account_deactivation_operations_completion_consistent"),
        ),
        CheckConstraint(
            "attempt_count >= 0",
            name=conv(
                "ck_account_deactivation_operations_attempt_count_nonnegative"
            ),
        ),
        CheckConstraint(
            "(subscription_canceled_at IS NULL OR routing_disabled_at IS NOT NULL) "
            "AND (active_call_drained_at IS NULL OR subscription_canceled_at IS NOT NULL) "
            "AND (number_released_at IS NULL OR active_call_drained_at IS NOT NULL) "
            "AND (activation_reset_at IS NULL OR number_released_at IS NOT NULL) "
            "AND (completed_at IS NULL OR activation_reset_at IS NOT NULL)",
            name=conv("ck_account_deactivation_operations_step_order"),
        ),
        Index(
            "uq_account_deactivation_operations_one_incomplete_user",
            "user_id",
            unique=True,
            postgresql_where=text("completed_at IS NULL"),
            sqlite_where=text("completed_at IS NULL"),
        ),
    )

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    lifecycle_generation: Mapped[int] = mapped_column(nullable=False)
    trigger: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    stripe_subscription_id: Mapped[str | None] = mapped_column(String(255))
    phone_provider_id: Mapped[str | None] = mapped_column(String(255))
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    routing_disabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    subscription_canceled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    active_call_drained_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    number_released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    activation_reset_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempt_count: Mapped[int] = mapped_column(nullable=False, default=0)
    last_reconciled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_code: Mapped[str | None] = mapped_column(String(64))
