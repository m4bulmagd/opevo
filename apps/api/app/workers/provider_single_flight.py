from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, AsyncSession


@dataclass(frozen=True)
class ProviderSingleFlight:
    connection: AsyncConnection | None

    def assert_transaction_free(
        self,
        *business_sessions: AsyncSession,
    ) -> None:
        if self.connection is not None and self.connection.in_transaction():
            raise RuntimeError("provider lock connection has an open transaction")
        if any(session.in_transaction() for session in business_sessions):
            raise RuntimeError("provider business session has an open transaction")


@asynccontextmanager
async def provider_single_flight(session_factory, operation_key: str | None):
    if operation_key is None:
        yield ProviderSingleFlight(connection=None)
        return

    async with session_factory() as lock_session:
        if lock_session.get_bind().dialect.name != "postgresql":
            yield ProviderSingleFlight(connection=None)
            return
        bind = lock_session.bind
        if not isinstance(bind, AsyncEngine):
            raise RuntimeError("provider single-flight requires an async engine")
        async with bind.connect() as connection:
            await connection.execute(
                text(
                    "SELECT pg_advisory_lock("
                    "hashtextextended(CAST(:operation_key AS text), 0))"
                ),
                {"operation_key": operation_key},
            )
            await connection.commit()
            guard = ProviderSingleFlight(connection=connection)
            guard.assert_transaction_free(lock_session)
            try:
                yield guard
            finally:
                if connection.in_transaction():
                    await connection.rollback()
                released = await connection.scalar(
                    text(
                        "SELECT pg_advisory_unlock("
                        "hashtextextended(CAST(:operation_key AS text), 0))"
                    ),
                    {"operation_key": operation_key},
                )
                await connection.commit()
                if released is not True:
                    raise RuntimeError("provider single-flight lock was not held")
