from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.activation_event import ActivationEvent
from app.models.agent_config import AgentConfig
from app.models.business_profile import BusinessProfile
from app.models.customer_activation import CustomerActivation
from app.models.outbox_event import OutboxEvent
from app.models.phone_number import PhoneNumber
from app.models.phone_number_provisioning import PhoneNumberProvisioning
from app.models.subscription import Subscription
from app.models.usage_ledger import UsageLedger
from app.schemas.business_profile import WEEKDAYS
from app.services.activation_go_live_service import (
    ActivationGoLiveBlockedError,
    ActivationGoLiveService,
    fail_current_go_live_attempt,
)
from app.services.forwarding_verification_service import as_utc
from app.services.routing_fingerprint import routing_fingerprint
from app.workers.jobs.outbox_delivery import OutboxDeliveryError, outbox_delivery_job
from app.workers.jobs.outbox_topics import deliver_phone_routing


FIXED_NOW = datetime(2026, 7, 18, 12, 0, tzinfo=UTC)
SOURCE_NUMBER = "+33199000100"
PRESVO_NUMBER = "+33999000100"


@pytest.fixture(autouse=True)
def _activation_flow_enabled(monkeypatch: pytest.MonkeyPatch):
    from app.core.config import get_settings

    monkeypatch.setenv("ACTIVATION_FLOW_ENABLED", "true")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


class _Pool:
    def __init__(self) -> None:
        self.jobs: list[tuple[str, dict]] = []

    async def enqueue_job(self, name: str, payload: dict) -> None:
        self.jobs.append((name, payload))


class _RoutingProvider:
    def __init__(self, *, failure: str | None = None) -> None:
        self.failure = failure
        self.enabled: list[str] = []
        self.disabled: list[str] = []

    async def enable_number(self, *, provider_number_id: str) -> str:
        self.enabled.append(provider_number_id)
        if self.failure is not None:
            from app.providers.telephony.base import TelephonyProviderError

            raise TelephonyProviderError(self.failure)
        return "app-active"

    async def disable_number(self, *, provider_number_id: str) -> str:
        self.disabled.append(provider_number_id)
        return "app-disabled"


async def _seed_ready_customer(db_session, user):
    user.country_code = "FR"
    profile = BusinessProfile(
        user_id=user.id,
        owner_name="Camille Martin",
        business_name="Atelier Martin",
        business_type="Plomberie",
        public_description="Dépannage et installation de plomberie.",
        timezone="Europe/Paris",
        business_hours={
            day: {"closed": True, "intervals": []} for day in WEEKDAYS
        },
        existing_phone_e164=SOURCE_NUMBER,
        confirmed_carrier="orange",
        receptionist_name="Léa",
        content_revision=3,
        routing_revision=2,
    )
    activation = CustomerActivation(
        user_id=user.id,
        profile_confirmed_revision=profile.content_revision,
        profile_confirmed_at=FIXED_NOW - timedelta(hours=2),
        provisioning_consented_at=FIXED_NOW - timedelta(hours=1),
        verification_status="succeeded",
        forwarding_verified_at=FIXED_NOW - timedelta(minutes=30),
    )
    phone = PhoneNumber(
        user_id=user.id,
        e164=PRESVO_NUMBER,
        country_code="FR",
        provider="fake",
        provider_number_id="fake-number-go-live",
        provider_connection_name="app-disabled",
        is_active=False,
    )
    config = AgentConfig(
        user_id=user.id,
        agent_name="Léa",
        business_display_name="Atelier Martin",
        owner_context="Camille Martin, owner of Atelier Martin",
        system_prompt="Answer missed calls professionally.",
        knowledge_base="Open weekdays.",
        is_enabled=False,
        profile_projection_revision=profile.content_revision,
    )
    db_session.add_all(
        [
            profile,
            activation,
            phone,
            config,
            Subscription(
                user_id=user.id,
                stripe_customer_id="cus-go-live",
                stripe_subscription_id="sub-go-live",
                plan_tier="starter",
                status="active",
                allocated_minutes=60,
                current_period_start=FIXED_NOW - timedelta(days=1),
                current_period_end=FIXED_NOW + timedelta(days=29),
            ),
            UsageLedger(
                user_id=user.id,
                event_type="invoice_paid_reset",
                source_id="invoice-go-live",
                minutes_delta=60,
                balance_after=60,
            ),
        ]
    )
    await db_session.flush()
    activation.verified_routing_fingerprint = routing_fingerprint(profile, phone)
    db_session.add(
        PhoneNumberProvisioning(
            user_id=user.id,
            phone_number_id=phone.id,
            target_country_code="FR",
            status="succeeded",
            attempt_count=1,
            can_retry=False,
            provider_operation_key=f"activation:phone.provision:{activation.id}",
        )
    )
    await db_session.commit()
    return profile, activation, phone, config


@pytest.mark.anyio
async def test_go_live_requires_current_forwarding_verification(
    db_session,
    active_user,
) -> None:
    _profile, activation, _phone, _config = await _seed_ready_customer(
        db_session, active_user
    )
    activation.verified_routing_fingerprint = None
    activation.forwarding_verified_at = None
    await db_session.commit()

    with pytest.raises(ActivationGoLiveBlockedError) as error:
        await ActivationGoLiveService(
            db_session,
            now_provider=lambda: FIXED_NOW,
        ).go_live(active_user.id, arq_pool=None)

    assert error.value.blockers == ("forwarding_not_verified",)
    assert await db_session.scalar(select(func.count()).select_from(OutboxEvent)) == 0


@pytest.mark.anyio
async def test_go_live_records_one_pending_attempt_and_returns_activating(
    db_session,
    active_user,
) -> None:
    _profile, activation, _phone, config = await _seed_ready_customer(
        db_session, active_user
    )
    pool = _Pool()
    service = ActivationGoLiveService(
        db_session,
        now_provider=lambda: FIXED_NOW,
    )

    snapshot = await service.go_live(active_user.id, arq_pool=pool)

    await db_session.refresh(activation)
    await db_session.refresh(config)
    outbox = list((await db_session.scalars(select(OutboxEvent))).all())
    events = list((await db_session.scalars(select(ActivationEvent))).all())
    assert snapshot.stage == "activating"
    assert as_utc(activation.go_live_requested_at) == FIXED_NOW
    assert as_utc(activation.go_live_approved_at) == FIXED_NOW
    assert config.is_enabled is True
    assert len(outbox) == 1
    assert outbox[0].topic == "phone.enable"
    assert outbox[0].payload == {"user_id": str(active_user.id)}
    assert str(active_user.id) not in outbox[0].idempotency_key
    assert [event.event_type for event in events] == ["go_live_requested"]
    assert pool.jobs == [("outbox_delivery_job", {})]


@pytest.mark.anyio
async def test_pending_go_live_replay_is_idempotent_and_does_not_wake_again(
    db_session,
    active_user,
) -> None:
    await _seed_ready_customer(db_session, active_user)
    pool = _Pool()
    service = ActivationGoLiveService(
        db_session,
        now_provider=lambda: FIXED_NOW,
    )

    first = await service.go_live(active_user.id, arq_pool=pool)
    second = await service.go_live(active_user.id, arq_pool=pool)

    assert first.stage == second.stage == "activating"
    assert await db_session.scalar(select(func.count()).select_from(OutboxEvent)) == 1
    assert await db_session.scalar(select(func.count()).select_from(ActivationEvent)) == 1
    assert pool.jobs == [("outbox_delivery_job", {})]


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("case", "expected_blockers"),
    [
        ("profile_incomplete", ("business_profile_incomplete",)),
        ("projection_stale", ("profile_projection_stale",)),
        ("subscription_ineligible", ("subscription_status_ineligible",)),
        ("subscription_period_missing", ("subscription_period_missing",)),
        ("subscription_period_inactive", ("subscription_period_inactive",)),
        ("minutes_exhausted", ("minutes_exhausted",)),
        ("provider_id_missing", ("phone_provider_id_missing",)),
    ],
)
async def test_go_live_returns_stable_prerequisite_blockers(
    db_session,
    active_user,
    case: str,
    expected_blockers: tuple[str, ...],
) -> None:
    profile, _activation, phone, config = await _seed_ready_customer(
        db_session, active_user
    )
    subscription = await db_session.scalar(select(Subscription))
    usage = await db_session.scalar(select(UsageLedger))
    assert subscription is not None
    assert usage is not None
    if case == "profile_incomplete":
        profile.business_name = None
    elif case == "projection_stale":
        config.profile_projection_revision = profile.content_revision - 1
    elif case == "subscription_ineligible":
        subscription.status = "canceled"
    elif case == "subscription_period_missing":
        subscription.current_period_end = None
    elif case == "subscription_period_inactive":
        subscription.current_period_end = FIXED_NOW - timedelta(seconds=1)
    elif case == "minutes_exhausted":
        usage.balance_after = 0
    elif case == "provider_id_missing":
        phone.provider_number_id = None
    await db_session.commit()

    with pytest.raises(ActivationGoLiveBlockedError) as error:
        await ActivationGoLiveService(
            db_session,
            now_provider=lambda: FIXED_NOW,
        ).go_live(active_user.id, arq_pool=None)

    assert error.value.blockers == expected_blockers
    assert await db_session.scalar(select(func.count()).select_from(OutboxEvent)) == 0


@pytest.mark.anyio
async def test_provider_projection_activates_only_the_current_attempt(
    db_session,
    active_user,
) -> None:
    _profile, activation, phone, _config = await _seed_ready_customer(
        db_session, active_user
    )
    service = ActivationGoLiveService(
        db_session,
        now_provider=lambda: FIXED_NOW,
    )
    await service.go_live(active_user.id, arq_pool=None)
    event = (await db_session.scalars(select(OutboxEvent))).one()
    event_id = event.id
    activation_id = activation.id
    phone_id = phone.id
    user_id = active_user.id
    await db_session.commit()
    provider = _RoutingProvider()
    session_factory = async_sessionmaker(db_session.bind, expire_on_commit=False)

    await deliver_phone_routing(
        {
            "session_factory": session_factory,
            "telephony_provider": provider,
            "routing_now": lambda: FIXED_NOW + timedelta(seconds=2),
        },
        event,
    )

    db_session.expire_all()
    stored_activation = await db_session.get(CustomerActivation, activation_id)
    stored_phone = await db_session.get(PhoneNumber, phone_id)
    events = list(
        (
            await db_session.scalars(
                select(ActivationEvent).order_by(ActivationEvent.created_at)
            )
        ).all()
    )
    assert stored_activation is not None
    assert stored_phone is not None
    assert stored_activation.activated_at is not None
    assert as_utc(stored_activation.activated_at) == FIXED_NOW + timedelta(seconds=2)
    assert stored_phone.is_active is True
    assert stored_phone.provider_connection_name == "app-active"
    assert provider.enabled == ["fake-number-go-live"]
    assert [item.event_type for item in events] == [
        "go_live_requested",
        "go_live_succeeded",
    ]

    await db_session.commit()
    replay = await service.go_live(user_id, arq_pool=None)
    assert replay.stage == "active"
    assert await db_session.scalar(select(func.count()).select_from(OutboxEvent)) == 1
    current_event = await db_session.get(OutboxEvent, event_id)
    assert current_event is not None
    assert await fail_current_go_live_attempt(db_session, event=current_event) is False
    await db_session.refresh(stored_activation)
    assert stored_activation.activated_at is not None


@pytest.mark.anyio
async def test_terminal_failure_allows_new_attempt_and_stale_event_is_obsolete(
    db_session,
    active_user,
) -> None:
    _profile, activation, _phone, config = await _seed_ready_customer(
        db_session, active_user
    )
    service = ActivationGoLiveService(
        db_session,
        now_provider=lambda: FIXED_NOW,
    )
    await service.go_live(active_user.id, arq_pool=None)
    first_event = (await db_session.scalars(select(OutboxEvent))).one()

    assert await fail_current_go_live_attempt(db_session, event=first_event) is True
    await db_session.commit()
    await db_session.refresh(activation)
    await db_session.refresh(config)
    assert activation.go_live_requested_at is None
    assert activation.go_live_approved_at is None
    assert activation.last_failure_code == "routing_provider_terminal"
    assert config.is_enabled is False

    await service.go_live(active_user.id, arq_pool=None)
    events = list(
        (
            await db_session.scalars(select(OutboxEvent).order_by(OutboxEvent.created_at))
        ).all()
    )
    assert len(events) == 2
    second_event = next(item for item in events if item.id != first_event.id)
    assert first_event.idempotency_key != second_event.idempotency_key
    await db_session.refresh(activation)
    newer_requested_at = activation.go_live_requested_at

    assert await fail_current_go_live_attempt(db_session, event=first_event) is False
    await db_session.commit()
    provider = _RoutingProvider()
    session_factory = async_sessionmaker(db_session.bind, expire_on_commit=False)
    await deliver_phone_routing(
        {"session_factory": session_factory, "telephony_provider": provider},
        first_event,
    )

    await db_session.refresh(activation)
    assert activation.go_live_requested_at == newer_requested_at
    assert activation.go_live_approved_at is not None
    assert provider.enabled == []


@pytest.mark.anyio
async def test_terminal_provider_error_atomically_returns_to_ready_to_activate(
    db_session,
    active_user,
) -> None:
    _profile, activation, phone, config = await _seed_ready_customer(
        db_session, active_user
    )
    service = ActivationGoLiveService(
        db_session,
        now_provider=lambda: FIXED_NOW,
    )
    await service.go_live(active_user.id, arq_pool=None)
    activation_id = activation.id
    phone_id = phone.id
    config_id = config.id
    user_id = active_user.id
    await db_session.commit()
    session_factory = async_sessionmaker(db_session.bind, expire_on_commit=False)

    result = await outbox_delivery_job(
        {
            "session_factory": session_factory,
            "outbox_handlers": {"phone.enable": deliver_phone_routing},
            "telephony_provider": _RoutingProvider(failure="provider_terminal"),
            "outbox_now": lambda: datetime.now(UTC) + timedelta(seconds=5),
            "outbox_terminal_failure_metric": lambda *_args: None,
        }
    )

    assert result == {"claimed": 1, "delivered": 0, "retried": 0, "failed": 1}
    db_session.expire_all()
    stored_activation = await db_session.get(CustomerActivation, activation_id)
    stored_phone = await db_session.get(PhoneNumber, phone_id)
    stored_config = await db_session.get(AgentConfig, config_id)
    outbox = (await db_session.scalars(select(OutboxEvent))).one()
    assert stored_activation is not None
    assert stored_phone is not None
    assert stored_config is not None
    assert stored_activation.go_live_requested_at is None
    assert stored_activation.go_live_approved_at is None
    assert stored_activation.activated_at is None
    assert stored_activation.last_failure_code == "routing_provider_terminal"
    assert stored_phone.is_active is False
    assert stored_phone.provider_connection_name == "app-disabled"
    assert stored_config.is_enabled is False
    assert outbox.status == "failed"
    snapshot = await service.snapshot_service.get(user_id)
    assert snapshot.stage == "ready_to_activate"


@pytest.mark.anyio
async def test_retry_exhaustion_performs_the_same_safe_failure_transition(
    db_session,
    active_user,
) -> None:
    _profile, activation, _phone, _config = await _seed_ready_customer(
        db_session, active_user
    )
    await ActivationGoLiveService(
        db_session,
        now_provider=lambda: FIXED_NOW,
    ).go_live(active_user.id, arq_pool=None)
    event = (await db_session.scalars(select(OutboxEvent))).one()
    activation_id = activation.id
    event_id = event.id
    await db_session.commit()
    session_factory = async_sessionmaker(db_session.bind, expire_on_commit=False)
    current_time = datetime.now(UTC) + timedelta(seconds=5)

    async def retryable(_ctx: dict, _event: OutboxEvent) -> None:
        raise OutboxDeliveryError("provider_retryable", retryable=True)

    from app.workers.jobs.outbox_delivery import OUTBOX_RETRY_DELAYS

    for delay in OUTBOX_RETRY_DELAYS:
        result = await outbox_delivery_job(
            {
                "session_factory": session_factory,
                "outbox_handlers": {"phone.enable": retryable},
                "outbox_now": lambda: current_time,
            }
        )
        assert result["retried"] == 1
        current_time += delay
    result = await outbox_delivery_job(
        {
            "session_factory": session_factory,
            "outbox_handlers": {"phone.enable": retryable},
            "outbox_now": lambda: current_time,
            "outbox_terminal_failure_metric": lambda *_args: None,
        }
    )

    assert result["failed"] == 1
    db_session.expire_all()
    stored = await db_session.get(CustomerActivation, activation_id)
    stored_event = await db_session.get(OutboxEvent, event_id)
    assert stored is not None
    assert stored_event is not None
    assert stored.go_live_requested_at is None
    assert stored.last_failure_code == "routing_provider_terminal"
    assert stored_event.status == "failed"


@pytest.mark.anyio
async def test_current_attempt_fails_safely_if_phone_disappears_before_delivery(
    db_session,
    active_user,
) -> None:
    _profile, activation, phone, _config = await _seed_ready_customer(
        db_session, active_user
    )
    await ActivationGoLiveService(
        db_session,
        now_provider=lambda: FIXED_NOW,
    ).go_live(active_user.id, arq_pool=None)
    provisioning = await db_session.scalar(select(PhoneNumberProvisioning))
    assert provisioning is not None
    provisioning.phone_number_id = None
    await db_session.flush()
    await db_session.delete(phone)
    activation_id = activation.id
    await db_session.commit()
    session_factory = async_sessionmaker(db_session.bind, expire_on_commit=False)

    result = await outbox_delivery_job(
        {
            "session_factory": session_factory,
            "outbox_handlers": {"phone.enable": deliver_phone_routing},
            "telephony_provider": _RoutingProvider(),
            "outbox_now": lambda: datetime.now(UTC) + timedelta(seconds=5),
            "outbox_terminal_failure_metric": lambda *_args: None,
        }
    )

    assert result["failed"] == 1
    db_session.expire_all()
    stored = await db_session.get(CustomerActivation, activation_id)
    assert stored is not None
    assert stored.go_live_requested_at is None
    assert stored.last_failure_code == "routing_provider_terminal"
