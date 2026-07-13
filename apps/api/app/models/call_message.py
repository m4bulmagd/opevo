from uuid import UUID

from sqlalchemy import ForeignKey, Integer, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base, TimestampMixin, UUIDPrimaryKeyMixin


class CallMessage(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "call_messages"
    __table_args__ = (
        UniqueConstraint(
            "call_id",
            "sequence_number",
            name="uq_call_messages_call_sequence",
        ),
    )

    call_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("calls.id"), nullable=False, index=True)
    speaker: Mapped[str] = mapped_column(Text, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    sequence_number: Mapped[int] = mapped_column(Integer, nullable=False)
