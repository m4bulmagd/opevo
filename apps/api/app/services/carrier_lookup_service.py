from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.providers.carrier_lookup.base import (
    CarrierLookupError,
    CarrierLookupProvider,
    CarrierLookupResult,
    normalize_carrier_name,
    normalize_number_type,
)
from app.providers.carrier_lookup.factory import build_carrier_lookup_provider
from app.providers.telephony.telnyx import normalize_french_number
from app.repositories.business_profile_repository import BusinessProfileRepository
from app.repositories.user_repository import UserRepository


class CarrierLookupUnavailableError(Exception):
    pass


class CarrierLookupService:
    def __init__(
        self,
        session: AsyncSession | None = None,
        *,
        provider: CarrierLookupProvider | None = None,
    ) -> None:
        self.session = session
        self.provider = provider or build_carrier_lookup_provider()

    async def lookup_number(self, e164: str) -> CarrierLookupResult:
        try:
            requested_number = normalize_french_number(e164)
            provider_result = await self.provider.lookup(requested_number)
            result_number = normalize_french_number(
                provider_result.normalized_number
            )
        except CarrierLookupError:
            raise
        except (TypeError, ValueError):
            raise CarrierLookupError("terminal") from None

        if (
            provider_result.country_code != "FR"
            or result_number != requested_number
        ):
            raise CarrierLookupError("terminal")

        carrier_name = self._safe_carrier_name(provider_result.carrier_name)
        return CarrierLookupResult(
            normalized_number=result_number,
            country_code="FR",
            carrier_name=carrier_name,
            normalized_carrier=normalize_carrier_name(carrier_name),
            number_type=normalize_number_type(provider_result.number_type),
            looked_up_at=self._safe_timestamp(provider_result.looked_up_at),
        )

    async def lookup_for_user(self, user_id: UUID) -> CarrierLookupResult:
        session = self._require_session()
        user_repository = UserRepository(session)
        profile_repository = BusinessProfileRepository(session)

        try:
            user = await user_repository.get_by_id_for_update(user_id)
            profile = await profile_repository.get_by_user_id_for_update(user_id)
            if (
                user is None
                or profile is None
                or profile.existing_phone_e164 is None
            ):
                raise CarrierLookupUnavailableError
            expected_number = profile.existing_phone_e164
        finally:
            # Release the read locks before invoking the external provider.
            await session.rollback()

        try:
            result = await self.lookup_number(expected_number)
        except CarrierLookupError:
            await self._record_failed_lookup(user_id, expected_number)
            raise CarrierLookupUnavailableError from None

        try:
            user = await user_repository.get_by_id_for_update(user_id)
            profile = await profile_repository.get_by_user_id_for_update(user_id)
            if (
                user is None
                or profile is None
                or profile.existing_phone_e164 != expected_number
            ):
                raise CarrierLookupUnavailableError
            profile.detected_carrier = result.normalized_carrier
            profile.detected_number_type = result.number_type
            profile.carrier_lookup_status = "succeeded"
            profile.carrier_looked_up_at = result.looked_up_at
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        return result

    async def _record_failed_lookup(self, user_id: UUID, expected_number: str) -> None:
        session = self._require_session()
        user_repository = UserRepository(session)
        profile_repository = BusinessProfileRepository(session)
        try:
            user = await user_repository.get_by_id_for_update(user_id)
            profile = await profile_repository.get_by_user_id_for_update(user_id)
            if (
                user is None
                or profile is None
                or profile.existing_phone_e164 != expected_number
            ):
                await session.rollback()
                return
            profile.detected_carrier = None
            profile.detected_number_type = None
            profile.carrier_lookup_status = "failed"
            profile.carrier_looked_up_at = datetime.now(UTC)
            await session.commit()
        except Exception:
            await session.rollback()
            raise

    def _require_session(self) -> AsyncSession:
        if self.session is None:
            raise RuntimeError("A database session is required")
        return self.session

    @staticmethod
    def _safe_carrier_name(value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized[:100] or None

    @staticmethod
    def _safe_timestamp(value: datetime) -> datetime:
        if value.tzinfo is None:
            return datetime.now(UTC)
        return value.astimezone(UTC)
