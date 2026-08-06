from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID

import pytest
from conftest import install_test_api_runtime
from fastapi import HTTPException

from app.core.auth import UserIdentity
from app.services.customer_readiness_policy import (
    CustomerReadinessStage,
    ReadinessBlocker,
)


def _fake_request_with_pool(pool):
    app = SimpleNamespace(state=SimpleNamespace())
    install_test_api_runtime(app, arq_pool=pool)
    return SimpleNamespace(app=app)


class FakeArqPool:
    def __init__(self) -> None:
        self.jobs: list[tuple[str, dict]] = []

    async def enqueue_job(self, name: str, payload: dict, **_kwargs) -> None:
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
            agent_setup_complete=True,
            can_retry_provisioning=False,
            stage="ready",
            can_activate=True,
            can_route=False,
            blockers=["agent_disabled"],
            warnings=[],
            evaluated_at=datetime(2026, 7, 16, 12, 0, tzinfo=UTC),
            policy_version="runtime-v2",
        )

    async def retry_provisioning(self, user_id, *, arq_pool):
        await arq_pool.enqueue_job("outbox_delivery_job", {})
        return SimpleNamespace(stage="provisioning")


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
    assert response.agent_setup_complete is True
    assert response.can_retry_provisioning is False
    assert response.stage == "ready"
    assert response.can_activate is True
    assert response.can_route is False
    assert response.blockers == ["agent_disabled"]
    assert response.warnings == []
    assert response.evaluated_at == datetime(2026, 7, 16, 12, 0, tzinfo=UTC)
    assert response.policy_version == "runtime-v2"


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

    assert response.stage == "provisioning"
    assert pool.jobs == [("outbox_delivery_job", {})]


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


@pytest.mark.anyio
async def test_onboarding_status_remains_compatibility_projection_of_central_readiness() -> None:
    from app.services.onboarding_service import OnboardingService

    evaluated_at = datetime(2026, 7, 18, 10, 30, tzinfo=UTC)
    result = SimpleNamespace(
        stage=CustomerReadinessStage.READY,
        blockers=(
            ReadinessBlocker.AGENT_DISABLED,
            ReadinessBlocker.BUSINESS_PROFILE_INCOMPLETE,
            ReadinessBlocker.FORWARDING_NOT_VERIFIED,
        ),
        can_activate=False,
        can_route=False,
        warnings=(),
        evaluated_at=evaluated_at,
        policy_version="runtime-v2",
    )
    readiness_service = SimpleNamespace(
        provisioning_repository=object(),
        evaluate=lambda user_id: None,
    )

    async def evaluate(user_id):
        return SimpleNamespace(
            result=result,
            subscription=None,
            balance=0,
            phone_number=None,
            provisioning=None,
        )

    readiness_service.evaluate = evaluate
    response = await OnboardingService(
        readiness_service=readiness_service
    ).get_status(UUID("00000000-0000-0000-0000-000000000000"))

    assert response.model_dump() == {
        "subscription_status": None,
        "plan_tier": None,
        "minutes_remaining": 0,
        "phone_number": None,
        "phone_number_status": "missing",
        "agent_setup_complete": True,
        "can_retry_provisioning": False,
        "stage": "ready",
        "can_activate": False,
        "can_route": False,
        "blockers": [
            "agent_disabled",
            "business_profile_incomplete",
            "forwarding_not_verified",
        ],
        "warnings": [],
        "evaluated_at": evaluated_at,
        "policy_version": "runtime-v2",
    }
