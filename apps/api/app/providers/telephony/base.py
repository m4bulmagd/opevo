from abc import ABC, abstractmethod


SAFE_PROVIDER_CATEGORIES = frozenset({"provider_retryable", "provider_terminal"})


class TelephonyProviderError(RuntimeError):
    def __init__(self, category: str) -> None:
        if category not in SAFE_PROVIDER_CATEGORIES:
            raise ValueError("Unsafe telephony provider category")
        super().__init__(category)
        self.category = category
        self.retryable = category == "provider_retryable"


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

    @abstractmethod
    async def enable_number(self, *, provider_number_id: str) -> str:
        raise NotImplementedError

    @abstractmethod
    async def disable_number(self, *, provider_number_id: str) -> str:
        raise NotImplementedError
