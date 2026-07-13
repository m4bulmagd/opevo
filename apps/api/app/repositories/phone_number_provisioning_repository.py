from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.phone_number_provisioning import PhoneNumberProvisioning


class PhoneNumberProvisioningRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_user_id(self, user_id) -> PhoneNumberProvisioning | None:
        result = await self.session.execute(
            select(PhoneNumberProvisioning).where(PhoneNumberProvisioning.user_id == user_id)
        )
        return result.scalar_one_or_none()

    async def get_by_user_id_for_update(
        self,
        user_id,
    ) -> PhoneNumberProvisioning | None:
        result = await self.session.execute(
            select(PhoneNumberProvisioning)
            .where(PhoneNumberProvisioning.user_id == user_id)
            .with_for_update()
        )
        return result.scalar_one_or_none()

    async def mark_running(
        self,
        *,
        user_id,
        target_country_code: str,
        operation_key: str | None = None,
    ) -> PhoneNumberProvisioning:
        provisioning = await self.get_by_user_id_for_update(user_id)
        if provisioning is None:
            provisioning = PhoneNumberProvisioning(
                user_id=user_id,
                target_country_code=target_country_code,
                attempt_count=1,
                status="running",
                can_retry=False,
                last_error_reason=None,
                last_error_payload=None,
                provider_operation_key=operation_key,
            )
            self.session.add(provisioning)
        else:
            provisioning.target_country_code = target_country_code
            provisioning.attempt_count += 1
            provisioning.status = "running"
            provisioning.can_retry = False
            provisioning.last_error_reason = None
            provisioning.last_error_payload = None
            provisioning.phone_number_id = None
            if provisioning.provider_operation_key is None:
                provisioning.provider_operation_key = operation_key

        await self.session.flush()
        return provisioning

    async def mark_succeeded(self, *, user_id, phone_number_id, target_country_code: str) -> PhoneNumberProvisioning:
        provisioning = await self.get_by_user_id(user_id)
        if provisioning is None:
            provisioning = PhoneNumberProvisioning(
                user_id=user_id,
                target_country_code=target_country_code,
                attempt_count=1,
            )
            self.session.add(provisioning)
        provisioning.status = "succeeded"
        provisioning.can_retry = False
        provisioning.phone_number_id = phone_number_id
        provisioning.last_error_reason = None
        provisioning.last_error_payload = None
        await self.session.flush()
        return provisioning

    async def mark_failed(
        self,
        *,
        user_id,
        target_country_code: str,
        reason: str,
        payload: dict | None,
        can_retry: bool,
    ) -> PhoneNumberProvisioning:
        provisioning = await self.get_by_user_id(user_id)
        if provisioning is None:
            provisioning = PhoneNumberProvisioning(
                user_id=user_id,
                target_country_code=target_country_code,
                attempt_count=1,
            )
            self.session.add(provisioning)
        provisioning.target_country_code = target_country_code
        provisioning.status = "failed"
        provisioning.can_retry = can_retry
        provisioning.last_error_reason = reason
        provisioning.last_error_payload = payload
        provisioning.phone_number_id = None
        await self.session.flush()
        return provisioning
