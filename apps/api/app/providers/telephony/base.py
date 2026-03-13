from abc import ABC, abstractmethod


class TelephonyProvider(ABC):
    @abstractmethod
    async def provision_number(self, *, country_code: str) -> dict:
        raise NotImplementedError

    @abstractmethod
    async def enable_number(self, *, provider_number_id: str) -> str:
        raise NotImplementedError

    @abstractmethod
    async def disable_number(self, *, provider_number_id: str) -> str:
        raise NotImplementedError
