from datetime import UTC, datetime, timedelta
from uuid import uuid4

import phonenumbers
import pytest

from app.models.agent_config import AgentConfig
from app.models.business_profile import BusinessProfile
from app.models.customer_activation import CustomerActivation
from app.models.phone_number import PhoneNumber
from app.models.phone_number_provisioning import PhoneNumberProvisioning
from app.models.subscription import Subscription
from app.models.user import User
from app.services.activation_policy import ActivationStage
from app.services.activation_snapshot_service import (
    ActivationSnapshotService,
    ActivationSnapshotUnavailableError,
)
from app.services.routing_fingerprint import routing_fingerprint


NOW = datetime(2026, 7, 17, 10, tzinfo=UTC)
# ARCEP reserves 01 99 00 for fiction and 09 99 for technical/internal use.
ARCEP_FICTIONAL_FIXED_NUMBER = "+33199000000"
ARCEP_TECHNICAL_NUMBER = "+33999000000"


class FakeByIdRepository:
    def __init__(self, value) -> None:
        self.value = value
        self.calls: list[object] = []

    async def get_by_id(self, user_id):
        self.calls.append(user_id)
        return self.value


class FakeByUserRepository:
    def __init__(self, value) -> None:
        self.value = value
        self.calls: list[object] = []

    async def get_by_user_id(self, user_id):
        self.calls.append(user_id)
        return self.value


class FakeUsageRepository:
    def __init__(self, balance: int) -> None:
        self.balance = balance
        self.calls: list[object] = []

    async def get_current_balance(self, *, user_id) -> int:
        self.calls.append(user_id)
        return self.balance


def complete_hours() -> dict[str, dict[str, object]]:
    return {
        day: {"closed": True, "intervals": []}
        for day in (
            "monday",
            "tuesday",
            "wednesday",
            "thursday",
            "friday",
            "saturday",
            "sunday",
        )
    }


def build_records():
    user_id = uuid4()
    phone_number_id = uuid4()
    user = User(
        id=user_id,
        clerk_user_id="snapshot-user",
        email="snapshot@example.com",
        status="active",
    )
    profile = BusinessProfile(
        user_id=user_id,
        owner_name="Sam",
        business_name="Sam Plumbing",
        business_type="Plumber",
        public_description="Emergency and maintenance plumbing in Lyon.",
        timezone="Europe/Paris",
        business_hours=complete_hours(),
        existing_phone_e164="+33612345678",
        confirmed_carrier="orange",
        receptionist_name="Ava",
        faqs=[],
        content_revision=4,
        routing_revision=2,
    )
    phone = PhoneNumber(
        id=phone_number_id,
        user_id=user_id,
        e164="+33912345678",
        country_code="FR",
        provider="telnyx",
        provider_number_id="pn_snapshot",
        provider_connection_name="app-active",
        is_active=True,
    )
    activation = CustomerActivation(
        user_id=user_id,
        workflow_version=1,
        profile_confirmed_revision=4,
        profile_confirmed_at=NOW,
        provisioning_consented_at=NOW,
        verification_status="succeeded",
        forwarding_verified_at=NOW,
        go_live_approved_at=NOW,
        activated_at=NOW,
    )
    activation.verified_routing_fingerprint = routing_fingerprint(profile, phone)
    subscription = Subscription(
        user_id=user_id,
        stripe_customer_id="cus_snapshot",
        stripe_subscription_id="sub_snapshot",
        plan_tier="starter",
        status="active",
        allocated_minutes=60,
        current_period_start=datetime(2026, 7, 1, tzinfo=UTC),
        current_period_end=datetime(2026, 8, 1, tzinfo=UTC),
    )
    provisioning = PhoneNumberProvisioning(
        user_id=user_id,
        phone_number_id=phone_number_id,
        target_country_code="FR",
        status="succeeded",
        attempt_count=1,
        can_retry=False,
        last_error_payload={"secret_provider_detail": "must-not-leak"},
    )
    agent = AgentConfig(
        user_id=user_id,
        agent_name="Ava",
        business_display_name="Sam Plumbing",
        profile_projection_revision=4,
        owner_context="Sam runs Sam Plumbing.",
        system_prompt="Be concise.",
        knowledge_base="Open weekdays.",
        pipeline_mode="stt_llm_tts",
        is_enabled=True,
    )
    return user, profile, activation, subscription, provisioning, phone, agent


def build_service(*, records, balance: int = 30):
    user, profile, activation, subscription, provisioning, phone, agent = records
    repositories = {
        "user_repository": FakeByIdRepository(user),
        "business_profile_repository": FakeByUserRepository(profile),
        "activation_repository": FakeByUserRepository(activation),
        "subscription_repository": FakeByUserRepository(subscription),
        "usage_repository": FakeUsageRepository(balance),
        "provisioning_repository": FakeByUserRepository(provisioning),
        "phone_number_repository": FakeByUserRepository(phone),
        "agent_config_repository": FakeByUserRepository(agent),
    }
    return ActivationSnapshotService(**repositories), repositories


def test_activation_response_normalizes_all_naive_milestones_to_utc() -> None:
    naive = datetime(2026, 7, 18, 12, 30)
    activation = CustomerActivation(
        user_id=uuid4(),
        profile_confirmed_at=naive,
        provisioning_consented_at=naive,
        verification_window_started_at=naive,
        verification_window_expires_at=naive + timedelta(minutes=10),
        verification_status="open",
        forwarding_verified_at=naive,
        go_live_approved_at=naive,
        activated_at=naive,
    )

    response = ActivationSnapshotService._activation_response(activation, now=naive)

    assert response.profile_confirmed_at == naive.replace(tzinfo=UTC)
    assert response.provisioning_consented_at == naive.replace(tzinfo=UTC)
    assert response.verification_window_started_at == naive.replace(tzinfo=UTC)
    assert response.verification_window_expires_at == (
        naive + timedelta(minutes=10)
    ).replace(tzinfo=UTC)
    assert response.verification_status == "open"
    assert response.forwarding_verified_at == naive.replace(tzinfo=UTC)
    assert response.go_live_approved_at == naive.replace(tzinfo=UTC)
    assert response.activated_at == naive.replace(tzinfo=UTC)


@pytest.mark.anyio
async def test_get_loads_each_authoritative_row_once_and_returns_active_snapshot() -> None:
    records = build_records()
    service, repositories = build_service(records=records)
    user = records[0]

    snapshot = await service.get(user.id, now=NOW)

    assert snapshot.stage is ActivationStage.ACTIVE
    assert snapshot.workflow_version == 1
    assert snapshot.profile.business_name == "Sam Plumbing"
    assert snapshot.profile_constraints.phone_country == "FR"
    assert snapshot.activation.activated_at == NOW
    assert snapshot.activation.verification_window_started_at is None
    assert snapshot.activation.verification_window_expires_at is None
    assert snapshot.activation.verification_status == "succeeded"
    assert snapshot.billing.eligible is True
    assert snapshot.billing.minutes_remaining == 30
    assert snapshot.number.assigned_e164 == "+33912345678"
    assert snapshot.number.provider_ready is True
    assert snapshot.runtime_readiness.can_route is True
    assert snapshot.runtime_readiness.policy_version == "runtime-v5"
    assert "last_error_payload" not in snapshot.model_dump_json()
    assert "secret_provider_detail" not in snapshot.model_dump_json()
    for repository in repositories.values():
        assert repository.calls == [user.id]


@pytest.mark.anyio
async def test_provider_pending_number_order_remains_in_refreshable_provisioning_stage() -> None:
    records = list(build_records())
    activation = records[2]
    activation.verification_status = "not_started"
    activation.forwarding_verified_at = None
    activation.verified_routing_fingerprint = None
    activation.go_live_approved_at = None
    activation.activated_at = None
    provisioning = records[4]
    provisioning.status = "running"
    provisioning.phone_number_id = None
    provisioning.can_retry = False
    provisioning.last_error_reason = "existing_order_pending"
    records[5] = None
    service, _repositories = build_service(records=tuple(records))

    snapshot = await service.get(records[0].id, now=NOW)

    assert snapshot.stage is ActivationStage.PROVISIONING
    assert snapshot.number.provisioning_status == "running"
    assert snapshot.number.can_retry is False
    assert snapshot.number.provider_ready is False


@pytest.mark.anyio
async def test_consented_terminal_assignment_inconsistency_fails_explicitly() -> None:
    records = list(build_records())
    records[4].phone_number_id = uuid4()
    records[4].can_retry = True
    service, _repositories = build_service(records=tuple(records))

    snapshot = await service.get(records[0].id, now=NOW)

    assert snapshot.stage is ActivationStage.PROVISIONING_FAILED
    assert snapshot.next_action is None
    assert snapshot.blockers == ["number_assignment_inconsistent"]
    assert snapshot.number.provisioning_status == "succeeded"
    assert snapshot.number.provider_ready is False
    assert snapshot.number.can_retry is False
    assert snapshot.runtime_readiness.stage == "number_provisioning_failed"
    assert snapshot.runtime_readiness.can_route is False
    assert "number_not_provisioned" in snapshot.runtime_readiness.blockers


@pytest.mark.anyio
async def test_completed_legacy_number_advances_without_historical_consent() -> None:
    records = list(build_records())
    activation = records[2]
    activation.provisioning_consented_at = None
    activation.verification_status = "not_started"
    activation.forwarding_verified_at = None
    activation.verified_routing_fingerprint = None
    activation.go_live_approved_at = None
    activation.activated_at = None
    service, _repositories = build_service(records=tuple(records))

    snapshot = await service.get(records[0].id, now=NOW)

    assert snapshot.stage is ActivationStage.FORWARDING_REQUIRED
    assert snapshot.next_action == "configure_forwarding"
    assert snapshot.blockers == ["forwarding_not_verified"]
    assert "number_provisioned" in snapshot.completed_milestones
    assert "provisioning_consented" not in snapshot.completed_milestones
    assert snapshot.activation.provisioning_consented_at is None


@pytest.mark.anyio
async def test_legacy_completion_requires_an_exact_provisioning_phone_link() -> None:
    records = list(build_records())
    activation = records[2]
    activation.provisioning_consented_at = None
    records[4].phone_number_id = uuid4()
    service, _repositories = build_service(records=tuple(records))

    snapshot = await service.get(records[0].id, now=NOW)

    assert snapshot.stage is ActivationStage.PROVISIONING_CONSENT_REQUIRED
    assert "number_provisioned" not in snapshot.completed_milestones
    assert snapshot.number.provider_ready is False
    assert snapshot.runtime_readiness.can_route is False
    assert "number_not_provisioned" in snapshot.runtime_readiness.blockers


@pytest.mark.anyio
@pytest.mark.parametrize("provider_number_id", [None, " "])
async def test_legacy_completion_requires_a_nonblank_provider_number_identity(
    provider_number_id: str | None,
) -> None:
    records = list(build_records())
    records[2].provisioning_consented_at = None
    records[5].provider_number_id = provider_number_id
    service, _repositories = build_service(records=tuple(records))

    snapshot = await service.get(records[0].id, now=NOW)

    assert snapshot.stage is ActivationStage.PROVISIONING_CONSENT_REQUIRED
    assert "number_provisioned" not in snapshot.completed_milestones
    assert snapshot.number.provider_ready is False
    assert snapshot.runtime_readiness.can_route is False
    assert "number_not_provisioned" in snapshot.runtime_readiness.blockers


@pytest.mark.anyio
@pytest.mark.parametrize("status", [None, "queued", "running", "failed"])
async def test_incomplete_provisioning_never_uses_legacy_completion_path(
    status: str | None,
) -> None:
    records = list(build_records())
    records[2].provisioning_consented_at = None
    records[4] = None if status is None else records[4]
    if records[4] is not None:
        records[4].status = status
    service, _repositories = build_service(records=tuple(records))

    snapshot = await service.get(records[0].id, now=NOW)

    assert snapshot.stage is ActivationStage.PROVISIONING_CONSENT_REQUIRED
    assert "number_provisioned" not in snapshot.completed_milestones
    assert snapshot.number.provider_ready is False
    assert snapshot.runtime_readiness.can_route is False
    assert "number_not_provisioned" in snapshot.runtime_readiness.blockers


@pytest.mark.anyio
async def test_snapshot_forwarding_uses_stored_detected_number_type() -> None:
    records = list(build_records())
    profile = records[1]
    phone = records[5]
    profile.existing_phone_e164 = ARCEP_FICTIONAL_FIXED_NUMBER
    profile.detected_number_type = "mobile"
    phone.e164 = ARCEP_TECHNICAL_NUMBER
    service, _repositories = build_service(records=tuple(records))

    snapshot = await service.get(records[0].id, now=NOW)

    assert snapshot.forwarding is not None
    assert snapshot.forwarding.carrier == "orange"
    assert snapshot.forwarding.number_type == "mobile"
    assert snapshot.forwarding.opevo_number == ARCEP_TECHNICAL_NUMBER
    assert all(step.dial_code is None for step in snapshot.forwarding.steps)


@pytest.mark.anyio
async def test_snapshot_classifies_existing_line_when_lookup_type_is_missing() -> None:
    records = list(build_records())
    profile = records[1]
    phone = records[5]
    profile.confirmed_carrier = "sfr"
    profile.existing_phone_e164 = ARCEP_FICTIONAL_FIXED_NUMBER
    profile.detected_number_type = None
    phone.e164 = ARCEP_TECHNICAL_NUMBER
    service, _repositories = build_service(records=tuple(records))

    snapshot = await service.get(records[0].id, now=NOW)

    assert snapshot.forwarding is not None
    assert snapshot.forwarding.number_type == "fixed"
    assert snapshot.forwarding.step("unanswered").dial_code == "*61*0999000000#"


def test_local_classification_keeps_ambiguous_line_types_safe(monkeypatch) -> None:
    monkeypatch.setattr(
        phonenumbers,
        "number_type",
        lambda _number: phonenumbers.PhoneNumberType.FIXED_LINE_OR_MOBILE,
    )

    assert (
        ActivationSnapshotService._classify_existing_number(
            ARCEP_FICTIONAL_FIXED_NUMBER
        )
        == "unknown"
    )


@pytest.mark.anyio
@pytest.mark.parametrize("missing_record", ["carrier", "phone"])
async def test_snapshot_omits_forwarding_until_carrier_and_number_exist(
    missing_record: str,
) -> None:
    records = list(build_records())
    if missing_record == "carrier":
        records[1].confirmed_carrier = None
    else:
        records[5] = None
    service, _repositories = build_service(records=tuple(records))

    snapshot = await service.get(records[0].id, now=NOW)

    assert snapshot.forwarding is None


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("user_status", "balance", "runtime_blocker"),
    [
        ("deactivating", 30, "account_deactivating"),
        ("inactive", 30, "account_inactive"),
        ("active", 0, "minutes_exhausted"),
    ],
)
async def test_runtime_failures_do_not_change_paid_subscription_eligibility(
    user_status: str,
    balance: int,
    runtime_blocker: str,
) -> None:
    records = build_records()
    records[0].status = user_status
    service, _repositories = build_service(records=records, balance=balance)

    snapshot = await service.get(records[0].id, now=NOW)

    assert snapshot.billing.eligible is True
    assert snapshot.stage is ActivationStage.RUNTIME_PAUSED
    assert runtime_blocker in snapshot.blockers
    assert runtime_blocker in snapshot.runtime_readiness.blockers


@pytest.mark.anyio
async def test_get_returns_safe_profile_required_snapshot_for_missing_domain_rows() -> None:
    user = User(
        id=uuid4(),
        clerk_user_id="new-user",
        email="new@example.com",
        status="active",
    )
    empty_records = (user, None, None, None, None, None, None)
    service, _repositories = build_service(records=empty_records, balance=0)

    snapshot = await service.get(user.id, now=NOW)

    assert snapshot.stage is ActivationStage.PROFILE_REQUIRED
    assert snapshot.profile.owner_name is None
    assert snapshot.profile.content_revision == 1
    assert snapshot.activation.profile_confirmed_at is None
    assert snapshot.billing.eligible is False
    assert snapshot.number.assigned_e164 is None
    assert snapshot.blockers == ["profile_not_confirmed"]


@pytest.mark.anyio
async def test_get_rejects_missing_authoritative_user_before_loading_aggregates() -> None:
    user_id = uuid4()
    empty_records = (None, None, None, None, None, None, None)
    service, repositories = build_service(records=empty_records, balance=0)

    with pytest.raises(ActivationSnapshotUnavailableError):
        await service.get(user_id, now=NOW)

    assert repositories["user_repository"].calls == [user_id]
    for name, repository in repositories.items():
        if name != "user_repository":
            assert repository.calls == []


@pytest.mark.anyio
async def test_expired_verification_window_is_not_reported_open() -> None:
    records = list(build_records())
    activation = records[2]
    activation.forwarding_verified_at = None
    activation.verified_routing_fingerprint = None
    activation.go_live_approved_at = None
    activation.activated_at = None
    activation.verification_window_started_at = datetime(2026, 7, 17, 9, tzinfo=UTC)
    activation.verification_window_expires_at = NOW
    activation.verification_status = "open"
    service, _repositories = build_service(records=tuple(records))

    snapshot = await service.get(records[0].id, now=NOW)

    assert snapshot.stage is ActivationStage.FORWARDING_REQUIRED
    assert snapshot.activation.verification_window_started_at == datetime(
        2026, 7, 17, 9, tzinfo=UTC
    )
    assert snapshot.activation.verification_window_expires_at == NOW
    assert snapshot.activation.verification_status == "expired"


@pytest.mark.anyio
async def test_claimed_window_remains_resumable_during_completion_grace() -> None:
    records = list(build_records())
    activation = records[2]
    activation.forwarding_verified_at = None
    activation.verified_routing_fingerprint = None
    activation.go_live_approved_at = None
    activation.activated_at = None
    activation.verification_window_started_at = NOW - timedelta(minutes=10)
    activation.verification_window_expires_at = NOW
    activation.verification_status = "claimed"
    service, _repositories = build_service(records=tuple(records))

    snapshot = await service.get(records[0].id, now=NOW + timedelta(minutes=1))

    assert snapshot.stage is ActivationStage.FORWARDING_REQUIRED
    assert snapshot.activation.verification_status == "claimed"


@pytest.mark.anyio
async def test_claimed_window_is_reported_expired_at_grace_boundary() -> None:
    records = list(build_records())
    activation = records[2]
    activation.forwarding_verified_at = None
    activation.verified_routing_fingerprint = None
    activation.go_live_approved_at = None
    activation.activated_at = None
    activation.verification_window_started_at = NOW - timedelta(minutes=10)
    activation.verification_window_expires_at = NOW
    activation.verification_status = "claimed"
    service, _repositories = build_service(records=tuple(records))

    snapshot = await service.get(records[0].id, now=NOW + timedelta(minutes=2))

    assert snapshot.activation.verification_status == "expired"
