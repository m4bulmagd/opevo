from __future__ import annotations

import asyncio
from logging.config import fileConfig
import os

from alembic import context
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config

from app.models import Base
# Import every model for its SQLAlchemy metadata-registration side effect.
from app.models.activation_event import ActivationEvent  # noqa: F401
from app.models.account_deactivation_operation import (  # noqa: F401
    AccountDeactivationOperation,
)
from app.models.agent_config import AgentConfig  # noqa: F401
from app.models.business_profile import BusinessProfile  # noqa: F401
from app.models.call import Call  # noqa: F401
from app.models.call_message import CallMessage  # noqa: F401
from app.models.customer_activation import CustomerActivation  # noqa: F401
from app.models.notification import Notification  # noqa: F401
from app.models.phone_number import PhoneNumber  # noqa: F401
from app.models.recording_egress_operation import (  # noqa: F401
    RecordingEgressOperation,
)
from app.models.subscription import Subscription  # noqa: F401
from app.models.usage_ledger import UsageLedger  # noqa: F401
from app.models.user import User  # noqa: F401
from app.models.webhook_event import WebhookEvent  # noqa: F401


config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

database_url = os.environ.get("DATABASE_URL", "").strip()
if not database_url:
    raise RuntimeError("DATABASE_URL is required to run database migrations")
config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async def run() -> None:
        async with connectable.connect() as connection:
            await connection.run_sync(do_run_migrations)
        await connectable.dispose()

    asyncio.run(run())


def do_run_migrations(connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)

    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
