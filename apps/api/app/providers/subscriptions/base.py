from abc import ABC, abstractmethod


class SubscriptionProvider(ABC):
    @abstractmethod
    async def cancel_immediately(self, subscription_id: str) -> None:
        raise NotImplementedError
