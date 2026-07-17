from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.schema import conv

from app.models import Base, TimestampMixin, UUIDPrimaryKeyMixin


class CustomerActivation(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "customer_activations"
    __table_args__ = (
        CheckConstraint(
            "workflow_version >= 1",
            name=conv("ck_customer_activations_workflow_version_positive"),
        ),
        CheckConstraint(
            "verification_status IN "
            "('not_started', 'open', 'claimed', 'succeeded', 'failed', "
            "'expired', 'invalidated')",
            name=conv("ck_customer_activations_verification_status_allowed"),
        ),
    )

    user_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("users.id"),
        nullable=False,
        unique=True,
        index=True,
    )
    workflow_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    profile_confirmed_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    profile_confirmed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    provisioning_consented_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    provisioning_idempotency_key: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        unique=True,
    )
    verification_window_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    verification_window_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    verification_session_id: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        unique=True,
    )
    verification_claimed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    verification_dispatch_id: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        unique=True,
    )
    verification_routing_fingerprint: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )
    verification_status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="not_started",
    )
    verified_routing_fingerprint: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )
    forwarding_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    go_live_requested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    go_live_approved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    activated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    last_failure_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
