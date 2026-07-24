from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import phonenumbers
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.business_profile import BusinessProfile
from app.models.customer_activation import CustomerActivation
from app.models.user import User
from app.repositories.activation_event_repository import ActivationEventRepository
from app.repositories.business_profile_repository import BusinessProfileRepository
from app.repositories.customer_activation_repository import (
    CustomerActivationRepository,
)
from app.repositories.phone_number_provisioning_repository import (
    PhoneNumberProvisioningRepository,
)
from app.repositories.phone_number_repository import PhoneNumberRepository
from app.repositories.user_repository import UserRepository
from app.services.business_profile_service import REQUIRED_PROFILE_FIELDS
from app.services.account_access_policy import require_active_account
from app.services.routing_fingerprint import routing_fingerprint


WINDOW_DURATION = timedelta(minutes=10)
COMPLETION_GRACE = timedelta(minutes=2)
DEFAULT_EXPIRY_BATCH_SIZE = 100


def as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


class ForwardingVerificationConflictError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class ForwardingVerificationClaim:
    activation_id: UUID
    user_id: UUID
    lifecycle_generation: int
    session_id: str
    room_name: str


def build_expiry_user_claim_statement(*, now: datetime, limit: int):
    eligible = or_(
        and_(
            CustomerActivation.verification_status == "open",
            CustomerActivation.verification_window_expires_at <= now,
        ),
        and_(
            CustomerActivation.verification_status == "claimed",
            CustomerActivation.verification_window_expires_at
            <= now - COMPLETION_GRACE,
        ),
    )
    return (
        select(User.id)
        .join(
            CustomerActivation,
            CustomerActivation.user_id == User.id,
        )
        .where(eligible)
        .order_by(
            CustomerActivation.verification_window_expires_at,
            User.id,
        )
        .limit(limit)
        .with_for_update(of=User, skip_locked=True)
    )


class ForwardingVerificationService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        user_repository=None,
        activation_repository=None,
        business_profile_repository=None,
        phone_number_repository=None,
        provisioning_repository=None,
        activation_event_repository=None,
        now_provider: Callable[[], datetime] | None = None,
    ) -> None:
        self.session = session
        self.user_repository = user_repository or UserRepository(session)
        self.activation_repository = (
            activation_repository or CustomerActivationRepository(session)
        )
        self.business_profile_repository = (
            business_profile_repository or BusinessProfileRepository(session)
        )
        self.phone_number_repository = (
            phone_number_repository or PhoneNumberRepository(session)
        )
        self.provisioning_repository = (
            provisioning_repository or PhoneNumberProvisioningRepository(session)
        )
        self.activation_event_repository = (
            activation_event_repository or ActivationEventRepository(session)
        )
        self.now_provider = now_provider or (lambda: datetime.now(UTC))

    async def open_window(self, user_id: UUID) -> CustomerActivation:
        try:
            user = await self.user_repository.get_by_id_for_update(user_id)
            if user is None:
                raise ForwardingVerificationConflictError("profile_unavailable")
            require_active_account(user)

            activation = await self.activation_repository.get_by_user_id_for_update(
                user_id
            )
            profile = await self.business_profile_repository.get_by_user_id_for_update(
                user_id
            )
            self._require_current_confirmed_profile(
                activation=activation,
                profile=profile,
            )
            assert activation is not None
            assert profile is not None

            phone = await self.phone_number_repository.get_by_user_id_for_update(user_id)
            if phone is None or not self._present(phone.provider_number_id):
                raise ForwardingVerificationConflictError("phone_not_ready")
            provisioning = (
                await self.provisioning_repository.get_by_user_id_for_update(user_id)
            )
            if provisioning is None or provisioning.status != "succeeded":
                raise ForwardingVerificationConflictError(
                    "provisioning_not_succeeded"
                )
            if provisioning.phone_number_id != phone.id:
                raise ForwardingVerificationConflictError(
                    "provisioning_state_conflict"
                )

            now = as_utc(self.now_provider())
            if activation.verification_status in {"open", "claimed"}:
                if not self._is_expiry_eligible(activation, now):
                    raise ForwardingVerificationConflictError(
                        "verification_window_already_open"
                    )
                await self._mark_expired(activation)
            if (
                activation.verification_status == "succeeded"
                and activation.forwarding_verified_at is not None
                and activation.verified_routing_fingerprint
                == routing_fingerprint(profile, phone)
            ):
                raise ForwardingVerificationConflictError(
                    "verification_already_succeeded"
                )

            started_at = now
            activation.verification_window_started_at = started_at
            activation.verification_window_expires_at = started_at + WINDOW_DURATION
            activation.verification_session_id = None
            activation.verification_claimed_at = None
            activation.verification_dispatch_id = None
            activation.verification_routing_fingerprint = None
            activation.verified_routing_fingerprint = None
            activation.forwarding_verified_at = None
            activation.verification_status = "open"
            activation.last_failure_code = None
            await self.activation_event_repository.append(
                user_id=user_id,
                activation_id=activation.id,
                event_type="verification_window_opened",
                idempotency_key=self._window_event_key(
                    activation,
                    "opened",
                ),
                metadata={},
            )
            await self.session.commit()
            return activation
        except Exception:
            await self.session.rollback()
            raise

    async def claim(
        self,
        *,
        called_number: str,
        room_name: str,
    ) -> ForwardingVerificationClaim:
        try:
            claim = await self.claim_in_transaction(
                called_number=called_number,
                room_name=room_name,
            )
            await self.session.commit()
            return claim
        except Exception:
            await self.session.rollback()
            raise

    async def claim_in_transaction(
        self,
        *,
        called_number: str,
        room_name: str,
        diversion_number: str | None = None,
    ) -> ForwardingVerificationClaim:
        """Claim a window while leaving commit and rollback to the caller."""
        normalized_number = self._normalize_french_number(called_number)
        resolved_phone = await self.phone_number_repository.get_by_e164(
            normalized_number
        )
        if resolved_phone is None:
            raise ForwardingVerificationConflictError(
                "verification_window_not_found"
            )
        user_id = resolved_phone.user_id

        user = await self.user_repository.get_by_id_for_update(user_id)
        if user is None or user.status != "active":
            raise ForwardingVerificationConflictError(
                "verification_window_not_found"
            )
        activation = await self.activation_repository.get_by_user_id_for_update(
            user_id
        )
        if activation is None:
            raise ForwardingVerificationConflictError(
                "verification_window_not_found"
            )
        profile = await self.business_profile_repository.get_by_user_id_for_update(
            user_id
        )
        phone = await self.phone_number_repository.get_by_user_id_for_update(user_id)
        if (
            profile is None
            or phone is None
            or phone.e164 != normalized_number
            or not self._present(phone.provider_number_id)
        ):
            raise ForwardingVerificationConflictError(
                "verification_window_not_found"
            )
        existing_session_id = activation.verification_session_id
        if isinstance(existing_session_id, str):
            claim_event = (
                await self.activation_event_repository.get_by_idempotency_key(
                    "activation-event:verification-claim:"
                    f"{existing_session_id}"
                )
            )
            if (
                claim_event is not None
                and claim_event.event_type == "verification_window_claimed"
                and claim_event.activation_id == activation.id
                and claim_event.user_id == user_id
                and claim_event.event_metadata.get("room_name") == room_name
            ):
                return ForwardingVerificationClaim(
                    activation_id=activation.id,
                    user_id=user_id,
                    lifecycle_generation=user.lifecycle_generation,
                    session_id=existing_session_id,
                    room_name=room_name,
                )

        if diversion_number is not None:
            existing_number = profile.existing_phone_e164
            if not isinstance(diversion_number, str) or not isinstance(
                existing_number,
                str,
            ):
                raise ForwardingVerificationConflictError(
                    "verification_diversion_mismatch"
                )
            try:
                normalized_diversion = self._normalize_french_number(
                    diversion_number
                )
                normalized_existing_number = self._normalize_french_number(
                    existing_number
                )
            except ForwardingVerificationConflictError:
                raise ForwardingVerificationConflictError(
                    "verification_diversion_mismatch"
                ) from None
            if normalized_diversion != normalized_existing_number:
                raise ForwardingVerificationConflictError(
                    "verification_diversion_mismatch"
                )
        if activation.verification_status == "claimed":
            raise ForwardingVerificationConflictError(
                "verification_window_already_claimed"
            )
        if activation.verification_status != "open":
            raise ForwardingVerificationConflictError(
                "verification_window_not_open"
            )
        started_at = activation.verification_window_started_at
        expires_at = activation.verification_window_expires_at
        if started_at is None or expires_at is None:
            raise ForwardingVerificationConflictError(
                "verification_window_not_open"
            )
        now = as_utc(self.now_provider())
        if now < as_utc(started_at):
            raise ForwardingVerificationConflictError(
                "verification_window_not_open"
            )
        if now >= as_utc(expires_at):
            raise ForwardingVerificationConflictError(
                "verification_window_expired"
            )

        session_id = str(uuid4())
        fingerprint = routing_fingerprint(profile, phone)
        activation.verification_session_id = session_id
        activation.verification_claimed_at = now
        activation.verification_status = "claimed"
        activation.verification_routing_fingerprint = fingerprint
        await self.activation_event_repository.append(
            user_id=user_id,
            activation_id=activation.id,
            event_type="verification_window_claimed",
            idempotency_key=f"activation-event:verification-claim:{session_id}",
            metadata={"room_name": room_name},
        )
        await self.session.flush()
        return ForwardingVerificationClaim(
            activation_id=activation.id,
            user_id=user_id,
            lifecycle_generation=user.lifecycle_generation,
            session_id=session_id,
            room_name=room_name,
        )

    async def claim_for_user(
        self,
        user_id: UUID,
        *,
        room_name: str | None = None,
    ) -> ForwardingVerificationClaim:
        phone = await self.phone_number_repository.get_by_user_id(user_id)
        if phone is None:
            raise ForwardingVerificationConflictError(
                "verification_window_not_found"
            )
        if room_name is None:
            activation = await self.activation_repository.get_by_user_id(user_id)
            if (
                activation is None
                or activation.verification_window_started_at is None
                or activation.verification_window_expires_at is None
            ):
                raise ForwardingVerificationConflictError(
                    "verification_window_not_open"
                )
            window_epoch = int(
                as_utc(activation.verification_window_started_at).timestamp()
            )
            room_name = (
                f"local-verification-{activation.id}-{window_epoch}"
            )
        return await self.claim(called_number=phone.e164, room_name=room_name)

    async def complete(self, *, session_id: str) -> CustomerActivation:
        try:
            resolved_activation = (
                await self.activation_repository.get_by_verification_session_id(
                    session_id
                )
            )
            if resolved_activation is None:
                raise ForwardingVerificationConflictError(
                    "verification_session_not_found"
                )
            user_id = resolved_activation.user_id
            user = await self.user_repository.get_by_id_for_update(user_id)
            if user is None or user.status != "active":
                raise ForwardingVerificationConflictError(
                    "verification_session_not_found"
                )
            activation = await self.activation_repository.get_by_user_id_for_update(
                user_id
            )
            if activation is None or activation.verification_session_id != session_id:
                raise ForwardingVerificationConflictError(
                    "verification_session_not_found"
                )
            if activation.verification_status == "succeeded":
                await self.session.commit()
                return activation
            if activation.verification_status != "claimed":
                raise ForwardingVerificationConflictError(
                    "verification_session_not_claimed"
                )

            expires_at = activation.verification_window_expires_at
            if expires_at is None:
                raise ForwardingVerificationConflictError(
                    "verification_session_not_claimed"
                )
            now = as_utc(self.now_provider())
            if now >= as_utc(expires_at) + COMPLETION_GRACE:
                raise ForwardingVerificationConflictError(
                    "verification_completion_expired"
                )

            profile = await self.business_profile_repository.get_by_user_id_for_update(
                user_id
            )
            phone = await self.phone_number_repository.get_by_user_id_for_update(user_id)
            if profile is None or phone is None:
                raise ForwardingVerificationConflictError(
                    "verification_routing_stale"
                )
            current_fingerprint = routing_fingerprint(profile, phone)
            claimed_fingerprint = activation.verification_routing_fingerprint
            if (
                claimed_fingerprint is None
                or claimed_fingerprint != current_fingerprint
            ):
                raise ForwardingVerificationConflictError(
                    "verification_routing_stale"
                )

            activation.verification_status = "succeeded"
            activation.verified_routing_fingerprint = claimed_fingerprint
            activation.forwarding_verified_at = now
            activation.last_failure_code = None
            await self.activation_event_repository.append(
                user_id=user_id,
                activation_id=activation.id,
                event_type="verification_window_succeeded",
                idempotency_key=(
                    f"activation-event:verification-success:{session_id}"
                ),
                metadata={},
            )
            await self.session.commit()
            return activation
        except Exception:
            await self.session.rollback()
            raise

    async def expire(self, user_id: UUID) -> CustomerActivation | None:
        try:
            user = await self.user_repository.get_by_id_for_update(user_id)
            if user is None:
                await self.session.commit()
                return None
            activation = await self.activation_repository.get_by_user_id_for_update(
                user_id
            )
            if activation is None or activation.verification_status == "expired":
                await self.session.commit()
                return activation
            if not self._is_expiry_eligible(activation, as_utc(self.now_provider())):
                await self.session.commit()
                return activation
            await self._mark_expired(activation)
            await self.session.commit()
            return activation
        except Exception:
            await self.session.rollback()
            raise

    async def expire_batch(
        self,
        *,
        limit: int = DEFAULT_EXPIRY_BATCH_SIZE,
    ) -> int:
        if limit < 1:
            raise ValueError("limit must be positive")
        now = as_utc(self.now_provider())
        try:
            user_ids = list(
                (
                    await self.session.scalars(
                        build_expiry_user_claim_statement(
                            now=now,
                            limit=limit,
                        )
                    )
                ).all()
            )
            if not user_ids:
                await self.session.commit()
                return 0

            activations = list(
                (
                    await self.session.scalars(
                        select(CustomerActivation)
                        .where(CustomerActivation.user_id.in_(user_ids))
                        .order_by(CustomerActivation.user_id)
                        .with_for_update()
                        .execution_options(populate_existing=True)
                    )
                ).all()
            )
            expired_count = 0
            for activation in activations:
                if not self._is_expiry_eligible(activation, now):
                    continue
                await self._mark_expired(activation)
                expired_count += 1
            await self.session.commit()
            return expired_count
        except Exception:
            await self.session.rollback()
            raise

    async def _mark_expired(self, activation: CustomerActivation) -> None:
        activation.verification_status = "expired"
        activation.last_failure_code = "verification_window_expired"
        await self.activation_event_repository.append(
            user_id=activation.user_id,
            activation_id=activation.id,
            event_type="verification_window_expired",
            idempotency_key=self._window_event_key(activation, "expired"),
            metadata={},
        )

    @staticmethod
    def _is_expiry_eligible(
        activation: CustomerActivation,
        now: datetime,
    ) -> bool:
        expires_at = activation.verification_window_expires_at
        if expires_at is None:
            return False
        deadline = as_utc(expires_at)
        if activation.verification_status == "claimed":
            deadline += COMPLETION_GRACE
        elif activation.verification_status != "open":
            return False
        return now >= deadline

    @staticmethod
    def _require_current_confirmed_profile(
        *,
        activation: CustomerActivation | None,
        profile: BusinessProfile | None,
    ) -> None:
        if (
            activation is None
            or activation.profile_confirmed_at is None
            or activation.profile_confirmed_revision is None
        ):
            raise ForwardingVerificationConflictError("profile_not_confirmed")
        if profile is None:
            raise ForwardingVerificationConflictError("profile_incomplete")
        if any(
            not ForwardingVerificationService._present(getattr(profile, field))
            for field in REQUIRED_PROFILE_FIELDS
        ):
            raise ForwardingVerificationConflictError("profile_incomplete")

    @staticmethod
    def _normalize_french_number(raw_number: str) -> str:
        try:
            parsed = phonenumbers.parse(raw_number, "FR")
        except phonenumbers.NumberParseException:
            raise ForwardingVerificationConflictError(
                "verification_called_number_invalid"
            ) from None
        if not phonenumbers.is_valid_number_for_region(parsed, "FR"):
            raise ForwardingVerificationConflictError(
                "verification_called_number_invalid"
            )
        return phonenumbers.format_number(
            parsed,
            phonenumbers.PhoneNumberFormat.E164,
        )

    @staticmethod
    def _window_event_key(
        activation: CustomerActivation,
        transition: str,
    ) -> str:
        started_at = activation.verification_window_started_at
        if started_at is None:
            raise RuntimeError("verification window identity is missing")
        timestamp = as_utc(started_at).isoformat()
        return (
            f"activation-event:verification-window:{activation.id}:"
            f"{timestamp}:{transition}"
        )

    @staticmethod
    def _present(value: object) -> bool:
        return bool(value and (not isinstance(value, str) or value.strip()))
