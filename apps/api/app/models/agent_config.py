from uuid import UUID

from sqlalchemy import Boolean, ForeignKey, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base, TimestampMixin, UUIDPrimaryKeyMixin


class AgentConfig(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "agent_configs"

    user_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("users.id"), nullable=False, unique=True, index=True)
    agent_name: Mapped[str] = mapped_column(String(255), nullable=False, default="Assistant")
    owner_context: Mapped[str | None] = mapped_column(Text, nullable=True)
    system_prompt: Mapped[str] = mapped_column(Text, nullable=False, default="")
    knowledge_base: Mapped[str] = mapped_column(Text, nullable=False, default="")
    pipeline_mode: Mapped[str] = mapped_column(String(50), nullable=False, default="stt_llm_tts")
    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
