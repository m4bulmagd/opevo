from uuid import UUID

from sqlalchemy import CheckConstraint, ForeignKey, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.schema import conv

from app.models import Base, TimestampMixin, UUIDPrimaryKeyMixin


class BillingCheckoutAttempt(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "billing_checkout_attempts"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "lifecycle_generation",
            name="uq_billing_checkout_attempts_user_generation",
        ),
        UniqueConstraint(
            "idempotency_key",
            name="uq_billing_checkout_attempts_idempotency_key",
        ),
        UniqueConstraint(
            "stripe_checkout_session_id",
            name="uq_billing_checkout_attempts_stripe_checkout_session_id",
        ),
        CheckConstraint(
            "lifecycle_generation >= 1",
            name=conv("ck_billing_checkout_attempts_generation_positive"),
        ),
        CheckConstraint(
            "status IN ('pending', 'completed')",
            name=conv("ck_billing_checkout_attempts_status_allowed"),
        ),
    )

    user_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    lifecycle_generation: Mapped[int] = mapped_column(nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    stripe_checkout_session_id: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="pending",
    )
