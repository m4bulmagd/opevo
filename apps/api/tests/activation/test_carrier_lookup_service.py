from dataclasses import replace
from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.provider_failures import ProviderFailure
from app.providers.carrier_lookup.base import (
    CarrierLookupResult,
)
from app.repositories.business_profile_repository import BusinessProfileRepository
from app.services.carrier_lookup_service import (
    CarrierLookupService,
    CarrierLookupUnavailableError,
)


class ResultProvider:
    def __init__(self, result: CarrierLookupResult) -> None:
        self.result = result
        self.calls: list[str] = []

    async def lookup(self, e164: str) -> CarrierLookupResult:
        self.calls.append(e164)
        return self.result


class FailingProvider:
    def __init__(self, error: ProviderFailure) -> None:
        self.error = error
        self.calls: list[str] = []

    async def lookup(self, e164: str) -> CarrierLookupResult:
        self.calls.append(e164)
        raise self.error


def lookup_result(
    *,
    number: str = "+33612345678",
    country_code: str = "FR",
    carrier_name: str | None = "Orange France",
    normalized_carrier: str = "other",
    number_type: str | None = "MOBILE",
) -> CarrierLookupResult:
    return CarrierLookupResult(
        normalized_number=number,
        country_code=country_code,
        carrier_name=carrier_name,
        normalized_carrier=normalized_carrier,  # type: ignore[arg-type]
        number_type=number_type,
        looked_up_at=datetime.now(UTC),
    )


async def seed_profile(
    session: AsyncSession,
    user_id,
    *,
    phone: str = "+33612345678",
    confirmed_carrier: str | None = "free",
):
    profile = await BusinessProfileRepository(session).get_or_create_for_update(user_id)
    profile.existing_phone_e164 = phone
    profile.confirmed_carrier = confirmed_carrier
    await session.commit()
    return profile


@pytest.mark.anyio
async def test_lookup_number_normalizes_provider_brand_and_safe_number_type() -> None:
    provider = ResultProvider(
        lookup_result(carrier_name="Orange France", number_type="mobile network")
    )

    result = await CarrierLookupService(None, provider=provider).lookup_number(
        "+33 6 12 34 56 78"
    )

    assert provider.calls == ["+33612345678"]
    assert result.normalized_number == "+33612345678"
    assert result.country_code == "FR"
    assert result.normalized_carrier == "orange"
    assert result.number_type == "mobile"


@pytest.mark.anyio
@pytest.mark.parametrize(
    "unsafe_result",
    [
        lookup_result(number="+12025550123", country_code="US"),
        lookup_result(number="+33712345678", country_code="FR"),
    ],
)
async def test_lookup_number_rejects_non_french_or_mismatched_results(
    unsafe_result: CarrierLookupResult,
) -> None:
    with pytest.raises(ProviderFailure) as exc_info:
        await CarrierLookupService(
            None, provider=ResultProvider(unsafe_result)
        ).lookup_number("+33612345678")

    assert exc_info.value.disposition == "terminal"


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("field", "malformed_value"),
    [
        ("normalized_number", object()),
        ("country_code", object()),
        ("carrier_name", object()),
        ("number_type", object()),
        ("looked_up_at", "not-a-timestamp"),
    ],
)
async def test_lookup_number_converts_malformed_provider_contract_to_safe_error(
    field: str,
    malformed_value: object,
) -> None:
    malformed_result = replace(
        lookup_result(),
        **{field: malformed_value},
    )

    with pytest.raises(ProviderFailure) as exc_info:
        await CarrierLookupService(
            None, provider=ResultProvider(malformed_result)
        ).lookup_number("+33612345678")

    assert exc_info.value.disposition == "terminal"
    assert exc_info.value.error_class == "validation"


@pytest.mark.anyio
async def test_lookup_for_user_releases_transaction_during_provider_io_and_persists_detection(
    db_session: AsyncSession,
    active_user,
) -> None:
    user_id = active_user.id
    profile = await seed_profile(db_session, user_id)
    original_content_revision = profile.content_revision
    original_routing_revision = profile.routing_revision

    class TransactionCheckingProvider(ResultProvider):
        async def lookup(self, e164: str) -> CarrierLookupResult:
            assert db_session.in_transaction() is False
            return await super().lookup(e164)

    provider = TransactionCheckingProvider(
        lookup_result(carrier_name="Bouygues Telecom", number_type="mobile")
    )

    result = await CarrierLookupService(db_session, provider=provider).lookup_for_user(
        user_id
    )

    stored = await BusinessProfileRepository(db_session).get_by_user_id(user_id)
    assert stored is not None
    assert result.normalized_carrier == "bouygues"
    assert stored.detected_carrier == "bouygues"
    assert stored.detected_number_type == "mobile"
    assert stored.carrier_lookup_status == "succeeded"
    assert stored.carrier_looked_up_at == result.looked_up_at
    assert stored.confirmed_carrier == "free"
    assert stored.content_revision == original_content_revision
    assert stored.routing_revision == original_routing_revision


@pytest.mark.anyio
@pytest.mark.parametrize("error_code", ["retryable", "terminal"])
async def test_provider_failure_records_only_safe_failed_state_and_preserves_confirmation(
    db_session: AsyncSession,
    active_user,
    error_code: str,
) -> None:
    user_id = active_user.id
    profile = await seed_profile(db_session, user_id)
    profile.detected_carrier = "orange"
    profile.detected_number_type = "mobile"
    profile.carrier_lookup_status = "succeeded"
    await db_session.commit()
    provider = FailingProvider(
        ProviderFailure(
            provider="telnyx",
            operation="lookup_carrier",
            disposition=error_code,  # type: ignore[arg-type]
            error_class="unavailable" if error_code == "retryable" else "validation",
        )
    )

    with pytest.raises(CarrierLookupUnavailableError):
        await CarrierLookupService(db_session, provider=provider).lookup_for_user(
            user_id
        )

    stored = await BusinessProfileRepository(db_session).get_by_user_id(user_id)
    assert stored is not None
    assert stored.detected_carrier is None
    assert stored.detected_number_type is None
    assert stored.carrier_lookup_status == "failed"
    assert stored.carrier_looked_up_at is not None
    assert stored.confirmed_carrier == "free"
    assert "secret" not in stored.carrier_lookup_status


@pytest.mark.anyio
async def test_malformed_provider_contract_records_safe_failed_state(
    db_session: AsyncSession,
    active_user,
) -> None:
    user_id = active_user.id
    await seed_profile(db_session, user_id)
    malformed_result = replace(lookup_result(), number_type=object())

    with pytest.raises(CarrierLookupUnavailableError):
        await CarrierLookupService(
            db_session,
            provider=ResultProvider(malformed_result),
        ).lookup_for_user(user_id)

    stored = await BusinessProfileRepository(db_session).get_by_user_id(user_id)
    assert stored is not None
    assert stored.detected_carrier is None
    assert stored.detected_number_type is None
    assert stored.carrier_lookup_status == "failed"
    assert stored.carrier_looked_up_at is not None
    assert stored.confirmed_carrier == "free"


@pytest.mark.anyio
async def test_number_changed_during_lookup_never_persists_stale_detection(
    db_session: AsyncSession,
    active_user,
) -> None:
    user_id = active_user.id
    await seed_profile(db_session, user_id, confirmed_carrier=None)
    other_session_factory = async_sessionmaker(
        db_session.bind,
        expire_on_commit=False,
    )

    class NumberChangingProvider(ResultProvider):
        async def lookup(self, e164: str) -> CarrierLookupResult:
            assert db_session.in_transaction() is False
            async with other_session_factory() as other_session:
                changed = await BusinessProfileRepository(
                    other_session
                ).get_or_create_for_update(user_id)
                changed.existing_phone_e164 = "+33712345678"
                changed.content_revision += 1
                changed.routing_revision += 1
                await other_session.commit()
            return await super().lookup(e164)

    provider = NumberChangingProvider(lookup_result())

    with pytest.raises(CarrierLookupUnavailableError):
        await CarrierLookupService(db_session, provider=provider).lookup_for_user(
            user_id
        )

    db_session.expire_all()
    stored = await BusinessProfileRepository(db_session).get_by_user_id(user_id)
    assert stored is not None
    assert stored.existing_phone_e164 == "+33712345678"
    assert stored.detected_carrier is None
    assert stored.detected_number_type is None
    assert stored.carrier_lookup_status is None
    assert stored.carrier_looked_up_at is None


@pytest.mark.anyio
async def test_missing_saved_number_fails_without_calling_provider(
    db_session: AsyncSession,
    active_user,
) -> None:
    provider = ResultProvider(lookup_result())

    with pytest.raises(CarrierLookupUnavailableError):
        await CarrierLookupService(db_session, provider=provider).lookup_for_user(
            active_user.id
        )

    assert provider.calls == []
