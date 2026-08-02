import hashlib
import re
from typing import NoReturn

from app.core.provider_failures import ProviderFailure
from app.providers.telephony.base import TelephonyProvider


_FAKE_PROVIDER_ID = re.compile(r"fake-[0-9a-f]{16}")


class FakeTelephonyProvider(TelephonyProvider):
    async def provision_number(
        self,
        *,
        country_code: str,
        operation_key: str | None = None,
    ) -> dict:
        if country_code != "FR" or not isinstance(operation_key, str):
            self._raise_validation_error("provision_number")
        normalized_operation_key = operation_key.strip()
        if not normalized_operation_key:
            self._raise_validation_error("provision_number")

        digest = hashlib.sha256(normalized_operation_key.encode("utf-8")).hexdigest()
        digits = str(int(digest[:12], 16)).zfill(10)[-8:]
        return {
            "e164": f"+339{digits}",
            "provider_number_id": f"fake-{digest[:16]}",
            "provider_connection_name": "app-disabled",
        }

    async def recover_provisioned_number(
        self,
        *,
        country_code: str,
        operation_key: str,
    ) -> dict | None:
        return await self.provision_number(
            country_code=country_code,
            operation_key=operation_key,
        )

    async def enable_number(self, *, provider_number_id: str) -> str:
        self._require_fake_id(provider_number_id, operation="enable_number")
        return "app-active"

    async def disable_number(self, *, provider_number_id: str) -> str:
        self._require_fake_id(provider_number_id, operation="disable_number")
        return "app-disabled"

    async def release_number(self, *, provider_number_id: str) -> None:
        self._require_fake_id(provider_number_id, operation="release_number")

    @staticmethod
    def _require_fake_id(provider_number_id: str, *, operation: str) -> None:
        if not isinstance(provider_number_id, str) or _FAKE_PROVIDER_ID.fullmatch(
            provider_number_id
        ) is None:
            FakeTelephonyProvider._raise_validation_error(operation)

    @staticmethod
    def _raise_validation_error(_operation: str) -> NoReturn:
        raise ProviderFailure(
            provider="fake",
            operation="validate",
            disposition="terminal",
            error_class="validation",
        ) from None
