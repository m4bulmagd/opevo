import hashlib
import re
from typing import NoReturn

from app.providers.telephony.base import TelephonyProvider, TelephonyProviderError


_FAKE_PROVIDER_ID = re.compile(r"fake-[0-9a-f]{16}")


class FakeTelephonyProvider(TelephonyProvider):
    async def provision_number(
        self,
        *,
        country_code: str,
        operation_key: str | None = None,
    ) -> dict:
        if country_code != "FR" or not isinstance(operation_key, str):
            self._raise_validation_error()
        normalized_operation_key = operation_key.strip()
        if not normalized_operation_key:
            self._raise_validation_error()

        digest = hashlib.sha256(normalized_operation_key.encode("utf-8")).hexdigest()
        digits = str(int(digest[:12], 16)).zfill(10)[-8:]
        return {
            "e164": f"+339{digits}",
            "provider_number_id": f"fake-{digest[:16]}",
            "provider_connection_name": "app-disabled",
        }

    async def enable_number(self, *, provider_number_id: str) -> str:
        self._require_fake_id(provider_number_id)
        return "app-active"

    async def disable_number(self, *, provider_number_id: str) -> str:
        self._require_fake_id(provider_number_id)
        return "app-disabled"

    @staticmethod
    def _require_fake_id(provider_number_id: str) -> None:
        if not isinstance(provider_number_id, str) or _FAKE_PROVIDER_ID.fullmatch(
            provider_number_id
        ) is None:
            FakeTelephonyProvider._raise_validation_error()

    @staticmethod
    def _raise_validation_error() -> NoReturn:
        raise TelephonyProviderError(
            "provider_terminal",
            error_class="validation",
        ) from None
