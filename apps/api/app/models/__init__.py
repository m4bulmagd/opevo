from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, MetaData, Uuid, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


naming_convention = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=naming_convention)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class UUIDPrimaryKeyMixin:
    id: Mapped[UUID] = mapped_column(
        Uuid,
        primary_key=True,
        default=uuid4,
    )


from app.models.agent_config import AgentConfig  # noqa: E402,F401
from app.models.call import Call  # noqa: E402,F401
from app.models.call_message import CallMessage  # noqa: E402,F401
from app.models.notification import Notification  # noqa: E402,F401
from app.models.phone_number import PhoneNumber  # noqa: E402,F401
from app.models.subscription import Subscription  # noqa: E402,F401
from app.models.usage_ledger import UsageLedger  # noqa: E402,F401
from app.models.user import User  # noqa: E402,F401
from app.models.webhook_event import WebhookEvent  # noqa: E402,F401
