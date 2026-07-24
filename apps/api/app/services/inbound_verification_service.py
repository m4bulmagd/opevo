from sqlalchemy.ext.asyncio import AsyncSession

from app.services.forwarding_verification_service import (
    ForwardingVerificationClaim,
    ForwardingVerificationConflictError,
    ForwardingVerificationService,
)
from app.services.outbox_service import OutboxService


class InboundVerificationService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        forwarding_verification_service: ForwardingVerificationService | None = None,
        outbox_service: OutboxService | None = None,
        now_provider=None,
    ) -> None:
        self.session = session
        self.forwarding_verification_service = (
            forwarding_verification_service
            or ForwardingVerificationService(session, now_provider=now_provider)
        )
        self.outbox_service = outbox_service or OutboxService(session)

    async def claim_if_open(
        self,
        *,
        called_number: str,
        room_name: str,
        diversion_number: str | None,
    ) -> ForwardingVerificationClaim | None:
        try:
            claim = (
                await self.forwarding_verification_service.claim_in_transaction(
                    called_number=called_number,
                    room_name=room_name,
                    diversion_number=diversion_number,
                )
            )
        except ForwardingVerificationConflictError:
            return None

        try:
            await self.outbox_service.add(
                topic="livekit.verification_dispatch",
                aggregate_type="forwarding-verification",
                aggregate_id=claim.activation_id,
                idempotency_key=(
                    f"livekit.verification_dispatch:{claim.session_id}"
                ),
                payload={
                    "activation_id": str(claim.activation_id),
                    "session_id": claim.session_id,
                    "room_name": claim.room_name,
                    "lifecycle_generation": claim.lifecycle_generation,
                },
            )
            await self.session.commit()
        except Exception:
            await self.session.rollback()
            raise
        return claim
