from uuid import UUID

from sqlalchemy import ForeignKey, Index, Integer, String, Uuid, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base, TimestampMixin, UUIDPrimaryKeyMixin


class UsageLedger(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "usage_ledgers"
    __table_args__ = (
        Index(
            "uq_usage_ledgers_call_event_type",
            "call_id",
            "event_type",
            unique=True,
            postgresql_where=text("call_id IS NOT NULL"),
            sqlite_where=text("call_id IS NOT NULL"),
        ),
        Index(
            "uq_usage_ledgers_event_source",
            "event_type",
            "source_id",
            unique=True,
            postgresql_where=text("source_id IS NOT NULL"),
            sqlite_where=text("source_id IS NOT NULL"),
        ),
    )

    user_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("users.id"), nullable=False, index=True)
    call_id: Mapped[UUID | None] = mapped_column(Uuid, ForeignKey("calls.id"), nullable=True, index=True)
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    source_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    minutes_delta: Mapped[int] = mapped_column(Integer, nullable=False)
    balance_after: Mapped[int | None] = mapped_column(Integer, nullable=True)
