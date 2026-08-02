from abc import ABC, abstractmethod

class TelephonyProvisioningReviewRequired(Exception):
    def __init__(self, *, reason: str, payload: dict) -> None:
        super().__init__(reason)
        self.reason = reason
        self.payload = payload


class TelephonyProvisioningPending(Exception):
    def __init__(self, *, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class TelephonyProvider(ABC):
    @abstractmethod
    async def provision_number(
        self,
        *,
        country_code: str,
        operation_key: str | None = None,
    ) -> dict:
        raise NotImplementedError

    async def recover_provisioned_number(
        self,
        *,
        country_code: str,
        operation_key: str,
    ) -> dict | None:
        """Look up a previously accepted provisioning operation without ordering."""
        del country_code, operation_key
        return None

    @abstractmethod
    async def enable_number(self, *, provider_number_id: str) -> str:
        raise NotImplementedError

    @abstractmethod
    async def disable_number(self, *, provider_number_id: str) -> str:
        raise NotImplementedError

    @abstractmethod
    async def release_number(self, *, provider_number_id: str) -> None:
        raise NotImplementedError
