from uuid import UUID

from sqlalchemy import ForeignKey, Integer, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base, TimestampMixin, UUIDPrimaryKeyMixin


class UsageLedger(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "usage_ledgers"

    user_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("users.id"), nullable=False, index=True)
    call_id: Mapped[UUID | None] = mapped_column(Uuid, ForeignKey("calls.id"), nullable=True, index=True)
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    minutes_delta: Mapped[int] = mapped_column(Integer, nullable=False)
    balance_after: Mapped[int | None] = mapped_column(Integer, nullable=True)
