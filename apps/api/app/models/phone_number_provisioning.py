from uuid import UUID

from sqlalchemy import JSON, Boolean, CheckConstraint, ForeignKey, Integer, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.schema import conv

from app.models import Base, TimestampMixin, UUIDPrimaryKeyMixin


class PhoneNumberProvisioning(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "phone_number_provisionings"
    __table_args__ = (
        CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed')",
            name=conv("ck_phone_number_provisionings_status_allowed"),
        ),
        CheckConstraint(
            "attempt_count >= 0",
            name=conv(
                "ck_phone_number_provisionings_attempt_count_nonnegative"
            ),
        ),
    )

    user_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("users.id"), nullable=False, unique=True, index=True)
    phone_number_id: Mapped[UUID | None] = mapped_column(
        Uuid, ForeignKey("phone_numbers.id"), nullable=True, index=True
    )
    target_country_code: Mapped[str] = mapped_column(String(2), nullable=False, default="FR")
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="queued")
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    can_retry: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    last_error_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    last_error_payload: Mapped[dict | None] = mapped_column(JSON, nullable=True, default=dict)
