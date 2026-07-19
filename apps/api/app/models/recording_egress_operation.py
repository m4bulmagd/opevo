from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    Uuid,
    false,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.schema import conv

from app.models import Base, TimestampMixin, UUIDPrimaryKeyMixin


RECORDING_START_STATES = frozenset(
    {"prepared", "starting", "started", "not_started", "uncertain"}
)


class RecordingEgressOperation(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "recording_egress_operations"
    __table_args__ = (
        UniqueConstraint(
            "call_id",
            name="uq_recording_egress_operations_call_id",
        ),
        UniqueConstraint(
            "provider_egress_id",
            name="uq_recording_egress_operations_provider_egress_id",
        ),
        CheckConstraint(
            "start_state IN ('prepared','starting','started','not_started','uncertain')",
            name=conv("ck_recording_egress_operations_start_state_allowed"),
        ),
        CheckConstraint(
            "(start_state = 'started' AND provider_egress_id IS NOT NULL) OR "
            "(start_state <> 'started' AND provider_egress_id IS NULL)",
            name=conv(
                "ck_recording_egress_operations_provider_identity_consistent"
            ),
        ),
        CheckConstraint(
            "(legacy_incomplete = false AND room_name IS NOT NULL) OR "
            "(legacy_incomplete = true AND room_name IS NULL AND "
            "start_state IN ('started','uncertain'))",
            name=conv("ck_recording_egress_operations_legacy_room_consistent"),
        ),
        CheckConstraint(
            "start_state <> 'prepared' OR start_attempted_at IS NULL",
            name=conv(
                "ck_recording_egress_operations_prepared_attempt_consistent"
            ),
        ),
        CheckConstraint(
            "delete_requested_at IS NULL OR stop_requested_at IS NOT NULL",
            name=conv("ck_recording_egress_operations_delete_implies_stop"),
        ),
        CheckConstraint(
            "object_deleted_at IS NULL OR delete_requested_at IS NOT NULL",
            name=conv(
                "ck_recording_egress_operations_object_delete_implies_request"
            ),
        ),
        Index(
            "ix_recording_egress_operations_due_work",
            "start_state",
            "stop_requested_at",
            "delete_requested_at",
            "updated_at",
        ),
    )

    call_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("calls.id", ondelete="RESTRICT"),
        nullable=False,
    )
    room_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    legacy_incomplete: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=false(),
    )
    expected_object_key: Mapped[str] = mapped_column(String(512), nullable=False)
    provider_egress_id: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    start_state: Mapped[str] = mapped_column(String(32), nullable=False)
    start_attempted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    stop_requested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    delete_requested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    provider_terminal_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    object_deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    last_reconciled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    last_error_code: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )
