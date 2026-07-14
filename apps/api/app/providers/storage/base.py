from dataclasses import dataclass


class StorageProviderError(RuntimeError):
    def __init__(self, category: str) -> None:
        if category not in {"provider_retryable", "provider_terminal"}:
            raise ValueError("Unsafe storage provider category")
        super().__init__(category)
        self.category = category
        self.retryable = category == "provider_retryable"


@dataclass(frozen=True)
class StoredObject:
    object_key: str
    url: str


class StorageProvider:
    async def upload_bytes(self, *, object_key: str, data: bytes, content_type: str) -> StoredObject:
        raise NotImplementedError

    async def get_download_url(self, *, object_key: str) -> str | None:
        raise NotImplementedError
