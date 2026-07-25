from abc import ABC, abstractmethod

from app.core.observability import validated_error_class


SAFE_PROVIDER_CATEGORIES = frozenset({"provider_retryable", "provider_terminal"})


class TelephonyProviderError(RuntimeError):
    def __init__(
        self,
        category: str,
        *,
        error_class: str | None = None,
    ) -> None:
        if category not in SAFE_PROVIDER_CATEGORIES:
            raise ValueError("Unsafe telephony provider category")
        super().__init__(category)
        self.category = category
        self.retryable = category == "provider_retryable"
        self.error_class = validated_error_class(
            error_class or ("unavailable" if self.retryable else "unknown")
        )


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

    @abstractmethod
    async def release_number(self, *, provider_number_id: str) -> None:
        raise NotImplementedError
