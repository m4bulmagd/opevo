from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.business_profile import BusinessProfile
from app.models.phone_number import PhoneNumber
from app.repositories.business_profile_repository import BusinessProfileRepository
from app.repositories.customer_activation_repository import (
    CustomerActivationRepository,
)
from app.schemas.business_profile import BusinessProfileDraft, WEEKDAYS
from app.services.business_profile_service import (
    BusinessProfileIncompleteError,
    BusinessProfileNotFoundError,
    BusinessProfileService,
)
from app.services.routing_fingerprint import routing_fingerprint


def complete_business_hours() -> dict[str, dict[str, object]]:
    return {
        day: {
            "closed": day in {"saturday", "sunday"},
            "intervals": []
            if day in {"saturday", "sunday"}
            else [{"start": "09:00", "end": "18:00"}],
        }
        for day in WEEKDAYS
    }


def complete_profile_draft(**overrides: object) -> BusinessProfileDraft:
    payload = {
        "owner_name": "Camille Martin",
        "business_name": "Atelier Martin",
        "business_type": "Plomberie",
        "public_description": "Dépannage et installation de plomberie.",
        "timezone": "Europe/Paris",
        "business_hours": complete_business_hours(),
        "existing_phone_e164": "+33 6 12 34 56 78",
        "confirmed_carrier": "orange",
        "receptionist_name": "Léa",
        "faqs": [
            {
                "question": "Intervenez-vous le week-end ?",
                "answer": "Oui, uniquement pour les urgences.",
            }
        ],
        "special_instructions": "Toujours demander le code postal.",
        "escalation_notes": "Transférer les urgences au propriétaire.",
    } | overrides
    return BusinessProfileDraft.model_validate(payload)


def build_profile_service(db_session: AsyncSession) -> BusinessProfileService:
    return BusinessProfileService(db_session)


@pytest.mark.anyio
async def test_save_draft_persists_normalized_profile_without_confirming(
    db_session: AsyncSession,
    active_user,
) -> None:
    saved = await build_profile_service(db_session).save_draft(
        active_user.id,
        complete_profile_draft(),
    )
    activation = await CustomerActivationRepository(db_session).get_by_user_id(
        active_user.id
    )

    assert saved.existing_phone_e164 == "+33612345678"
    assert saved.business_hours["monday"]["intervals"] == [
        {"start": "09:00", "end": "18:00"}
    ]
    assert saved.content_revision == 2
    assert saved.routing_revision == 2
    assert activation is not None
    assert activation.profile_confirmed_revision is None
    assert activation.profile_confirmed_at is None


@pytest.mark.anyio
async def test_saving_identical_draft_does_not_increment_revisions(
    db_session: AsyncSession,
    active_user,
) -> None:
    service = build_profile_service(db_session)
    first = await service.save_draft(active_user.id, complete_profile_draft())
    content_revision = first.content_revision
    routing_revision = first.routing_revision

    second = await service.save_draft(active_user.id, complete_profile_draft())

    assert second.content_revision == content_revision
    assert second.routing_revision == routing_revision


@pytest.mark.anyio
async def test_confirm_profile_sets_initial_milestone_and_france_for_null_country(
    db_session: AsyncSession,
    active_user,
) -> None:
    assert active_user.country_code is None
    service = build_profile_service(db_session)
    profile = await service.save_draft(active_user.id, complete_profile_draft())

    activation = await service.confirm_profile(active_user.id)

    assert activation.profile_confirmed_revision == profile.content_revision
    assert activation.profile_confirmed_at is not None
    assert active_user.country_code == "FR"


@pytest.mark.anyio
async def test_confirm_profile_reports_every_missing_required_field(
    db_session: AsyncSession,
    active_user,
) -> None:
    service = build_profile_service(db_session)
    await service.save_draft(active_user.id, BusinessProfileDraft())

    with pytest.raises(BusinessProfileIncompleteError) as exc_info:
        await service.confirm_profile(active_user.id)

    assert exc_info.value.fields == (
        "owner_name",
        "business_name",
        "business_type",
        "public_description",
        "timezone",
        "business_hours",
        "existing_phone_e164",
        "confirmed_carrier",
        "receptionist_name",
    )


@pytest.mark.anyio
@pytest.mark.parametrize("operation", ["save", "confirm"])
async def test_profile_commands_reject_unknown_user(
    db_session: AsyncSession,
    operation: str,
) -> None:
    service = build_profile_service(db_session)

    with pytest.raises(BusinessProfileNotFoundError):
        if operation == "save":
            await service.save_draft(uuid4(), complete_profile_draft())
        else:
            await service.confirm_profile(uuid4())


@pytest.mark.anyio
async def test_routing_change_clears_verification_and_go_live(
    db_session: AsyncSession,
    active_user,
) -> None:
    service = build_profile_service(db_session)
    await service.save_draft(active_user.id, complete_profile_draft())
    await service.confirm_profile(active_user.id)
    profile = await BusinessProfileRepository(db_session).get_by_user_id(active_user.id)
    activation = await CustomerActivationRepository(
        db_session
    ).get_or_create_for_update(active_user.id)
    assert profile is not None
    now = datetime.now(UTC)
    activation.verification_window_started_at = now
    activation.verification_window_expires_at = now + timedelta(minutes=15)
    activation.verification_session_id = "verification-session"
    activation.verification_claimed_at = now
    activation.verification_dispatch_id = "dispatch-id"
    activation.verification_routing_fingerprint = "pending-fingerprint"
    activation.verification_status = "succeeded"
    activation.verified_routing_fingerprint = "old"
    activation.forwarding_verified_at = now
    activation.go_live_requested_at = now
    activation.go_live_approved_at = now
    activation.activated_at = now
    profile.detected_carrier = "Orange"
    profile.detected_number_type = "mobile"
    profile.carrier_lookup_status = "succeeded"
    profile.carrier_looked_up_at = now
    initial_profile_confirmed_revision = activation.profile_confirmed_revision
    initial_profile_confirmed_at = activation.profile_confirmed_at
    previous_content_revision = profile.content_revision
    previous_routing_revision = profile.routing_revision
    await db_session.commit()

    updated = complete_profile_draft(existing_phone_e164="+33144556677")
    saved = await service.save_draft(active_user.id, updated)

    assert saved.content_revision == previous_content_revision + 1
    assert saved.routing_revision == previous_routing_revision + 1
    assert saved.detected_carrier is None
    assert saved.detected_number_type is None
    assert saved.carrier_lookup_status is None
    assert saved.carrier_looked_up_at is None
    assert saved.confirmed_carrier is None
    assert activation.verification_window_started_at is None
    assert activation.verification_window_expires_at is None
    assert activation.verification_session_id is None
    assert activation.verification_claimed_at is None
    assert activation.verification_dispatch_id is None
    assert activation.verification_routing_fingerprint is None
    assert activation.verified_routing_fingerprint is None
    assert activation.forwarding_verified_at is None
    assert activation.go_live_requested_at is None
    assert activation.go_live_approved_at is None
    assert activation.activated_at is None
    assert activation.verification_status == "invalidated"
    assert activation.profile_confirmed_revision == initial_profile_confirmed_revision
    assert activation.profile_confirmed_at == initial_profile_confirmed_at


@pytest.mark.anyio
async def test_confirmed_carrier_change_invalidates_routing_state(
    db_session: AsyncSession,
    active_user,
) -> None:
    service = build_profile_service(db_session)
    profile = await service.save_draft(active_user.id, complete_profile_draft())
    activation = await service.confirm_profile(active_user.id)
    now = datetime.now(UTC)
    activation.verification_status = "succeeded"
    activation.verified_routing_fingerprint = "old"
    activation.forwarding_verified_at = now
    activation.go_live_approved_at = now
    activation.activated_at = now
    previous_routing_revision = profile.routing_revision
    await db_session.commit()

    saved = await service.save_draft(
        active_user.id,
        complete_profile_draft(confirmed_carrier="sfr"),
    )

    assert saved.routing_revision == previous_routing_revision + 1
    assert activation.verification_status == "invalidated"
    assert activation.verified_routing_fingerprint is None
    assert activation.forwarding_verified_at is None
    assert activation.go_live_approved_at is None
    assert activation.activated_at is None


@pytest.mark.anyio
@pytest.mark.parametrize("content_field", ["hours", "faqs"])
async def test_content_change_preserves_routing_and_completed_milestones(
    db_session: AsyncSession,
    active_user,
    content_field: str,
) -> None:
    service = build_profile_service(db_session)
    profile = await service.save_draft(active_user.id, complete_profile_draft())
    activation = await service.confirm_profile(active_user.id)
    now = datetime.now(UTC)
    activation.verification_status = "succeeded"
    activation.verified_routing_fingerprint = "verified-fingerprint"
    activation.forwarding_verified_at = now
    activation.go_live_approved_at = now
    activation.activated_at = now
    await db_session.commit()
    previous_content_revision = profile.content_revision
    previous_routing_revision = profile.routing_revision
    initial_profile_confirmed_revision = activation.profile_confirmed_revision
    initial_profile_confirmed_at = activation.profile_confirmed_at

    if content_field == "hours":
        hours = complete_business_hours()
        hours["monday"] = {
            "closed": False,
            "intervals": [{"start": "10:00", "end": "18:00"}],
        }
        updated = complete_profile_draft(business_hours=hours)
    else:
        updated = complete_profile_draft(
            faqs=[{"question": "Nouvelle question ?", "answer": "Nouvelle réponse."}]
        )

    saved = await service.save_draft(active_user.id, updated)

    assert saved.content_revision == previous_content_revision + 1
    assert saved.routing_revision == previous_routing_revision
    assert activation.verification_status == "succeeded"
    assert activation.verified_routing_fingerprint == "verified-fingerprint"
    assert activation.forwarding_verified_at == now
    assert activation.go_live_approved_at == now
    assert activation.activated_at == now
    assert activation.profile_confirmed_revision == initial_profile_confirmed_revision
    assert activation.profile_confirmed_at == initial_profile_confirmed_at


def profile_for_fingerprint() -> BusinessProfile:
    return BusinessProfile(
        user_id=uuid4(),
        existing_phone_e164="+33612345678",
        confirmed_carrier="orange",
        routing_revision=3,
    )


def phone_for_fingerprint(profile: BusinessProfile) -> PhoneNumber:
    return PhoneNumber(
        user_id=profile.user_id,
        e164="+33187654321",
        country_code="FR",
    )


def test_routing_fingerprint_is_stable_and_contains_no_phone_number() -> None:
    profile = profile_for_fingerprint()
    phone = phone_for_fingerprint(profile)

    first = routing_fingerprint(profile, phone)
    second = routing_fingerprint(profile, phone)

    assert first == second
    assert len(first) == 64
    assert profile.existing_phone_e164 not in first
    assert phone.e164 not in first


def test_routing_fingerprint_changes_for_every_routing_sensitive_value() -> None:
    baseline_profile = profile_for_fingerprint()
    baseline_phone = phone_for_fingerprint(baseline_profile)
    baseline = routing_fingerprint(baseline_profile, baseline_phone)

    changed_number = profile_for_fingerprint()
    changed_number.existing_phone_e164 = "+33144556677"
    changed_carrier = profile_for_fingerprint()
    changed_carrier.confirmed_carrier = "sfr"
    changed_revision = profile_for_fingerprint()
    changed_revision.routing_revision += 1
    changed_presvo_profile = profile_for_fingerprint()
    changed_presvo_number = phone_for_fingerprint(changed_presvo_profile)
    changed_presvo_number.e164 = "+33123456789"

    assert (
        routing_fingerprint(changed_number, phone_for_fingerprint(changed_number))
        != baseline
    )
    assert (
        routing_fingerprint(changed_carrier, phone_for_fingerprint(changed_carrier))
        != baseline
    )
    assert (
        routing_fingerprint(changed_revision, phone_for_fingerprint(changed_revision))
        != baseline
    )
    assert (
        routing_fingerprint(changed_presvo_profile, changed_presvo_number) != baseline
    )
    assert routing_fingerprint(baseline_profile, None) != baseline
