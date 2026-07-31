from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.dispatch_token import create_dispatch_token
from app.core.verification_token import create_verification_token
from app.models.activation_event import ActivationEvent
from app.models.business_profile import BusinessProfile
from app.models.call import Call
from app.models.call_message import CallMessage
from app.models.customer_activation import CustomerActivation
from app.models.notification import Notification
from app.models.outbox_event import OutboxEvent
from app.models.phone_number import PhoneNumber
from app.models.usage_ledger import UsageLedger
from app.models.user import User
from app.services.forwarding_verification_service import ForwardingVerificationService


SOURCE_NUMBER = "+33199000000"
ALTERNATE_SOURCE_NUMBER = "+33199000001"
PRESVO_NUMBER = "+33999000000"


async def _seed_claimed_verification(database_url: str) -> tuple[str, str]:
    now = datetime.now(UTC)
    engine = create_async_engine(database_url, future=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        user = User(
            clerk_user_id="verification-completion-owner",
            email="verification-completion-owner@example.invalid",
        )
        session.add(user)
        await session.flush()
        session.add_all(
            [
                BusinessProfile(
                    user_id=user.id,
                    owner_name="Camille Martin",
                    business_name="Atelier Martin",
                    business_type="Plomberie",
                    public_description="Dépannage et installation de plomberie.",
                    timezone="Europe/Paris",
                    business_hours={
                        "monday": {"closed": False, "intervals": []}
                    },
                    existing_phone_e164=SOURCE_NUMBER,
                    confirmed_carrier="orange",
                    receptionist_name="Léa",
                    content_revision=3,
                    routing_revision=2,
                ),
                CustomerActivation(
                    user_id=user.id,
                    profile_confirmed_revision=3,
                    profile_confirmed_at=now - timedelta(hours=1),
                    verification_window_started_at=now - timedelta(minutes=1),
                    verification_window_expires_at=now + timedelta(minutes=9),
                    verification_status="open",
                ),
                PhoneNumber(
                    user_id=user.id,
                    e164=PRESVO_NUMBER,
                    country_code="FR",
                    provider="fake",
                    provider_number_id="fake_completion_number",
                    provider_connection_name="app-disabled",
                    is_active=False,
                ),
            ]
        )
        await session.commit()
        claim = await ForwardingVerificationService(
            session,
            now_provider=lambda: now,
        ).claim(
            called_number=PRESVO_NUMBER,
            room_name="verification-completion-room",
        )
        result = (claim.session_id, str(user.id))
    await engine.dispose()
    return result


async def _load_artifact_counts(database_url: str) -> dict[str, int]:
    engine = create_async_engine(database_url, future=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        counts = {
            "calls": int(
                await session.scalar(select(func.count()).select_from(Call)) or 0
            ),
            "messages": int(
                await session.scalar(select(func.count()).select_from(CallMessage))
                or 0
            ),
            "notifications": int(
                await session.scalar(select(func.count()).select_from(Notification))
                or 0
            ),
            "usage": int(
                await session.scalar(select(func.count()).select_from(UsageLedger))
                or 0
            ),
            "outbox": int(
                await session.scalar(select(func.count()).select_from(OutboxEvent))
                or 0
            ),
        }
    await engine.dispose()
    return counts


@pytest.mark.anyio
async def test_completion_requires_only_verification_token_and_writes_no_call_artifacts(
    async_client,
    client_database_url: str,
) -> None:
    session_id, user_id = await _seed_claimed_verification(client_database_url)
    token = create_verification_token(session_id=session_id, user_id=user_id)

    response = await async_client.post(
        f"/api/activation/verification/{session_id}/complete",
        headers={"x-verification-token": token},
        json={"schema_version": 1},
    )

    assert response.status_code == 200
    assert response.json() == {
        "schema_version": 1,
        "status": "verified",
        "session_id": session_id,
    }
    assert await _load_artifact_counts(client_database_url) == {
        "calls": 0,
        "messages": 0,
        "notifications": 0,
        "usage": 0,
        "outbox": 0,
    }


@pytest.mark.anyio
async def test_duplicate_completion_returns_the_exact_same_success_once(
    async_client,
    client_database_url: str,
) -> None:
    session_id, user_id = await _seed_claimed_verification(client_database_url)
    token = create_verification_token(session_id=session_id, user_id=user_id)
    expected = {
        "schema_version": 1,
        "status": "verified",
        "session_id": session_id,
    }

    first = await async_client.post(
        f"/api/activation/verification/{session_id}/complete",
        headers={"x-verification-token": token},
        json={"schema_version": 1},
    )
    duplicate = await async_client.post(
        f"/api/activation/verification/{session_id}/complete",
        headers={"x-verification-token": token},
        json={"schema_version": 1},
    )

    assert first.status_code == duplicate.status_code == 200
    assert first.json() == duplicate.json() == expected
    engine = create_async_engine(client_database_url, future=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        success_events = await session.scalar(
            select(func.count())
            .select_from(ActivationEvent)
            .where(ActivationEvent.event_type == "verification_window_succeeded")
        )
    await engine.dispose()
    assert success_events == 1


@pytest.mark.anyio
@pytest.mark.parametrize(
    "credential_case",
    [
        "missing",
        "malformed",
        "clerk_only",
        "wrong_owner",
        "wrong_session",
        "call_token",
    ],
)
async def test_completion_authentication_failures_are_generic_401(
    async_client,
    client_database_url: str,
    rs256_clerk_token_for,
    credential_case: str,
) -> None:
    session_id, user_id = await _seed_claimed_verification(client_database_url)
    path_session_id = session_id
    headers: dict[str, str] = {}
    if credential_case == "malformed":
        headers["x-verification-token"] = "not-a-jwt"
    elif credential_case == "clerk_only":
        headers["authorization"] = (
            f"Bearer {rs256_clerk_token_for('verification-completion-owner')}"
        )
    elif credential_case == "wrong_owner":
        headers["x-verification-token"] = create_verification_token(
            session_id=session_id,
            user_id=str(uuid4()),
        )
    elif credential_case == "wrong_session":
        path_session_id = str(uuid4())
        headers["x-verification-token"] = create_verification_token(
            session_id=session_id,
            user_id=user_id,
        )
    elif credential_case == "call_token":
        headers["x-verification-token"] = create_dispatch_token(
            call_id=str(uuid4()),
            user_id=user_id,
            agent_config_id=str(uuid4()),
        )

    response = await async_client.post(
        f"/api/activation/verification/{path_session_id}/complete",
        headers=headers,
        json={"schema_version": 1},
    )

    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid verification token"}
    assert session_id not in response.text
    assert user_id not in response.text
    assert await _load_artifact_counts(client_database_url) == {
        "calls": 0,
        "messages": 0,
        "notifications": 0,
        "usage": 0,
        "outbox": 0,
    }


@pytest.mark.anyio
@pytest.mark.parametrize("state_case", ["expired", "not_claimed", "routing_stale"])
async def test_authenticated_unclaimable_completion_is_stable_safe_409(
    async_client,
    client_database_url: str,
    state_case: str,
) -> None:
    session_id, user_id = await _seed_claimed_verification(client_database_url)
    engine = create_async_engine(client_database_url, future=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        activation = await session.scalar(select(CustomerActivation))
        profile = await session.scalar(select(BusinessProfile))
        assert activation is not None
        assert profile is not None
        if state_case == "expired":
            activation.verification_window_expires_at = datetime.now(UTC) - timedelta(
                minutes=3
            )
        elif state_case == "not_claimed":
            activation.verification_status = "open"
        else:
            profile.existing_phone_e164 = ALTERNATE_SOURCE_NUMBER
        await session.commit()
    await engine.dispose()
    token = create_verification_token(session_id=session_id, user_id=user_id)

    response = await async_client.post(
        f"/api/activation/verification/{session_id}/complete",
        headers={"x-verification-token": token},
        json={"schema_version": 1},
    )

    assert response.status_code == 409
    assert response.json() == {"detail": {"code": "verification_not_claimable"}}
    assert session_id not in response.text
    assert user_id not in response.text
