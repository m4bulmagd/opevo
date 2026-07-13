from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.usage_ledger import UsageLedger
from app.repositories.call_repository import CallRepository
from app.repositories.usage_repository import UsageRepository


@dataclass(frozen=True)
class UsageGrantResult:
    ledger: UsageLedger
    already_granted: bool
    first_activation: bool


@dataclass(frozen=True)
class UsageDebitResult:
    user_id: UUID
    minutes_charged: int
    balance_before: int
    balance_after: int
    already_debited: bool


class UsageAccountingService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        usage_repository: UsageRepository | None = None,
        call_repository: CallRepository | None = None,
    ) -> None:
        self.usage_repository = usage_repository or UsageRepository(session)
        self.call_repository = call_repository or CallRepository(session)

    async def acquire_invoice_grant_lock(self, *, invoice_id: str) -> None:
        if not isinstance(invoice_id, str) or not invoice_id.strip():
            raise ValueError("Stripe invoice object ID is required")
        await self.usage_repository.lock_invoice_source(source_id=invoice_id)

    async def grant_invoice(
        self,
        *,
        user_id: UUID,
        invoice_id: str,
        minutes: int,
    ) -> UsageGrantResult:
        if minutes < 0:
            raise ValueError("Invoice grant minutes must be nonnegative")

        await self.acquire_invoice_grant_lock(invoice_id=invoice_id)
        user = await self.usage_repository.lock_user(user_id=user_id)
        if user is None:
            raise ValueError("User not found")

        existing = await self.usage_repository.get_invoice_grant_by_source_id(
            source_id=invoice_id
        )
        if existing is not None:
            if existing.user_id != user_id:
                raise ValueError("Invoice grant belongs to another user")
            return UsageGrantResult(
                ledger=existing,
                already_granted=True,
                first_activation=existing.event_type == "subscription_activated",
            )

        first_activation = not await self.usage_repository.has_invoice_grant(
            user_id=user_id
        )
        ledger = await self.usage_repository.create(
            user_id=user_id,
            event_type=(
                "subscription_activated"
                if first_activation
                else "invoice_paid_reset"
            ),
            source_id=invoice_id,
            minutes_delta=minutes,
            balance_after=minutes,
            created_at=await self.usage_repository.next_created_at(
                user_id=user_id
            ),
        )
        return UsageGrantResult(
            ledger=ledger,
            already_granted=False,
            first_activation=first_activation,
        )

    async def debit_call(
        self,
        *,
        call_id: UUID,
        duration_seconds: int,
    ) -> UsageDebitResult:
        if duration_seconds < 0:
            raise ValueError("Call duration must be nonnegative")

        call = await self.call_repository.get_by_id_for_update(call_id)
        if call is None:
            raise ValueError("Call not found")

        existing = await self.usage_repository.get_call_debit(call_id=call.id)
        if existing is not None:
            if existing.user_id != call.user_id:
                raise ValueError("Call debit owner does not match call owner")
            balance_after = int(existing.balance_after or 0)
            minutes_charged = max(0, -existing.minutes_delta)
            return UsageDebitResult(
                user_id=call.user_id,
                minutes_charged=minutes_charged,
                balance_before=balance_after + minutes_charged,
                balance_after=balance_after,
                already_debited=True,
            )

        if call.status == "completed":
            user = await self.usage_repository.lock_user(user_id=call.user_id)
            if user is None:
                raise ValueError("Call owner not found")
            balance = max(
                0,
                await self.usage_repository.get_current_balance(
                    user_id=call.user_id
                ),
            )
            return UsageDebitResult(
                user_id=call.user_id,
                minutes_charged=call.minutes_charged or 0,
                balance_before=balance,
                balance_after=balance,
                already_debited=True,
            )

        user = await self.usage_repository.lock_user(user_id=call.user_id)
        if user is None:
            raise ValueError("Call owner not found")

        balance_before = max(
            0,
            await self.usage_repository.get_current_balance(user_id=call.user_id),
        )
        requested_minutes = max(1, (duration_seconds + 59) // 60)
        minutes_charged = min(requested_minutes, balance_before)
        balance_after = balance_before - minutes_charged
        await self.usage_repository.create(
            user_id=call.user_id,
            call_id=call.id,
            event_type="call_completed",
            minutes_delta=-minutes_charged,
            balance_after=balance_after,
            created_at=await self.usage_repository.next_created_at(
                user_id=call.user_id
            ),
        )
        return UsageDebitResult(
            user_id=call.user_id,
            minutes_charged=minutes_charged,
            balance_before=balance_before,
            balance_after=balance_after,
            already_debited=False,
        )
