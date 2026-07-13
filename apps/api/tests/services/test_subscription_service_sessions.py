import asyncio
from types import SimpleNamespace

import pytest

from app.services.billing_query_service import BillingQueryService
from app.services.onboarding_service import OnboardingService


class SingleSessionGuard:
    def __init__(self) -> None:
        self.active = False

    async def operation(self, result):
        if self.active:
            raise RuntimeError("concurrent operation on one session")
        self.active = True
        try:
            await asyncio.sleep(0)
            return result
        finally:
            self.active = False


class UserLookupRepository:
    def __init__(self, result, guard: SingleSessionGuard | None = None) -> None:
        self.result = result
        self.guard = guard

    async def get_by_user_id(self, _user_id):
        if self.guard is None:
            return self.result
        return await self.guard.operation(self.result)


class UsageRepository:
    def __init__(self, balance: int, guard: SingleSessionGuard | None = None) -> None:
        self.balance = balance
        self.guard = guard

    async def get_current_balance(self, *, user_id):
        if self.guard is None:
            return self.balance
        return await self.guard.operation(self.balance)


@pytest.mark.anyio
async def test_billing_query_runs_same_session_reads_sequentially() -> None:
    guard = SingleSessionGuard()
    service = BillingQueryService(
        subscription_repository=UserLookupRepository(None, guard),
        usage_repository=UsageRepository(0, guard),
    )

    snapshot = await service.get_usage_snapshot("user-id")

    assert snapshot.minutes_remaining == 0


@pytest.mark.anyio
async def test_onboarding_runs_same_session_reads_sequentially() -> None:
    guard = SingleSessionGuard()
    service = OnboardingService(
        subscription_repository=UserLookupRepository(None, guard),
        usage_repository=UsageRepository(0, guard),
        phone_number_repository=UserLookupRepository(None, guard),
        provisioning_repository=UserLookupRepository(None, guard),
        agent_config_repository=UserLookupRepository(None, guard),
    )

    status = await service.get_status("user-id")

    assert status.overall_status == "not_subscribed"


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("subscription_status", "has_access"),
    [
        ("active", True),
        ("trialing", True),
        ("past_due", False),
        ("unpaid", False),
        ("canceled", False),
        ("incomplete", False),
        ("incomplete_expired", False),
        ("paused", False),
        ("unknown", False),
    ],
)
async def test_onboarding_routes_only_with_central_subscription_access(
    subscription_status: str,
    has_access: bool,
) -> None:
    subscription = SimpleNamespace(status=subscription_status, plan_tier="starter")
    phone_number = SimpleNamespace(e164="+33123456789", is_active=True)
    provisioning = SimpleNamespace(
        status="succeeded",
        can_retry=False,
    )
    config = SimpleNamespace(
        is_enabled=True,
        agent_name="Presvo Front Desk",
        owner_context="Dental office reception",
        system_prompt="Handle inbound calls professionally.",
        knowledge_base="Open weekdays.",
    )
    service = OnboardingService(
        subscription_repository=UserLookupRepository(subscription),
        usage_repository=UsageRepository(60),
        phone_number_repository=UserLookupRepository(phone_number),
        provisioning_repository=UserLookupRepository(provisioning),
        agent_config_repository=UserLookupRepository(config),
    )

    status = await service.get_status("user-id")

    assert status.routing_enabled is has_access
    assert (status.overall_status == "live") is has_access


@pytest.mark.anyio
async def test_trialing_subscription_can_retry_failed_provisioning() -> None:
    subscription = SimpleNamespace(status="trialing", plan_tier="starter")
    provisioning = SimpleNamespace(status="failed", can_retry=True)
    service = OnboardingService(
        subscription_repository=UserLookupRepository(subscription),
        usage_repository=UsageRepository(60),
        phone_number_repository=UserLookupRepository(None),
        provisioning_repository=UserLookupRepository(provisioning),
        agent_config_repository=UserLookupRepository(None),
    )

    status = await service.get_status("user-id")

    assert status.can_retry_provisioning is True
