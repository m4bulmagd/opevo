from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.business_profile import BusinessProfile
from app.models.customer_activation import CustomerActivation
from app.repositories.agent_config_repository import AgentConfigRepository
from app.repositories.business_profile_repository import BusinessProfileRepository
from app.repositories.customer_activation_repository import (
    CustomerActivationRepository,
)
from app.repositories.user_repository import UserRepository
from app.schemas.business_profile import BusinessProfileDraft
from app.services.receptionist_projection_service import (
    ReceptionistProjectionService,
)


ROUTING_FIELDS = ("existing_phone_e164", "confirmed_carrier")
REQUIRED_PROFILE_FIELDS = (
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


class BusinessProfileNotFoundError(Exception):
    pass


class BusinessProfileIncompleteError(Exception):
    def __init__(self, fields: tuple[str, ...]) -> None:
        super().__init__("Business profile is incomplete")
        self.fields = fields


class BusinessProfileService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.user_repository = UserRepository(session)
        self.profile_repository = BusinessProfileRepository(session)
        self.activation_repository = CustomerActivationRepository(session)
        self.agent_config_repository = AgentConfigRepository(session)
        self.projection_service = ReceptionistProjectionService()

    async def save_draft(
        self,
        user_id: UUID,
        draft: BusinessProfileDraft,
    ) -> BusinessProfile:
        try:
            user = await self.user_repository.get_by_id_for_update(user_id)
            if user is None:
                raise BusinessProfileNotFoundError
            profile = await self.profile_repository.get_or_create_for_update(user_id)
            activation = await self.activation_repository.get_or_create_for_update(
                user_id
            )
            updates = draft.to_storage_dict()
            replacing_phone = bool(
                profile.existing_phone_e164 is not None
                and profile.existing_phone_e164 != updates["existing_phone_e164"]
            )
            if replacing_phone:
                updates |= {
                    "detected_carrier": None,
                    "detected_number_type": None,
                    "carrier_lookup_status": None,
                    "carrier_looked_up_at": None,
                    "confirmed_carrier": None,
                }

            changed = {
                name
                for name, value in updates.items()
                if getattr(profile, name) != value
            }
            routing_changed = bool(changed & set(ROUTING_FIELDS))
            for name, value in updates.items():
                setattr(profile, name, value)
            if changed:
                profile.content_revision += 1
            if routing_changed:
                profile.routing_revision += 1
                self._invalidate_routing_state(activation)

            config = (
                await self.agent_config_repository.get_or_create_default_for_update(
                    user_id
                )
            )
            self.projection_service.project(profile, config)
            await self.session.flush()
            await self.session.commit()
        except Exception:
            await self.session.rollback()
            raise
        await self.session.refresh(profile)
        return profile

    async def confirm_profile(self, user_id: UUID) -> CustomerActivation:
        user = await self.user_repository.get_by_id_for_update(user_id)
        if user is None:
            raise BusinessProfileNotFoundError
        profile = await self.profile_repository.get_or_create_for_update(user_id)
        activation = await self.activation_repository.get_or_create_for_update(user_id)
        missing = tuple(
            field
            for field in REQUIRED_PROFILE_FIELDS
            if self._is_missing_required_value(getattr(profile, field))
        )
        if missing:
            raise BusinessProfileIncompleteError(missing)

        activation.profile_confirmed_revision = profile.content_revision
        if activation.profile_confirmed_at is None:
            activation.profile_confirmed_at = datetime.now(UTC)
        user.country_code = "FR"
        await self.session.commit()
        await self.session.refresh(activation)
        return activation

    @staticmethod
    def _is_missing_required_value(value: object) -> bool:
        return not value or isinstance(value, str) and not value.strip()

    @staticmethod
    def _invalidate_routing_state(activation: CustomerActivation) -> None:
        activation.verification_window_started_at = None
        activation.verification_window_expires_at = None
        activation.verification_session_id = None
        activation.verification_claimed_at = None
        activation.verification_dispatch_id = None
        activation.verification_routing_fingerprint = None
        activation.verification_status = "invalidated"
        activation.verified_routing_fingerprint = None
        activation.forwarding_verified_at = None
        activation.go_live_requested_at = None
        activation.go_live_approved_at = None
        activation.activated_at = None
