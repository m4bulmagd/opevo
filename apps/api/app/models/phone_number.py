from uuid import UUID

from sqlalchemy import Boolean, ForeignKey, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base, TimestampMixin, UUIDPrimaryKeyMixin


class PhoneNumber(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "phone_numbers"

    user_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("users.id"),
        nullable=False,
        unique=True,
        index=True,
    )
    e164: Mapped[str] = mapped_column(String(32), unique=True, nullable=False, index=True)
    country_code: Mapped[str] = mapped_column(String(2), nullable=False)
    provider: Mapped[str] = mapped_column(String(50), nullable=False, default="telnyx")
    provider_number_id: Mapped[str | None] = mapped_column(String(255), unique=True, nullable=True)
    provider_connection_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
