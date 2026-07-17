from collections.abc import Mapping

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.user import User
from app.schemas.business_profile import WEEKDAYS


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


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("method", "path", "json"),
    [
        ("GET", "/api/activation", None),
        ("PUT", "/api/business-profile", {}),
        ("POST", "/api/activation/confirm-profile", None),
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
        ("POST", "/api/activation/confirm-profile", None),
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
    [
        ("PUT", "/api/business-profile", {}),
        ("POST", "/api/activation/confirm-profile", None),
    ],
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
