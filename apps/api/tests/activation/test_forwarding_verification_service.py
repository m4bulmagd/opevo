from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID

import pytest
from sqlalchemy import func, select

from app.models.activation_event import ActivationEvent
from app.models.business_profile import BusinessProfile
from app.models.customer_activation import CustomerActivation
from app.models.phone_number import PhoneNumber
from app.models.phone_number_provisioning import PhoneNumberProvisioning
from app.services.forwarding_verification_service import (
    ForwardingVerificationConflictError,
    ForwardingVerificationService,
    as_utc,
)
from app.services.account_access_policy import AccountStateBlockedError
from app.services.routing_fingerprint import routing_fingerprint


FIXED_NOW = datetime(2026, 7, 18, 10, 0, tzinfo=UTC)
# ARCEP-reserved French test ranges: 019900 for source lines, 099900 for
# technical/internal lines. These numbers cannot identify a real subscriber.
SOURCE_NUMBER = "+33199000000"
ALTERNATE_SOURCE_NUMBER = "+33199000001"
OPEVO_NUMBER = "+33999000000"
ALTERNATE_OPEVO_NUMBER = "+33999000001"
WRONG_OPEVO_NUMBER = "+33999000002"


def _service(db_session, *, now: datetime = FIXED_NOW) -> ForwardingVerificationService:
    return ForwardingVerificationService(db_session, now_provider=lambda: now)


async def _seed_provisioned_customer(
    db_session,
    user,
) -> tuple[BusinessProfile, CustomerActivation, PhoneNumber, PhoneNumberProvisioning]:
    user.country_code = "FR"
    profile = BusinessProfile(
        user_id=user.id,
        owner_name="Camille Martin",
        business_name="Atelier Martin",
        business_type="Plomberie",
        public_description="Dépannage et installation de plomberie.",
        timezone="Europe/Paris",
        business_hours={"monday": {"closed": False, "intervals": []}},
        existing_phone_e164=SOURCE_NUMBER,
        confirmed_carrier="orange",
        receptionist_name="Léa",
        content_revision=3,
        routing_revision=2,
    )
    activation = CustomerActivation(
        user_id=user.id,
        profile_confirmed_revision=3,
        profile_confirmed_at=FIXED_NOW - timedelta(hours=1),
        provisioning_consented_at=FIXED_NOW - timedelta(minutes=30),
    )
    phone = PhoneNumber(
        user_id=user.id,
        e164=OPEVO_NUMBER,
        country_code="FR",
        provider="fake",
        provider_number_id="fake_number_owner",
        provider_connection_name="fake-connection",
        is_active=False,
    )
    db_session.add_all([profile, activation, phone])
    await db_session.flush()
    provisioning = PhoneNumberProvisioning(
        user_id=user.id,
        phone_number_id=phone.id,
        target_country_code="FR",
        status="succeeded",
        attempt_count=1,
        can_retry=False,
        provider_operation_key=f"activation:phone.provision:{activation.id}",
    )
    db_session.add(provisioning)
    await db_session.commit()
    return profile, activation, phone, provisioning


async def _events(db_session, event_type: str) -> list[ActivationEvent]:
    return list(
        (
            await db_session.scalars(
                select(ActivationEvent).where(
                    ActivationEvent.event_type == event_type
                )
            )
        ).all()
    )


@pytest.mark.anyio
async def test_window_is_exactly_ten_minutes_and_records_one_safe_event(
    db_session,
    active_user,
) -> None:
    await _seed_provisioned_customer(db_session, active_user)

    activation = await _service(db_session).open_window(active_user.id)

    assert activation.verification_window_started_at == FIXED_NOW
    assert activation.verification_window_expires_at == FIXED_NOW + timedelta(
        minutes=10
    )
    assert activation.verification_status == "open"
    events = await _events(db_session, "verification_window_opened")
    assert len(events) == 1
    assert events[0].event_metadata == {}
    assert str(active_user.id) not in events[0].idempotency_key


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("case", "expected_code"),
    [
        ("user_inactive", "account_inactive"),
        ("profile_unconfirmed", "profile_not_confirmed"),
        ("profile_missing_carrier", "profile_incomplete"),
        ("phone_not_ready", "phone_not_ready"),
        ("provisioning_missing", "provisioning_not_succeeded"),
        ("provisioning_failed", "provisioning_not_succeeded"),
        ("provisioning_mismatch", "provisioning_state_conflict"),
    ],
)
async def test_open_requires_current_provider_ready_provisioned_state(
    db_session,
    active_user,
    case: str,
    expected_code: str,
) -> None:
    profile, activation, phone, provisioning = await _seed_provisioned_customer(
        db_session, active_user
    )
    if case == "user_inactive":
        active_user.status = "inactive"
    elif case == "profile_unconfirmed":
        activation.profile_confirmed_at = None
    elif case == "profile_missing_carrier":
        profile.confirmed_carrier = None
    elif case == "phone_not_ready":
        phone.provider_number_id = None
    elif case == "provisioning_missing":
        await db_session.delete(provisioning)
    elif case == "provisioning_failed":
        provisioning.status = "failed"
    elif case == "provisioning_mismatch":
        provisioning.phone_number_id = None
    await db_session.commit()

    with pytest.raises(
        (ForwardingVerificationConflictError, AccountStateBlockedError)
    ) as exc_info:
        await _service(db_session).open_window(active_user.id)

    assert exc_info.value.code == expected_code
    assert await db_session.scalar(
        select(func.count()).select_from(ActivationEvent)
    ) == 0


@pytest.mark.anyio
@pytest.mark.parametrize("existing_status", ["open", "claimed"])
async def test_duplicate_open_is_a_non_mutating_stable_conflict(
    db_session,
    active_user,
    existing_status: str,
) -> None:
    _profile, activation, _phone, _provisioning = (
        await _seed_provisioned_customer(db_session, active_user)
    )
    activation.verification_window_started_at = FIXED_NOW - timedelta(minutes=1)
    activation.verification_window_expires_at = FIXED_NOW + timedelta(minutes=9)
    activation.verification_status = existing_status
    activation.verification_session_id = (
        "existing-session" if existing_status == "claimed" else None
    )
    activation.verification_claimed_at = (
        FIXED_NOW - timedelta(seconds=10) if existing_status == "claimed" else None
    )
    await db_session.commit()
    before = (
        as_utc(activation.verification_window_started_at),
        as_utc(activation.verification_window_expires_at),
        activation.verification_session_id,
        (
            as_utc(activation.verification_claimed_at)
            if activation.verification_claimed_at is not None
            else None
        ),
    )

    with pytest.raises(ForwardingVerificationConflictError) as exc_info:
        await _service(db_session).open_window(active_user.id)

    assert exc_info.value.code == "verification_window_already_open"
    await db_session.refresh(activation)
    assert (
        as_utc(activation.verification_window_started_at),
        as_utc(activation.verification_window_expires_at),
        activation.verification_session_id,
        (
            as_utc(activation.verification_claimed_at)
            if activation.verification_claimed_at is not None
            else None
        ),
    ) == before
    assert await _events(db_session, "verification_window_opened") == []


@pytest.mark.anyio
async def test_current_success_cannot_be_reopened_or_mutated(
    db_session,
    active_user,
) -> None:
    profile, activation, phone, _provisioning = await _seed_provisioned_customer(
        db_session, active_user
    )
    current_fingerprint = routing_fingerprint(profile, phone)
    activation.verification_status = "succeeded"
    activation.verification_session_id = "succeeded-session"
    activation.verification_routing_fingerprint = current_fingerprint
    activation.verified_routing_fingerprint = current_fingerprint
    activation.forwarding_verified_at = FIXED_NOW - timedelta(minutes=1)
    await db_session.commit()

    with pytest.raises(ForwardingVerificationConflictError) as exc_info:
        await _service(db_session).open_window(active_user.id)

    assert exc_info.value.code == "verification_already_succeeded"
    await db_session.refresh(activation)
    assert activation.verification_session_id == "succeeded-session"
    assert activation.forwarding_verified_at is not None
    assert as_utc(activation.forwarding_verified_at) == FIXED_NOW - timedelta(
        minutes=1
    )


@pytest.mark.anyio
async def test_stale_success_can_be_reopened_after_assigned_number_changes(
    db_session,
    active_user,
) -> None:
    profile, activation, phone, _provisioning = await _seed_provisioned_customer(
        db_session, active_user
    )
    verified_fingerprint = routing_fingerprint(profile, phone)
    activation.verification_status = "succeeded"
    activation.verification_session_id = "stale-succeeded-session"
    activation.verification_routing_fingerprint = verified_fingerprint
    activation.verified_routing_fingerprint = verified_fingerprint
    activation.forwarding_verified_at = FIXED_NOW - timedelta(minutes=1)
    phone.e164 = ALTERNATE_OPEVO_NUMBER
    await db_session.commit()

    reopened = await _service(db_session).open_window(active_user.id)

    assert reopened.verification_status == "open"
    assert reopened.verification_window_started_at == FIXED_NOW
    assert reopened.verification_session_id is None
    assert reopened.verified_routing_fingerprint is None
    assert reopened.forwarding_verified_at is None
    assert len(await _events(db_session, "verification_window_opened")) == 1


@pytest.mark.anyio
async def test_content_only_edit_after_confirmation_remains_eligible_to_open(
    db_session,
    active_user,
) -> None:
    profile, activation, _phone, _provisioning = (
        await _seed_provisioned_customer(db_session, active_user)
    )
    confirmed_revision = activation.profile_confirmed_revision
    routing_revision = profile.routing_revision
    profile.public_description = "Dépannage, installation et conseil."
    profile.content_revision += 1
    await db_session.commit()

    opened = await _service(db_session).open_window(active_user.id)

    assert activation.profile_confirmed_revision == confirmed_revision
    assert profile.routing_revision == routing_revision
    assert opened.verification_status == "open"
    assert opened.verification_window_started_at == FIXED_NOW


@pytest.mark.anyio
@pytest.mark.parametrize("prior_status", ["failed", "expired", "invalidated"])
async def test_reopen_resets_stale_verification_state(
    db_session,
    active_user,
    prior_status: str,
) -> None:
    _profile, activation, _phone, _provisioning = (
        await _seed_provisioned_customer(db_session, active_user)
    )
    activation.verification_status = prior_status
    activation.verification_session_id = "stale-session"
    activation.verification_claimed_at = FIXED_NOW - timedelta(days=1)
    activation.verification_dispatch_id = "stale-dispatch"
    activation.verification_routing_fingerprint = "a" * 64
    activation.verified_routing_fingerprint = "b" * 64
    activation.forwarding_verified_at = FIXED_NOW - timedelta(days=1)
    activation.last_failure_code = "internal_provider_detail"
    await db_session.commit()

    reopened = await _service(db_session).open_window(active_user.id)

    assert reopened.verification_status == "open"
    assert reopened.verification_session_id is None
    assert reopened.verification_claimed_at is None
    assert reopened.verification_dispatch_id is None
    assert reopened.verification_routing_fingerprint is None
    assert reopened.verified_routing_fingerprint is None
    assert reopened.forwarding_verified_at is None
    assert reopened.last_failure_code is None


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("prior_status", "expired_at"),
    [
        ("open", FIXED_NOW - timedelta(seconds=1)),
        ("claimed", FIXED_NOW - timedelta(minutes=2)),
    ],
)
async def test_logically_expired_window_is_persisted_then_reopened_atomically(
    db_session,
    active_user,
    prior_status: str,
    expired_at: datetime,
) -> None:
    _profile, activation, _phone, _provisioning = (
        await _seed_provisioned_customer(db_session, active_user)
    )
    activation.verification_window_started_at = expired_at - timedelta(minutes=10)
    activation.verification_window_expires_at = expired_at
    activation.verification_status = prior_status
    if prior_status == "claimed":
        activation.verification_session_id = "stale-claimed-session"
        activation.verification_claimed_at = expired_at - timedelta(minutes=1)
    await db_session.commit()

    reopened = await _service(db_session).open_window(active_user.id)

    assert reopened.verification_status == "open"
    assert reopened.verification_window_started_at == FIXED_NOW
    assert reopened.verification_window_expires_at == FIXED_NOW + timedelta(
        minutes=10
    )
    assert reopened.verification_session_id is None
    assert len(await _events(db_session, "verification_window_expired")) == 1
    assert len(await _events(db_session, "verification_window_opened")) == 1


@pytest.mark.anyio
async def test_claim_normalizes_french_number_and_persists_single_use_identity(
    db_session,
    active_user,
) -> None:
    profile, activation, phone, _provisioning = await _seed_provisioned_customer(
        db_session, active_user
    )
    await _service(db_session).open_window(active_user.id)

    claim = await _service(db_session).claim(
        called_number="09 99 00 00 00",
        room_name="verification-room-1",
    )

    await db_session.refresh(activation)
    assert UUID(claim.session_id)
    assert claim.user_id == active_user.id
    assert claim.room_name == "verification-room-1"
    assert activation.verification_session_id == claim.session_id
    assert activation.verification_claimed_at is not None
    assert as_utc(activation.verification_claimed_at) == FIXED_NOW
    assert activation.verification_status == "claimed"
    assert activation.verification_routing_fingerprint == routing_fingerprint(
        profile, phone
    )
    events = await _events(db_session, "verification_window_claimed")
    assert len(events) == 1
    assert events[0].event_metadata == {"room_name": "verification-room-1"}


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("called_number", "at", "expected_code"),
    [
        ("not-a-number", FIXED_NOW, "verification_called_number_invalid"),
        (WRONG_OPEVO_NUMBER, FIXED_NOW, "verification_window_not_found"),
        (
            OPEVO_NUMBER,
            FIXED_NOW + timedelta(minutes=10),
            "verification_window_expired",
        ),
        (
            OPEVO_NUMBER,
            FIXED_NOW - timedelta(microseconds=1),
            "verification_window_not_open",
        ),
    ],
)
async def test_claim_rejects_malformed_wrong_and_outside_half_open_window(
    db_session,
    active_user,
    called_number: str,
    at: datetime,
    expected_code: str,
) -> None:
    await _seed_provisioned_customer(db_session, active_user)
    await _service(db_session).open_window(active_user.id)

    with pytest.raises(ForwardingVerificationConflictError) as exc_info:
        await _service(db_session, now=at).claim(
            called_number=called_number,
            room_name="verification-room-rejected",
        )

    assert exc_info.value.code == expected_code
    assert await _events(db_session, "verification_window_claimed") == []


@pytest.mark.anyio
async def test_duplicate_claim_is_rejected_without_second_event(
    db_session,
    active_user,
) -> None:
    await _seed_provisioned_customer(db_session, active_user)
    await _service(db_session).open_window(active_user.id)
    first = await _service(db_session).claim(
        called_number=OPEVO_NUMBER,
        room_name="verification-room-first",
    )

    with pytest.raises(ForwardingVerificationConflictError) as exc_info:
        await _service(db_session).claim(
            called_number=OPEVO_NUMBER,
            room_name="verification-room-second",
        )

    assert exc_info.value.code == "verification_window_already_claimed"
    assert len(await _events(db_session, "verification_window_claimed")) == 1
    activation = await db_session.scalar(select(CustomerActivation))
    assert activation is not None
    assert activation.verification_session_id == first.session_id


@pytest.mark.anyio
async def test_complete_requires_claimed_current_routing_fingerprint(
    db_session,
    active_user,
) -> None:
    profile, activation, phone, _provisioning = await _seed_provisioned_customer(
        db_session, active_user
    )
    await _service(db_session).open_window(active_user.id)
    claimed = await _service(db_session).claim(
        called_number=phone.e164,
        room_name="verification-room-complete",
    )

    completed = await _service(db_session).complete(session_id=claimed.session_id)

    current_fingerprint = routing_fingerprint(profile, phone)
    assert completed.verification_status == "succeeded"
    assert completed.verification_routing_fingerprint == current_fingerprint
    assert completed.verified_routing_fingerprint == current_fingerprint
    assert completed.forwarding_verified_at == FIXED_NOW
    assert completed.go_live_requested_at is None
    assert completed.go_live_approved_at is None
    assert completed.activated_at is None
    assert len(await _events(db_session, "verification_window_succeeded")) == 1


@pytest.mark.anyio
async def test_content_only_profile_change_does_not_stale_claim(
    db_session,
    active_user,
) -> None:
    profile, _activation, phone, _provisioning = await _seed_provisioned_customer(
        db_session, active_user
    )
    await _service(db_session).open_window(active_user.id)
    claimed = await _service(db_session).claim(
        called_number=phone.e164,
        room_name="verification-room-content",
    )
    profile.public_description = "Nouveau contenu public."
    profile.content_revision += 1
    await db_session.commit()

    completed = await _service(db_session).complete(session_id=claimed.session_id)

    assert completed.verification_status == "succeeded"


@pytest.mark.anyio
@pytest.mark.parametrize(
    "routing_change",
    ["existing_number", "carrier", "opevo_number", "routing_revision"],
)
async def test_routing_change_blocks_completion_with_safe_conflict(
    db_session,
    active_user,
    routing_change: str,
) -> None:
    profile, activation, phone, _provisioning = await _seed_provisioned_customer(
        db_session, active_user
    )
    await _service(db_session).open_window(active_user.id)
    claimed = await _service(db_session).claim(
        called_number=phone.e164,
        room_name="verification-room-stale",
    )
    if routing_change == "existing_number":
        profile.existing_phone_e164 = ALTERNATE_SOURCE_NUMBER
    elif routing_change == "carrier":
        profile.confirmed_carrier = "sfr"
    elif routing_change == "opevo_number":
        phone.e164 = ALTERNATE_OPEVO_NUMBER
    else:
        profile.routing_revision += 1
    await db_session.commit()

    with pytest.raises(ForwardingVerificationConflictError) as exc_info:
        await _service(db_session).complete(session_id=claimed.session_id)

    assert exc_info.value.code == "verification_routing_stale"
    await db_session.refresh(activation)
    assert activation.verification_status == "claimed"
    assert activation.forwarding_verified_at is None
    assert await _events(db_session, "verification_window_succeeded") == []


@pytest.mark.anyio
async def test_completion_is_half_open_through_two_minute_grace(
    db_session,
    active_user,
) -> None:
    _profile, _activation, phone, _provisioning = await _seed_provisioned_customer(
        db_session, active_user
    )
    await _service(db_session).open_window(active_user.id)
    claimed = await _service(
        db_session, now=FIXED_NOW + timedelta(minutes=9, seconds=59)
    ).claim(called_number=phone.e164, room_name="verification-room-grace")

    before_boundary = await _service(
        db_session,
        now=FIXED_NOW + timedelta(minutes=11, seconds=59, microseconds=999999),
    ).complete(session_id=claimed.session_id)

    assert before_boundary.verification_status == "succeeded"


@pytest.mark.anyio
async def test_completion_at_grace_boundary_is_rejected(
    db_session,
    active_user,
) -> None:
    _profile, _activation, phone, _provisioning = await _seed_provisioned_customer(
        db_session, active_user
    )
    await _service(db_session).open_window(active_user.id)
    claimed = await _service(db_session).claim(
        called_number=phone.e164,
        room_name="verification-room-expired-completion",
    )

    with pytest.raises(ForwardingVerificationConflictError) as exc_info:
        await _service(
            db_session, now=FIXED_NOW + timedelta(minutes=12)
        ).complete(session_id=claimed.session_id)

    assert exc_info.value.code == "verification_completion_expired"


@pytest.mark.anyio
async def test_duplicate_completion_is_idempotent_only_for_same_session(
    db_session,
    active_user,
) -> None:
    _profile, _activation, phone, _provisioning = await _seed_provisioned_customer(
        db_session, active_user
    )
    await _service(db_session).open_window(active_user.id)
    claimed = await _service(db_session).claim(
        called_number=phone.e164,
        room_name="verification-room-idempotent",
    )
    first = await _service(db_session).complete(session_id=claimed.session_id)

    repeated = await _service(db_session).complete(session_id=claimed.session_id)

    assert repeated.id == first.id
    assert repeated.forwarding_verified_at == first.forwarding_verified_at
    assert len(await _events(db_session, "verification_window_succeeded")) == 1
    with pytest.raises(ForwardingVerificationConflictError) as exc_info:
        await _service(db_session).complete(session_id="different-session")
    assert exc_info.value.code == "verification_session_not_found"


@pytest.mark.anyio
async def test_completion_before_claim_is_rejected(
    db_session,
    active_user,
) -> None:
    await _seed_provisioned_customer(db_session, active_user)
    await _service(db_session).open_window(active_user.id)

    with pytest.raises(ForwardingVerificationConflictError) as exc_info:
        await _service(db_session).complete(session_id="not-claimed")

    assert exc_info.value.code == "verification_session_not_found"


@pytest.mark.anyio
async def test_claim_preserves_user_activation_profile_phone_lock_order() -> None:
    events: list[str] = []
    user_id = UUID("00000000-0000-0000-0000-000000000001")
    activation_id = UUID("00000000-0000-0000-0000-000000000002")
    profile = SimpleNamespace(
        existing_phone_e164=SOURCE_NUMBER,
        confirmed_carrier="orange",
        routing_revision=2,
    )
    phone = SimpleNamespace(
        user_id=user_id,
        e164=OPEVO_NUMBER,
        provider_number_id="provider-number",
    )
    activation = SimpleNamespace(
        id=activation_id,
        user_id=user_id,
        verification_status="open",
        verification_window_started_at=FIXED_NOW - timedelta(minutes=1),
        verification_window_expires_at=FIXED_NOW + timedelta(minutes=9),
        verification_session_id=None,
        verification_claimed_at=None,
        verification_routing_fingerprint=None,
    )

    class Session:
        async def flush(self) -> None:
            events.append("flush")

        async def commit(self) -> None:
            events.append("commit")

        async def rollback(self) -> None:
            events.append("rollback")

    class Users:
        async def get_by_id_for_update(self, requested_user_id):
            assert requested_user_id == user_id
            events.append("user")
            return SimpleNamespace(
                id=user_id,
                status="active",
                lifecycle_generation=1,
            )

    class Activations:
        async def get_by_user_id_for_update(self, requested_user_id):
            assert requested_user_id == user_id
            events.append("activation")
            return activation

    class Profiles:
        async def get_by_user_id_for_update(self, requested_user_id):
            assert requested_user_id == user_id
            events.append("profile")
            return profile

    class Phones:
        async def get_by_e164(self, e164):
            assert e164 == OPEVO_NUMBER
            events.append("resolve_owner")
            return phone

        async def get_by_user_id_for_update(self, requested_user_id):
            assert requested_user_id == user_id
            events.append("phone")
            return phone

    class Events:
        async def append(self, **_kwargs):
            events.append("event")

    service = ForwardingVerificationService(
        Session(),
        user_repository=Users(),
        activation_repository=Activations(),
        business_profile_repository=Profiles(),
        phone_number_repository=Phones(),
        activation_event_repository=Events(),
        now_provider=lambda: FIXED_NOW,
    )

    await service.claim(
        called_number=OPEVO_NUMBER,
        room_name="verification-room-locks",
    )

    assert events == [
        "resolve_owner",
        "user",
        "activation",
        "profile",
        "phone",
        "event",
        "flush",
        "commit",
    ]


@pytest.mark.anyio
async def test_transaction_owned_claim_seam_flushes_without_committing() -> None:
    events: list[str] = []
    user_id = UUID("00000000-0000-0000-0000-000000000011")
    activation_id = UUID("00000000-0000-0000-0000-000000000012")
    profile = SimpleNamespace(
        existing_phone_e164=SOURCE_NUMBER,
        confirmed_carrier="orange",
        routing_revision=2,
    )
    phone = SimpleNamespace(
        user_id=user_id,
        e164=OPEVO_NUMBER,
        provider_number_id="provider-number",
    )
    activation = SimpleNamespace(
        id=activation_id,
        user_id=user_id,
        verification_status="open",
        verification_window_started_at=FIXED_NOW - timedelta(minutes=1),
        verification_window_expires_at=FIXED_NOW + timedelta(minutes=9),
        verification_session_id=None,
        verification_claimed_at=None,
        verification_routing_fingerprint=None,
    )

    class Session:
        async def flush(self) -> None:
            events.append("flush")

        async def commit(self) -> None:
            events.append("commit")

        async def rollback(self) -> None:
            events.append("rollback")

    class Users:
        async def get_by_id_for_update(self, _user_id):
            events.append("user")
            return SimpleNamespace(
                id=user_id,
                status="active",
                lifecycle_generation=1,
            )

    class Activations:
        async def get_by_user_id_for_update(self, _user_id):
            events.append("activation")
            return activation

    class Profiles:
        async def get_by_user_id_for_update(self, _user_id):
            events.append("profile")
            return profile

    class Phones:
        async def get_by_e164(self, _e164):
            events.append("resolve_owner")
            return phone

        async def get_by_user_id_for_update(self, _user_id):
            events.append("phone")
            return phone

    class Events:
        async def append(self, **_kwargs):
            events.append("event")

    service = ForwardingVerificationService(
        Session(),
        user_repository=Users(),
        activation_repository=Activations(),
        business_profile_repository=Profiles(),
        phone_number_repository=Phones(),
        activation_event_repository=Events(),
        now_provider=lambda: FIXED_NOW,
    )

    claim = await service.claim_in_transaction(
        called_number=OPEVO_NUMBER,
        room_name="verification-room-owned-transaction",
    )

    assert claim.user_id == user_id
    assert events == [
        "resolve_owner",
        "user",
        "activation",
        "profile",
        "phone",
        "event",
        "flush",
    ]
