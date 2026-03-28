from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base, TimestampMixin, UUIDPrimaryKeyMixin


class Call(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "calls"

    user_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("users.id"), nullable=False, index=True)
    phone_number_id: Mapped[UUID | None] = mapped_column(Uuid, ForeignKey("phone_numbers.id"), nullable=True, index=True)
    livekit_room_id: Mapped[str | None] = mapped_column(String(255), unique=True, nullable=True)
    caller_number: Mapped[str | None] = mapped_column(String(32), nullable=True)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="pending")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    minutes_charged: Mapped[int | None] = mapped_column(Integer, nullable=True)
    summary_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    recording_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
