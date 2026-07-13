from contextlib import asynccontextmanager
from uuid import UUID

from sqlalchemy import text


def livekit_dispatch_lock_key(call_id: UUID) -> str:
    return f"livekit.dispatch:{call_id}"


@asynccontextmanager
async def livekit_dispatch_lock(session_factory, call_id: UUID):
    """Serialize provider dispatch and pending-timeout decisions in lock order."""
    async with session_factory() as lock_session:
        if lock_session.get_bind().dialect.name != "postgresql":
            yield
            return
        async with lock_session.begin():
            await lock_session.execute(
                text(
                    "SELECT pg_advisory_xact_lock("
                    "hashtextextended(:lock_key, 0)"
                    ")"
                ),
                {"lock_key": livekit_dispatch_lock_key(call_id)},
            )
            yield
