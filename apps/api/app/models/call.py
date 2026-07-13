from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    Uuid,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.schema import conv

from app.models import Base, TimestampMixin, UUIDPrimaryKeyMixin


class Call(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "calls"
    __table_args__ = (
        Index(
            "uq_calls_user_active",
            "user_id",
            unique=True,
            postgresql_where=text(
                "status IN ('pending', 'connected', 'ending', 'finalizing')"
            ),
            sqlite_where=text(
                "status IN ('pending', 'connected', 'ending', 'finalizing')"
            ),
        ),
        CheckConstraint(
            "duration_seconds IS NULL OR duration_seconds >= 0",
            name=conv("ck_calls_duration_seconds_nonnegative"),
        ),
        CheckConstraint(
            "minutes_charged IS NULL OR minutes_charged >= 0",
            name=conv("ck_calls_minutes_charged_nonnegative"),
        ),
        CheckConstraint(
            "status IN ('pending', 'connected', 'ending', 'finalizing', "
            "'completed', 'failed')",
            name=conv("ck_calls_status_allowed"),
        ),
        CheckConstraint(
            "finalization_attempt_count >= 0",
            name=conv("ck_calls_finalization_attempt_count_nonnegative"),
        ),
        CheckConstraint(
            "summary_transcript_max_sequence IS NULL OR "
            "summary_transcript_max_sequence >= 0",
            name=conv("ck_calls_summary_transcript_max_sequence_nonnegative"),
        ),
        CheckConstraint(
            "(status = 'failed' AND failure_code IS NOT NULL) OR "
            "(status <> 'failed' AND failure_code IS NULL)",
            name=conv("ck_calls_failure_status_consistent"),
        ),
        Index(
            "ix_calls_reconciliation_stale_work",
            "status",
            "state_changed_at",
            "last_reconciled_at",
            postgresql_where=text(
                "status IN ('pending', 'connected', 'ending', 'finalizing')"
            ),
            sqlite_where=text(
                "status IN ('pending', 'connected', 'ending', 'finalizing')"
            ),
        ),
    )

    user_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("users.id"), nullable=False, index=True)
    phone_number_id: Mapped[UUID | None] = mapped_column(Uuid, ForeignKey("phone_numbers.id"), nullable=True, index=True)
    agent_config_id: Mapped[UUID | None] = mapped_column(
        Uuid,
        ForeignKey("agent_configs.id"),
        nullable=True,
        index=True,
    )
    livekit_room_id: Mapped[str | None] = mapped_column(String(255), unique=True, nullable=True)
    livekit_dispatch_id: Mapped[str | None] = mapped_column(
        String(255),
        unique=True,
        nullable=True,
    )
    failure_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    caller_number: Mapped[str | None] = mapped_column(String(32), nullable=True)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="pending")
    state_changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
    )
    finalization_attempt_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
    )
    last_reconciled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    minutes_charged: Mapped[int | None] = mapped_column(Integer, nullable=True)
    summary_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary_data: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    summary_transcript_max_sequence: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    recording_object_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    recording_egress_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    recording_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
