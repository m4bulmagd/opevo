from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, Index, Integer, JSON, String, UniqueConstraint, Uuid, func, text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.schema import conv

from app.models import Base, TimestampMixin, UUIDPrimaryKeyMixin


class OutboxEvent(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "outbox_events"
    __table_args__ = (
        UniqueConstraint(
            "idempotency_key",
            name="uq_outbox_events_idempotency_key",
        ),
        CheckConstraint(
            "status IN ('pending', 'processing', 'delivered', 'failed')",
            name=conv("ck_outbox_events_status_allowed"),
        ),
        CheckConstraint(
            "attempt_count >= 0",
            name=conv("ck_outbox_events_attempt_count_nonnegative"),
        ),
        CheckConstraint(
            "((status = 'delivered' AND delivered_at IS NOT NULL "
            "AND last_error_code IS NULL) OR "
            "(status <> 'delivered' AND delivered_at IS NULL)) "
            "AND (status <> 'failed' OR last_error_code IS NOT NULL)",
            name=conv("ck_outbox_events_delivery_consistent"),
        ),
        Index(
            "ix_outbox_events_due_work",
            "status",
            "next_attempt_at",
            "created_at",
            "id",
        ),
    )

    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    topic: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    aggregate_type: Mapped[str] = mapped_column(String(100), nullable=False)
    aggregate_id: Mapped[UUID] = mapped_column(Uuid, nullable=False, index=True)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="pending",
        server_default=text("'pending'"),
        index=True,
    )
    attempt_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
    )
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    last_error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    routing_target_provider_number_id: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
