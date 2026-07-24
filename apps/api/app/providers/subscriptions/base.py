from abc import ABC, abstractmethod
from typing import Literal


ProviderErrorCategory = Literal["provider_retryable", "provider_terminal"]
ProviderErrorClass = Literal[
    "timeout",
    "rate_limited",
    "unavailable",
    "authentication",
    "validation",
    "conflict",
    "unknown",
]
SAFE_PROVIDER_ERROR_CATEGORIES = frozenset({"provider_retryable", "provider_terminal"})
SAFE_PROVIDER_ERROR_CLASSES = frozenset(
    {
        "timeout",
        "rate_limited",
        "unavailable",
        "authentication",
        "validation",
        "conflict",
        "unknown",
    }
)


class SubscriptionProviderError(RuntimeError):
    def __init__(
        self,
        category: ProviderErrorCategory,
        *,
        error_class: ProviderErrorClass,
    ) -> None:
        if category not in SAFE_PROVIDER_ERROR_CATEGORIES:
            raise ValueError("Unsafe subscription provider category")
        if error_class not in SAFE_PROVIDER_ERROR_CLASSES:
            raise ValueError("Unsafe subscription provider error class")
        super().__init__(error_class)
        self.category = category
        self.error_class = error_class
        self.retryable = category == "provider_retryable"


class SubscriptionProvider(ABC):
    @abstractmethod
    async def cancel_immediately(self, subscription_id: str) -> None:
        raise NotImplementedError
