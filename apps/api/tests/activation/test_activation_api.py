from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.user import User
from app.models.subscription import Subscription
from app.models.usage_ledger import UsageLedger
from app.core.auth import UserIdentity
from app.providers.carrier_lookup.base import CarrierLookupResult
from app.schemas.business_profile import WEEKDAYS
from app.services.activation_snapshot_service import ActivationSnapshotUnavailableError


PROFILE_COMMAND_MISSING_USER_CASES = (
    ("PUT", "/api/business-profile", {}),
    ("POST", "/api/activation/confirm-profile", None),
)


def _complete_business_hours() -> dict[str, dict[str, object]]:
    return {
        day: {
            "closed": day in {"saturday", "sunday"},
            "intervals": (
                []
                if day in {"saturday", "sunday"}
                else [{"start": "09:00", "end": "18:00"}]
            ),
        }
        for day in WEEKDAYS
    }


@pytest.fixture
def complete_profile_payload() -> dict[str, object]:
    return {
        "owner_name": "Camille Martin",
        "business_name": "Atelier Martin",
        "business_type": "Plomberie",
        "public_description": "Dépannage et installation de plomberie.",
        "timezone": "Europe/Paris",
        "business_hours": _complete_business_hours(),
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
    }


async def _seed_user(
    database_url: str,
    *,
    clerk_user_id: str,
    email: str,
) -> None:
    engine = create_async_engine(database_url, future=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        session.add(User(clerk_user_id=clerk_user_id, email=email))
        await session.commit()
    await engine.dispose()


async def _load_internal_user_id(database_url: str, clerk_user_id: str):
    engine = create_async_engine(database_url, future=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        user_id = await session.scalar(
            select(User.id).where(User.clerk_user_id == clerk_user_id)
        )
    await engine.dispose()
    return user_id


async def _bootstrap_clerk_user(
    async_client,
    *,
    signed_clerk_headers: Mapping[str, str],
    clerk_user_created_payload_bytes: bytes,
) -> None:
    response = await async_client.post(
        "/webhooks/clerk",
        content=clerk_user_created_payload_bytes,
        headers=signed_clerk_headers,
    )
    assert response.status_code == 202


class MissingUserSnapshotService:
    async def get(self, user_id):
        raise ActivationSnapshotUnavailableError("secret missing-user detail")


class SuccessfulProfileCommandService:
    def __init__(self) -> None:
        self.confirmed_user_ids: list[object] = []

    async def confirm_profile(self, user_id):
        self.confirmed_user_ids.append(user_id)


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("method", "path", "json"),
    [
        ("GET", "/api/activation", None),
        ("PUT", "/api/business-profile", {}),
        ("POST", "/api/activation/lookup-carrier", None),
        ("POST", "/api/activation/confirm-profile", None),
        ("POST", "/api/activation/confirm-provisioning", None),
        ("POST", "/api/activation/retry-provisioning", None),
    ],
)
async def test_activation_routes_require_authentication(
    async_client,
    method: str,
    path: str,
    json: dict[str, object] | None,
) -> None:
    response = await async_client.request(method, path, json=json)

    assert response.status_code == 401


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("method", "path", "json"),
    [
        ("GET", "/api/activation", None),
        ("PUT", "/api/business-profile", {}),
        ("POST", "/api/activation/lookup-carrier", None),
        ("POST", "/api/activation/confirm-profile", None),
        ("POST", "/api/activation/confirm-provisioning", None),
        ("POST", "/api/activation/retry-provisioning", None),
    ],
)
async def test_activation_routes_reject_unsynced_clerk_identity(
    async_client,
    valid_clerk_but_missing_local_user_token: str,
    method: str,
    path: str,
    json: dict[str, object] | None,
) -> None:
    response = await async_client.request(
        method,
        path,
        json=json,
        headers={
            "Authorization": f"Bearer {valid_clerk_but_missing_local_user_token}"
        },
    )

    assert response.status_code == 401
    assert response.json() == {"detail": "User not synced"}


@pytest.mark.anyio
async def test_profile_first_api_round_trip(
    async_client,
    signed_clerk_headers,
    clerk_user_created_payload_bytes: bytes,
    rs256_clerk_token_for,
    complete_profile_payload: dict[str, object],
) -> None:
    await _bootstrap_clerk_user(
        async_client,
        signed_clerk_headers=signed_clerk_headers,
        clerk_user_created_payload_bytes=clerk_user_created_payload_bytes,
    )
    headers = {"Authorization": f"Bearer {rs256_clerk_token_for('user_123')}"}

    initial = await async_client.get("/api/activation", headers=headers)
    saved = await async_client.put(
        "/api/business-profile",
        json=complete_profile_payload,
        headers=headers,
    )
    confirmed = await async_client.post(
        "/api/activation/confirm-profile",
        headers=headers,
    )
    resumed = await async_client.get("/api/activation", headers=headers)

    assert initial.status_code == 200
    assert initial.json()["stage"] == "profile_required"
    assert saved.status_code == 200
    assert saved.json()["existing_phone_e164"] == "+33612345678"
    assert confirmed.status_code == 200
    assert confirmed.json()["stage"] == "payment_required"
    assert confirmed.json()["activation"]["profile_confirmed_at"] is not None
    assert resumed.status_code == 200
    assert resumed.json()["stage"] == "payment_required"


@pytest.mark.anyio
async def test_carrier_lookup_returns_normalized_detection_without_confirming_it(
    async_client,
    client_database_url: str,
    rs256_clerk_token_for,
    complete_profile_payload: dict[str, object],
) -> None:
    await _seed_user(
        client_database_url,
        clerk_user_id="user_carrier_lookup_success",
        email="carrier-lookup-success@example.com",
    )
    headers = {
        "Authorization": (
            f"Bearer {rs256_clerk_token_for('user_carrier_lookup_success')}"
        )
    }
    saved = await async_client.put(
        "/api/business-profile",
        json=complete_profile_payload | {"confirmed_carrier": "free"},
        headers=headers,
    )

    response = await async_client.post(
        "/api/activation/lookup-carrier",
        headers=headers,
    )
    snapshot = await async_client.get("/api/activation", headers=headers)

    assert saved.status_code == 200
    assert response.status_code == 200
    assert response.json()["normalized_number"] == "+33612345678"
    assert response.json()["country_code"] == "FR"
    assert response.json()["carrier_name"] == "Orange"
    assert response.json()["normalized_carrier"] == "orange"
    assert response.json()["number_type"] == "mobile"
    assert response.json()["looked_up_at"] is not None
    assert snapshot.json()["profile"]["detected_carrier"] == "orange"
    assert snapshot.json()["profile"]["carrier_lookup_status"] == "succeeded"
    assert snapshot.json()["profile"]["confirmed_carrier"] == "free"


@pytest.mark.anyio
async def test_lookup_failure_returns_safe_manual_fallback_and_profile_put_still_confirms(
    async_client,
    test_app,
    client_database_url: str,
    rs256_clerk_token_for,
    complete_profile_payload: dict[str, object],
) -> None:
    from app.routers.activation import get_carrier_lookup_service
    from app.services.carrier_lookup_service import CarrierLookupUnavailableError

    class FailingLookupService:
        async def lookup_for_user(self, user_id):
            raise CarrierLookupUnavailableError("provider credential secret")

    await _seed_user(
        client_database_url,
        clerk_user_id="user_carrier_lookup_failure",
        email="carrier-lookup-failure@example.com",
    )
    headers = {
        "Authorization": (
            f"Bearer {rs256_clerk_token_for('user_carrier_lookup_failure')}"
        )
    }
    await async_client.put(
        "/api/business-profile",
        json=complete_profile_payload | {"confirmed_carrier": None},
        headers=headers,
    )
    test_app.dependency_overrides[get_carrier_lookup_service] = (
        FailingLookupService
    )
    try:
        failed = await async_client.post(
            "/api/activation/lookup-carrier",
            headers=headers,
        )
    finally:
        test_app.dependency_overrides.pop(get_carrier_lookup_service, None)

    manually_confirmed = await async_client.put(
        "/api/business-profile",
        json=complete_profile_payload | {"confirmed_carrier": "sfr"},
        headers=headers,
    )

    assert failed.status_code == 503
    assert failed.json() == {
        "detail": {
            "code": "carrier_lookup_unavailable",
            "manual_selection_allowed": True,
        }
    }
    assert "provider" not in failed.text.lower()
    assert "secret" not in failed.text.lower()
    assert manually_confirmed.status_code == 200
    assert manually_confirmed.json()["confirmed_carrier"] == "sfr"


@pytest.mark.anyio
async def test_telnyx_api_error_persists_failure_and_returns_secret_free_fallback(
    async_client,
    test_app,
    client_database_url: str,
    rs256_clerk_token_for,
    complete_profile_payload: dict[str, object],
) -> None:
    import telnyx

    from app.routers.activation import get_carrier_lookup_service
    from app.providers.carrier_lookup.telnyx import TelnyxCarrierLookupProvider
    from app.services.carrier_lookup_service import CarrierLookupService

    class APIErrorNumberLookupResource:
        @classmethod
        def retrieve(cls, phone_number, /, *, api_key):
            raise telnyx.error.APIError(
                [{"title": "provider credential secret"}],
                http_status=500,
            )

    class PersistingAPIErrorLookupService:
        async def lookup_for_user(self, user_id):
            engine = create_async_engine(client_database_url, future=True)
            session_factory = async_sessionmaker(engine, expire_on_commit=False)
            try:
                async with session_factory() as session:
                    service = CarrierLookupService(
                        session,
                        provider=TelnyxCarrierLookupProvider(
                            api_key="test-key",
                            number_lookup_resource=APIErrorNumberLookupResource,
                        ),
                    )
                    return await service.lookup_for_user(user_id)
            finally:
                await engine.dispose()

    await _seed_user(
        client_database_url,
        clerk_user_id="user_carrier_api_error",
        email="carrier-api-error@example.com",
    )
    headers = {
        "Authorization": (
            f"Bearer {rs256_clerk_token_for('user_carrier_api_error')}"
        )
    }
    saved = await async_client.put(
        "/api/business-profile",
        json=complete_profile_payload | {"confirmed_carrier": "free"},
        headers=headers,
    )
    test_app.dependency_overrides[get_carrier_lookup_service] = (
        PersistingAPIErrorLookupService
    )
    try:
        failed = await async_client.post(
            "/api/activation/lookup-carrier",
            headers=headers,
        )
    finally:
        test_app.dependency_overrides.pop(get_carrier_lookup_service, None)
    snapshot = await async_client.get("/api/activation", headers=headers)

    assert failed.status_code == 503
    assert failed.json() == {
        "detail": {
            "code": "carrier_lookup_unavailable",
            "manual_selection_allowed": True,
        }
    }
    assert "provider" not in failed.text.lower()
    assert "secret" not in failed.text.lower()
    profile = snapshot.json()["profile"]
    assert profile["detected_carrier"] is None
    assert profile["detected_number_type"] is None
    assert profile["carrier_lookup_status"] == "failed"
    assert profile["carrier_looked_up_at"] is not None
    assert profile["confirmed_carrier"] == "free"
    assert profile["content_revision"] == saved.json()["content_revision"]
    assert profile["routing_revision"] == saved.json()["routing_revision"]


@pytest.mark.anyio
async def test_carrier_lookup_uses_only_authenticated_internal_user_ownership(
    async_client,
    test_app,
    client_database_url: str,
    rs256_clerk_token_for,
) -> None:
    from app.routers.activation import get_carrier_lookup_service

    class CapturingLookupService:
        def __init__(self) -> None:
            self.user_ids: list[object] = []

        async def lookup_for_user(self, user_id):
            self.user_ids.append(user_id)
            return CarrierLookupResult(
                normalized_number="+33612345678",
                country_code="FR",
                carrier_name="Orange",
                normalized_carrier="orange",
                number_type="mobile",
                looked_up_at=datetime.now(UTC),
            )

    await _seed_user(
        client_database_url,
        clerk_user_id="carrier_owner_a",
        email="carrier-owner-a@example.com",
    )
    await _seed_user(
        client_database_url,
        clerk_user_id="carrier_owner_b",
        email="carrier-owner-b@example.com",
    )
    expected_user_id = await _load_internal_user_id(
        client_database_url,
        "carrier_owner_b",
    )
    service = CapturingLookupService()
    test_app.dependency_overrides[get_carrier_lookup_service] = lambda: service
    try:
        response = await async_client.post(
            "/api/activation/lookup-carrier",
            headers={
                "Authorization": (
                    f"Bearer {rs256_clerk_token_for('carrier_owner_b')}"
                )
            },
        )
    finally:
        test_app.dependency_overrides.pop(get_carrier_lookup_service, None)

    assert response.status_code == 200
    assert service.user_ids == [expected_user_id]


@pytest.mark.anyio
async def test_business_profile_rejects_invalid_payload(
    async_client,
    client_database_url: str,
    rs256_clerk_token_for,
) -> None:
    await _seed_user(
        client_database_url,
        clerk_user_id="user_invalid_profile",
        email="invalid-profile@example.com",
    )

    response = await async_client.put(
        "/api/business-profile",
        json={"owner_name": "   ", "unexpected": "value"},
        headers={
            "Authorization": (
                f"Bearer {rs256_clerk_token_for('user_invalid_profile')}"
            )
        },
    )

    assert response.status_code == 422


@pytest.mark.anyio
async def test_confirm_profile_returns_stable_incomplete_profile_error(
    async_client,
    client_database_url: str,
    rs256_clerk_token_for,
) -> None:
    await _seed_user(
        client_database_url,
        clerk_user_id="user_incomplete_profile",
        email="incomplete-profile@example.com",
    )
    headers = {
        "Authorization": f"Bearer {rs256_clerk_token_for('user_incomplete_profile')}"
    }
    saved = await async_client.put(
        "/api/business-profile",
        json={"owner_name": "Camille Martin"},
        headers=headers,
    )

    response = await async_client.post(
        "/api/activation/confirm-profile",
        headers=headers,
    )

    assert saved.status_code == 200
    assert response.status_code == 422
    assert response.json() == {
        "detail": {
            "code": "profile_incomplete",
            "fields": [
                "business_name",
                "business_type",
                "public_description",
                "timezone",
                "business_hours",
                "existing_phone_e164",
                "confirmed_carrier",
                "receptionist_name",
            ],
        }
    }


@pytest.mark.anyio
async def test_confirm_profile_revalidates_current_profile_after_stale_edit(
    async_client,
    client_database_url: str,
    rs256_clerk_token_for,
    complete_profile_payload: dict[str, object],
) -> None:
    await _seed_user(
        client_database_url,
        clerk_user_id="user_stale_confirmation",
        email="stale-confirmation@example.com",
    )
    headers = {
        "Authorization": f"Bearer {rs256_clerk_token_for('user_stale_confirmation')}"
    }
    saved = await async_client.put(
        "/api/business-profile",
        json=complete_profile_payload,
        headers=headers,
    )
    confirmed = await async_client.post(
        "/api/activation/confirm-profile",
        headers=headers,
    )
    changed_payload = complete_profile_payload | {
        "existing_phone_e164": "+33 1 44 55 66 77"
    }
    changed = await async_client.put(
        "/api/business-profile",
        json=changed_payload,
        headers=headers,
    )

    stale_confirmation = await async_client.post(
        "/api/activation/confirm-profile",
        headers=headers,
    )

    assert saved.status_code == 200
    assert confirmed.status_code == 200
    assert changed.status_code == 200
    assert changed.json()["confirmed_carrier"] is None
    assert stale_confirmation.status_code == 422
    assert stale_confirmation.json() == {
        "detail": {
            "code": "profile_incomplete",
            "fields": ["confirmed_carrier"],
        }
    }


@pytest.mark.anyio
async def test_profile_projection_size_failure_uses_stable_error_code(
    async_client,
    test_app,
    client_database_url: str,
    rs256_clerk_token_for,
) -> None:
    from app.routers.activation import get_business_profile_service
    from app.services.receptionist_projection_service import (
        ReceptionistProjectionTooLargeError,
    )

    class RejectingProfileService:
        async def save_draft(self, user_id, payload):
            raise ReceptionistProjectionTooLargeError("knowledge_base")

    await _seed_user(
        client_database_url,
        clerk_user_id="user_oversized_projection",
        email="oversized-projection@example.com",
    )
    test_app.dependency_overrides[get_business_profile_service] = (
        RejectingProfileService
    )
    try:
        response = await async_client.put(
            "/api/business-profile",
            json={},
            headers={
                "Authorization": (
                    f"Bearer {rs256_clerk_token_for('user_oversized_projection')}"
                )
            },
        )
    finally:
        test_app.dependency_overrides.pop(get_business_profile_service, None)

    assert response.status_code == 422
    assert response.json() == {
        "detail": {"code": "profile_projection_too_large"}
    }


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("method", "path", "json"),
    PROFILE_COMMAND_MISSING_USER_CASES,
)
async def test_profile_commands_translate_missing_internal_user_to_stable_conflict(
    async_client,
    test_app,
    client_database_url: str,
    rs256_clerk_token_for,
    method: str,
    path: str,
    json: dict[str, object] | None,
) -> None:
    from app.routers.activation import get_business_profile_service
    from app.services.business_profile_service import BusinessProfileNotFoundError

    class MissingProfileService:
        async def save_draft(self, user_id, payload):
            raise BusinessProfileNotFoundError("sensitive internal detail")

        async def confirm_profile(self, user_id):
            raise BusinessProfileNotFoundError("sensitive internal detail")

    await _seed_user(
        client_database_url,
        clerk_user_id="user_deleted_during_command",
        email="deleted-during-command@example.com",
    )
    test_app.dependency_overrides[get_business_profile_service] = (
        MissingProfileService
    )
    try:
        response = await async_client.request(
            method,
            path,
            json=json,
            headers={
                "Authorization": (
                    f"Bearer {rs256_clerk_token_for('user_deleted_during_command')}"
                )
            },
        )
    finally:
        test_app.dependency_overrides.pop(get_business_profile_service, None)

    assert response.status_code == 409
    assert response.json() == {"detail": {"code": "profile_unavailable"}}


@pytest.mark.anyio
async def test_get_activation_translates_missing_user_snapshot_race(
    async_client,
    test_app,
    client_database_url: str,
    rs256_clerk_token_for,
) -> None:
    from app.routers.activation import get_activation_snapshot_service

    await _seed_user(
        client_database_url,
        clerk_user_id="user_deleted_before_snapshot",
        email="deleted-before-snapshot@example.com",
    )
    test_app.dependency_overrides[get_activation_snapshot_service] = (
        MissingUserSnapshotService
    )
    try:
        response = await async_client.get(
            "/api/activation",
            headers={
                "Authorization": (
                    f"Bearer {rs256_clerk_token_for('user_deleted_before_snapshot')}"
                )
            },
        )
    finally:
        test_app.dependency_overrides.pop(get_activation_snapshot_service, None)

    assert response.status_code == 409
    assert response.json() == {"detail": {"code": "profile_unavailable"}}
    assert "secret missing-user detail" not in response.text


@pytest.mark.anyio
async def test_confirm_profile_translates_missing_user_during_snapshot_refresh(
    async_client,
    test_app,
    client_database_url: str,
    rs256_clerk_token_for,
) -> None:
    from app.routers.activation import (
        get_activation_snapshot_service,
        get_business_profile_service,
    )

    await _seed_user(
        client_database_url,
        clerk_user_id="user_deleted_after_confirmation",
        email="deleted-after-confirmation@example.com",
    )
    command_service = SuccessfulProfileCommandService()
    test_app.dependency_overrides[get_business_profile_service] = lambda: command_service
    test_app.dependency_overrides[get_activation_snapshot_service] = (
        MissingUserSnapshotService
    )
    try:
        response = await async_client.post(
            "/api/activation/confirm-profile",
            headers={
                "Authorization": (
                    f"Bearer {rs256_clerk_token_for('user_deleted_after_confirmation')}"
                )
            },
        )
    finally:
        test_app.dependency_overrides.pop(get_business_profile_service, None)
        test_app.dependency_overrides.pop(get_activation_snapshot_service, None)

    assert len(command_service.confirmed_user_ids) == 1
    assert response.status_code == 409
    assert response.json() == {"detail": {"code": "profile_unavailable"}}
    assert "secret missing-user detail" not in response.text


@pytest.mark.anyio
async def test_activation_api_is_scoped_to_authenticated_user(
    async_client,
    client_database_url: str,
    rs256_clerk_token_for,
    complete_profile_payload: dict[str, object],
) -> None:
    await _seed_user(
        client_database_url,
        clerk_user_id="activation_user_a",
        email="activation-a@example.com",
    )
    await _seed_user(
        client_database_url,
        clerk_user_id="activation_user_b",
        email="activation-b@example.com",
    )
    headers_a = {
        "Authorization": f"Bearer {rs256_clerk_token_for('activation_user_a')}"
    }
    headers_b = {
        "Authorization": f"Bearer {rs256_clerk_token_for('activation_user_b')}"
    }

    saved_a = await async_client.put(
        "/api/business-profile",
        json=complete_profile_payload,
        headers=headers_a,
    )
    snapshot_b = await async_client.get("/api/activation", headers=headers_b)

    assert saved_a.status_code == 200
    assert snapshot_b.status_code == 200
    assert snapshot_b.json()["profile"]["business_name"] is None
    assert snapshot_b.json()["stage"] == "profile_required"


@pytest.mark.anyio
async def test_confirm_provisioning_returns_202_canonical_activation_snapshot(
    async_client,
    client_database_url: str,
    rs256_clerk_token_for,
    complete_profile_payload: dict[str, object],
) -> None:
    clerk_user_id = "activation_confirm_provisioning"
    await _seed_user(
        client_database_url,
        clerk_user_id=clerk_user_id,
        email="confirm-provisioning@example.com",
    )
    headers = {"Authorization": f"Bearer {rs256_clerk_token_for(clerk_user_id)}"}
    saved = await async_client.put(
        "/api/business-profile",
        json=complete_profile_payload,
        headers=headers,
    )
    confirmed = await async_client.post(
        "/api/activation/confirm-profile",
        headers=headers,
    )
    user_id = await _load_internal_user_id(client_database_url, clerk_user_id)
    subscription_now = datetime.now(UTC)
    engine = create_async_engine(client_database_url, future=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        session.add_all(
            [
                Subscription(
                    user_id=user_id,
                    stripe_customer_id="cus_activation_confirm",
                    stripe_subscription_id="sub_activation_confirm",
                    plan_tier="starter",
                    status="active",
                    allocated_minutes=60,
                    current_period_start=subscription_now - timedelta(days=1),
                    current_period_end=subscription_now + timedelta(days=30),
                ),
                UsageLedger(
                    user_id=user_id,
                    event_type="subscription_activated",
                    source_id="in_activation_confirm",
                    minutes_delta=60,
                    balance_after=60,
                ),
            ]
        )
        await session.commit()
    await engine.dispose()

    response = await async_client.post(
        "/api/activation/confirm-provisioning",
        headers=headers,
    )

    assert saved.status_code == 200
    assert confirmed.status_code == 200
    assert response.status_code == 202
    assert response.json()["stage"] == "provisioning"
    assert response.json()["activation"]["provisioning_consented_at"] is not None
    assert response.json()["number"]["provisioning_status"] == "queued"


@pytest.mark.anyio
async def test_confirm_provisioning_translates_blocker_to_stable_409_code(
    async_client,
    test_app,
    client_database_url: str,
    rs256_clerk_token_for,
) -> None:
    from app.routers.activation import get_activation_provisioning_service
    from app.services.activation_provisioning_service import (
        ActivationProvisioningBlockedError,
    )

    class BlockedService:
        async def confirm(self, user_id, *, arq_pool):
            raise ActivationProvisioningBlockedError("minutes_exhausted")

    clerk_user_id = "activation_provisioning_blocked"
    await _seed_user(
        client_database_url,
        clerk_user_id=clerk_user_id,
        email="provisioning-blocked@example.com",
    )
    test_app.dependency_overrides[get_activation_provisioning_service] = BlockedService
    try:
        response = await async_client.post(
            "/api/activation/confirm-provisioning",
            headers={
                "Authorization": f"Bearer {rs256_clerk_token_for(clerk_user_id)}"
            },
        )
    finally:
        test_app.dependency_overrides.pop(
            get_activation_provisioning_service,
            None,
        )

    assert response.status_code == 409
    assert response.json() == {"detail": {"code": "minutes_exhausted"}}


@pytest.mark.anyio
async def test_confirm_provisioning_route_uses_authenticated_owner_and_arq_wake() -> None:
    from app.routers.activation import confirm_provisioning

    user_id = uuid4()
    pool = object()
    canonical_snapshot = object()

    class Commands:
        def __init__(self) -> None:
            self.calls: list[tuple[object, object]] = []

        async def confirm(self, requested_user_id, *, arq_pool):
            self.calls.append((requested_user_id, arq_pool))
            return canonical_snapshot

    commands = Commands()
    result = await confirm_provisioning(
        request=SimpleNamespace(
            app=SimpleNamespace(state=SimpleNamespace(arq_pool=pool))
        ),
        identity=UserIdentity(
            clerk_user_id="authenticated_owner",
            internal_user_id=user_id,
        ),
        service=commands,
    )

    assert result is canonical_snapshot
    assert commands.calls == [(user_id, pool)]
