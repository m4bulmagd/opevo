from types import SimpleNamespace
from uuid import UUID

import pytest
from fastapi import HTTPException

from app.core.auth import UserIdentity


def _fake_request_with_pool(pool):
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(arq_pool=pool)))


class FakeArqPool:
    def __init__(self) -> None:
        self.jobs: list[tuple[str, dict]] = []

    async def enqueue_job(self, name: str, payload: dict) -> None:
        self.jobs.append((name, payload))


class FakeOnboardingService:
    async def get_status(self, user_id):
        from app.schemas.onboarding import OnboardingStatusResponse

        return OnboardingStatusResponse(
            subscription_status="active",
            plan_tier="starter",
            minutes_remaining=60,
            phone_number="+33123456789",
            phone_number_status="ready",
            routing_enabled=False,
            agent_setup_complete=True,
            overall_status="ready_to_enable",
            can_retry_provisioning=False,
        )

    async def retry_provisioning(self, user_id, *, arq_pool):
        await arq_pool.enqueue_job("phone_provisioning_job", {"user_id": str(user_id)})
        from app.schemas.onboarding import RetryProvisioningResponse

        return RetryProvisioningResponse(status="accepted", queued=True)


class FakeRejectingOnboardingService(FakeOnboardingService):
    async def retry_provisioning(self, user_id, *, arq_pool):
        raise HTTPException(status_code=409, detail="Provisioning retry not allowed")


@pytest.mark.anyio
async def test_get_onboarding_status_returns_expected_fields() -> None:
    from app.routers.onboarding import get_onboarding_status

    response = await get_onboarding_status(
        identity=UserIdentity(
            clerk_user_id="user_123",
            internal_user_id=UUID("00000000-0000-0000-0000-000000000000"),
        ),
        service=FakeOnboardingService(),
    )

    assert response.subscription_status == "active"
    assert response.plan_tier == "starter"
    assert response.minutes_remaining == 60
    assert response.phone_number == "+33123456789"
    assert response.phone_number_status == "ready"
    assert response.routing_enabled is False
    assert response.agent_setup_complete is True
    assert response.overall_status == "ready_to_enable"
    assert response.can_retry_provisioning is False


@pytest.mark.anyio
async def test_retry_provisioning_enqueues_job() -> None:
    from app.routers.onboarding import retry_provisioning

    pool = FakeArqPool()
    response = await retry_provisioning(
        request=_fake_request_with_pool(pool),
        identity=UserIdentity(
            clerk_user_id="user_123",
            internal_user_id=UUID("00000000-0000-0000-0000-000000000000"),
        ),
        service=FakeOnboardingService(),
    )

    assert response.status == "accepted"
    assert response.queued is True
    assert pool.jobs == [("phone_provisioning_job", {"user_id": "00000000-0000-0000-0000-000000000000"})]


@pytest.mark.anyio
async def test_retry_provisioning_rejects_non_retryable_state() -> None:
    from app.routers.onboarding import retry_provisioning

    with pytest.raises(HTTPException) as exc_info:
        await retry_provisioning(
            request=_fake_request_with_pool(FakeArqPool()),
            identity=UserIdentity(
                clerk_user_id="user_123",
                internal_user_id=UUID("00000000-0000-0000-0000-000000000000"),
            ),
            service=FakeRejectingOnboardingService(),
        )

    assert exc_info.value.status_code == 409
