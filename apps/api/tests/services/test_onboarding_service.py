from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.models.agent_config import AgentConfig
from app.models.phone_number import PhoneNumber
from app.models.phone_number_provisioning import PhoneNumberProvisioning
from app.models.subscription import Subscription
from app.models.usage_ledger import UsageLedger


def _add_subscription(
    db_session,
    user_id,
    *,
    period_start: datetime = datetime(2026, 1, 1, tzinfo=UTC),
    period_end: datetime = datetime(2099, 1, 1, tzinfo=UTC),
) -> None:
    db_session.add(
        Subscription(
            user_id=user_id,
            stripe_customer_id=f"cus_{uuid4().hex}",
            stripe_subscription_id=f"sub_{uuid4().hex}",
            plan_tier="starter",
            status="active",
            allocated_minutes=60,
            current_period_start=period_start,
            current_period_end=period_end,
        )
    )


def _add_minutes(db_session, user_id, *, balance: int = 60) -> None:
    db_session.add(
        UsageLedger(
            user_id=user_id,
            event_type="subscription_activated",
            minutes_delta=balance,
            balance_after=balance,
        )
    )


def _add_phone(
    db_session,
    user_id,
    *,
    connection_name: str = "app-disabled",
    is_active: bool = False,
    provider_number_id: str | None = "pn_123",
) -> PhoneNumber:
    phone_number = PhoneNumber(
        id=uuid4(),
        user_id=user_id,
        e164="+35315551234",
        country_code="IE",
        provider="telnyx",
        provider_number_id=provider_number_id,
        provider_connection_name=connection_name,
        is_active=is_active,
    )
    db_session.add(phone_number)
    db_session.add(
        PhoneNumberProvisioning(
            user_id=user_id,
            phone_number_id=phone_number.id,
            target_country_code="IE",
            status="succeeded",
            attempt_count=1,
            can_retry=False,
        )
    )
    return phone_number


def _add_agent_config(
    db_session,
    user_id,
    *,
    complete: bool,
    enabled: bool = False,
) -> None:
    db_session.add(
        AgentConfig(
            user_id=user_id,
            agent_name="Presvo Front Desk" if complete else "Assistant",
            owner_context="Dental office reception" if complete else "",
            system_prompt=(
                "Answer missed calls and capture the caller's requested outcome."
                if complete
                else ""
            ),
            knowledge_base="Open weekdays." if complete else "",
            pipeline_mode="stt_llm_tts",
            is_enabled=enabled,
        )
    )


@pytest.mark.anyio
async def test_get_status_returns_subscription_required_defaults(
    db_session,
    active_user,
) -> None:
    from app.services.onboarding_service import OnboardingService

    status = await OnboardingService(db_session).get_status(active_user.id)

    assert status.subscription_status is None
    assert status.plan_tier is None
    assert status.minutes_remaining == 0
    assert status.phone_number is None
    assert status.phone_number_status == "missing"
    assert status.agent_setup_complete is False
    assert status.can_retry_provisioning is False
    assert status.stage == "subscription_required"
    assert status.can_activate is False
    assert status.can_route is False
    assert "subscription_missing" in status.blockers
    assert "minutes_exhausted" in status.blockers
    assert status.warnings == []
    assert status.policy_version == "runtime-v3"
    assert status.evaluated_at.tzinfo is not None


@pytest.mark.anyio
async def test_get_status_suspends_expired_subscription_period(
    db_session,
    active_user,
) -> None:
    from app.services.onboarding_service import OnboardingService

    _add_subscription(
        db_session,
        active_user.id,
        period_start=datetime(2026, 1, 1, tzinfo=UTC),
        period_end=datetime(2026, 2, 1, tzinfo=UTC),
    )
    _add_minutes(db_session, active_user.id)
    await db_session.commit()

    status = await OnboardingService(db_session).get_status(active_user.id)

    assert status.stage == "suspended"
    assert status.can_activate is False
    assert status.can_route is False
    assert "subscription_period_inactive" in status.blockers


@pytest.mark.anyio
async def test_get_status_suspends_when_minutes_are_exhausted(
    db_session,
    active_user,
) -> None:
    from app.services.onboarding_service import OnboardingService

    _add_subscription(db_session, active_user.id)
    await db_session.commit()

    status = await OnboardingService(db_session).get_status(active_user.id)

    assert status.stage == "suspended"
    assert status.minutes_remaining == 0
    assert "minutes_exhausted" in status.blockers


@pytest.mark.anyio
async def test_get_status_returns_provisioning_failed_with_retry(
    db_session,
    active_user,
) -> None:
    from app.services.onboarding_service import OnboardingService

    _add_subscription(db_session, active_user.id)
    _add_minutes(db_session, active_user.id)
    db_session.add(
        PhoneNumberProvisioning(
            user_id=active_user.id,
            target_country_code="IE",
            status="failed",
            attempt_count=1,
            can_retry=True,
            last_error_reason="no_affordable_number",
            last_error_payload={"retryable": True},
        )
    )
    await db_session.commit()

    status = await OnboardingService(db_session).get_status(active_user.id)

    assert status.phone_number_status == "failed"
    assert status.can_retry_provisioning is True
    assert status.stage == "number_provisioning_failed"
    assert status.can_activate is False
    assert status.can_route is False


@pytest.mark.anyio
async def test_get_status_never_labels_incomplete_provider_state_as_ready(
    db_session,
    active_user,
) -> None:
    from app.services.onboarding_service import OnboardingService

    _add_subscription(db_session, active_user.id)
    _add_minutes(db_session, active_user.id)
    _add_phone(db_session, active_user.id, provider_number_id=None)
    _add_agent_config(db_session, active_user.id, complete=True)
    await db_session.commit()

    status = await OnboardingService(db_session).get_status(active_user.id)

    assert status.stage == "number_provisioning_failed"
    assert status.phone_number_status == "failed"
    assert status.can_retry_provisioning is False
    assert "phone_provider_id_missing" in status.blockers


@pytest.mark.anyio
async def test_get_status_requires_receptionist_setup_when_number_is_ready(
    db_session,
    active_user,
) -> None:
    from app.services.onboarding_service import OnboardingService

    _add_subscription(db_session, active_user.id)
    _add_minutes(db_session, active_user.id)
    _add_phone(db_session, active_user.id)
    _add_agent_config(db_session, active_user.id, complete=False)
    await db_session.commit()

    status = await OnboardingService(db_session).get_status(active_user.id)

    assert status.phone_number == "+35315551234"
    assert status.phone_number_status == "ready"
    assert status.agent_setup_complete is False
    assert status.stage == "receptionist_setup_required"
    assert "agent_setup_incomplete" in status.blockers


@pytest.mark.anyio
async def test_get_status_returns_ready_when_receptionist_can_be_activated(
    db_session,
    active_user,
) -> None:
    from app.services.onboarding_service import OnboardingService

    _add_subscription(db_session, active_user.id)
    _add_minutes(db_session, active_user.id)
    _add_phone(db_session, active_user.id)
    _add_agent_config(db_session, active_user.id, complete=True)
    await db_session.commit()

    status = await OnboardingService(db_session).get_status(active_user.id)

    assert status.minutes_remaining == 60
    assert status.agent_setup_complete is True
    assert status.stage == "ready"
    assert status.can_activate is True
    assert status.can_route is False
    assert status.blockers == [
        "agent_disabled",
        "phone_inactive",
        "phone_projection_inactive",
    ]


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("connection_name", "is_active"),
    [
        ("app-disabled", True),
        ("app-active", False),
    ],
)
async def test_get_status_returns_routing_pending_for_inconsistent_projection(
    db_session,
    active_user,
    connection_name: str,
    is_active: bool,
) -> None:
    from app.services.onboarding_service import OnboardingService

    _add_subscription(db_session, active_user.id)
    _add_minutes(db_session, active_user.id)
    _add_phone(
        db_session,
        active_user.id,
        connection_name=connection_name,
        is_active=is_active,
    )
    _add_agent_config(db_session, active_user.id, complete=True, enabled=True)
    await db_session.commit()

    status = await OnboardingService(db_session).get_status(active_user.id)

    assert status.stage == "routing_pending"
    assert status.can_activate is True
    assert status.can_route is False


@pytest.mark.anyio
async def test_get_status_is_live_only_when_full_projection_is_active(
    db_session,
    active_user,
) -> None:
    from app.services.onboarding_service import OnboardingService

    _add_subscription(db_session, active_user.id)
    _add_minutes(db_session, active_user.id)
    _add_phone(
        db_session,
        active_user.id,
        connection_name="app-active",
        is_active=True,
    )
    _add_agent_config(db_session, active_user.id, complete=True, enabled=True)
    await db_session.commit()

    status = await OnboardingService(db_session).get_status(active_user.id)

    assert status.stage == "live"
    assert status.can_activate is True
    assert status.can_route is True
    assert status.blockers == []


@pytest.mark.anyio
async def test_retry_provisioning_delegates_to_activation_command() -> None:
    from app.services.onboarding_service import OnboardingService

    user_id = uuid4()
    pool = object()
    canonical_snapshot = object()

    class ProvisioningCommands:
        def __init__(self) -> None:
            self.calls: list[tuple[object, object]] = []

        async def retry(self, requested_user_id, *, arq_pool):
            self.calls.append((requested_user_id, arq_pool))
            return canonical_snapshot

    commands = ProvisioningCommands()
    readiness_service = type(
        "ReadinessService",
        (),
        {"provisioning_repository": object()},
    )
    result = await OnboardingService(
        readiness_service=readiness_service,
        activation_provisioning_service=commands,
    ).retry_provisioning(
        user_id,
        arq_pool=pool,
    )

    assert result is canonical_snapshot
    assert commands.calls == [(user_id, pool)]
