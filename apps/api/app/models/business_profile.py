from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, JSON, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.schema import conv

from app.models import Base, TimestampMixin, UUIDPrimaryKeyMixin


class BusinessProfile(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "business_profiles"
    __table_args__ = (
        CheckConstraint(
            "content_revision >= 1",
            name=conv("ck_business_profiles_content_revision_positive"),
        ),
        CheckConstraint(
            "routing_revision >= 1",
            name=conv("ck_business_profiles_routing_revision_positive"),
        ),
        CheckConstraint(
            "confirmed_carrier IS NULL OR confirmed_carrier IN "
            "('orange', 'sfr', 'bouygues', 'free', 'other')",
            name=conv("ck_business_profiles_confirmed_carrier_allowed"),
        ),
    )

    user_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("users.id"),
        nullable=False,
        unique=True,
        index=True,
    )
    owner_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    business_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    business_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    public_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    timezone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    business_hours: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    existing_phone_e164: Mapped[str | None] = mapped_column(String(32), nullable=True)
    detected_carrier: Mapped[str | None] = mapped_column(String(100), nullable=True)
    detected_number_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    carrier_lookup_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    carrier_looked_up_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    confirmed_carrier: Mapped[str | None] = mapped_column(String(32), nullable=True)
    receptionist_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    faqs: Mapped[list[dict[str, str]]] = mapped_column(
        JSON,
        nullable=False,
        default=list,
    )
    special_instructions: Mapped[str | None] = mapped_column(Text, nullable=True)
    escalation_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    routing_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
